/**
 * @file summon_device.c
 * @brief 设备端生命周期与子模块调度点。
 *
 * 本文件只做三件事：初始化、启动、停止。所有协议行为都在子模块里，
 * 由本文件按固定顺序调度。移植者不应修改本文件。
 */

#include "summon_device.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

#include "esp_log.h"

#include "summon_internal.h"

static const char *TAG = "summon_device";

/** 启动各阶段的事件位。 */
#define SUMMON_EV_HELLO_OK   BIT0
#define SUMMON_EV_STOPPED    BIT1

static EventGroupHandle_t s_events;
static summon_device_ctx_t s_ctx;

esp_err_t summon_device_init(const summon_device_config_t *config)
{
    if (config == NULL || summon_port_get() == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (config->device_id == NULL || config->shell_id == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.config = *config;
    s_ctx.port = summon_port_get();

    /* 顺序固定：编解码 → 去重 → 租约 → 停止状态机 → 心跳 → WSS。
     * 任一步失败即整体失败，不带病启动。 */
    esp_err_t err = summon_codec_init(&s_ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "codec init failed");
        return err;
    }
    err = summon_dedup_init(&s_ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "dedup init failed");
        return err;
    }
    err = summon_lease_init(&s_ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "lease init failed");
        return err;
    }
    err = summon_stop_fsm_init(&s_ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "stop fsm init failed");
        return err;
    }
    err = summon_heartbeat_init(&s_ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "heartbeat init failed");
        return err;
    }
    err = summon_ws_init(&s_ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ws init failed");
        return err;
    }

    s_events = xEventGroupCreate();
    if (s_events == NULL) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t summon_device_start(void)
{
    if (s_events == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    /* hello 必须由本框架发出，Port 不参与握手。 */
    esp_err_t err = summon_ws_send_hello(&s_ctx);
    if (err != ESP_OK) {
        return err;
    }
    /* 心跳与读循环各自独立任务；停止由 stop_fsm 统一收口。 */
    if (xTaskCreate(summon_heartbeat_task, "summon_hb", 3072, &s_ctx, 5, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(summon_ws_reader_task, "summon_rx", 4096, &s_ctx, 5, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t summon_device_stop(void)
{
    if (s_events == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    /* 等待 Port 确认停稳；框架据此才允许发 device.stopped。 */
    EventBits_t bits = xEventGroupWaitBits(
        s_events, SUMMON_EV_STOPPED, pdFALSE, pdTRUE, pdMS_TO_TICKS(10000));
    if ((bits & SUMMON_EV_STOPPED) == 0) {
        ESP_LOGW(TAG, "stop confirmation timed out; 不上报已停止");
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

void summon_device_notify_stopped(void)
{
    if (s_events != NULL) {
        xEventGroupSetBits(s_events, SUMMON_EV_STOPPED);
    }
}

void summon_device_notify_hello_ok(void)
{
    if (s_events != NULL) {
        xEventGroupSetBits(s_events, SUMMON_EV_HELLO_OK);
    }
}

/* ------------------------------------------------------------------ *
 * 协议分发：唯一的调度点。                                            *
 * 去重 -> 租约 -> 停止检查 -> 执行。顺序固定，不得调换：               *
 * 幂等命中必须先于 seq 与租约比较，但调用身份要先验证。                *
 * ------------------------------------------------------------------ */

esp_err_t summon_ws_send_hello(summon_device_ctx_t *ctx)
{
    char payload[192];
    snprintf(payload, sizeof(payload),
             "{\"device_id\":\"%s\",\"shell_id\":\"%s\"}",
             ctx->config->device_id, ctx->config->shell_id);
    uint8_t frame[SUMMON_MAX_FRAME_BYTES];
    size_t len = 0;
    esp_err_t err = summon_codec_encode(ctx, "device.hello", payload, frame, &len);
    if (err != ESP_OK) {
        return err;
    }
    err = summon_ws_send(ctx, frame, len);
    if (err == ESP_OK) {
        summon_device_notify_hello_ok();
    }
    return err;
}

esp_err_t summon_ws_send_heartbeat(summon_device_ctx_t *ctx)
{
    char payload[96];
    snprintf(payload, sizeof(payload), "{\"device_id\":\"%s\"}", ctx->config->device_id);
    uint8_t frame[SUMMON_MAX_FRAME_BYTES];
    size_t len = 0;
    esp_err_t err = summon_codec_encode(ctx, "device.heartbeat", payload, frame, &len);
    if (err != ESP_OK) {
        return err;
    }
    return summon_ws_send(ctx, frame, len);
}

static esp_err_t send_result(summon_device_ctx_t *ctx, const char *command_id,
                             summon_action_status_t status, const char *detail)
{
    char payload[256];
    snprintf(payload, sizeof(payload),
             "{\"device_id\":\"%s\",\"command_id\":\"%s\",\"status\":%d,\"detail\":\"%s\"}",
             ctx->config->device_id, command_id, (int)status,
             detail != NULL ? detail : "");
    uint8_t frame[SUMMON_MAX_FRAME_BYTES];
    size_t len = 0;
    esp_err_t err = summon_codec_encode(ctx, "device.result", payload, frame, &len);
    if (err != ESP_OK) {
        return err;
    }
    return summon_ws_send(ctx, frame, len);
}

static void action_done_cb(summon_action_status_t status, const char *detail, void *user)
{
    summon_device_ctx_t *ctx = (summon_device_ctx_t *)user;
    /* 只有真的做完才报 COMPLETED；不得用动画计时伪造完成。 */
    send_result(ctx, ctx->current_command_id, status, detail);
}

void summon_ws_on_frame(summon_device_ctx_t *ctx, const uint8_t *raw, size_t len)
{
    char type[32];
    char payload[SUMMON_MAX_FRAME_BYTES];
    esp_err_t err = summon_codec_decode(ctx, raw, len, type, payload, sizeof(payload));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "decode failed (%d); 拒绝该帧", err);
        return;
    }

    if (strcmp(type, "device.heartbeat") == 0) {
        summon_heartbeat_note_gateway(ctx);
        return;
    }

    if (strcmp(type, "device.stop") == 0) {
        /* 停止流程：清队列 -> 等 Port 确认 -> 才发 device.stopped */
        summon_stop_fsm_stop(ctx);
        return;
    }

    if (strcmp(type, "device.command") != 0) {
        ESP_LOGW(TAG, "unexpected type %s", type);
        return;
    }

    /* 从这里起按固定顺序校验；任一不满足即回报而不运动。 */
    summon_command_fields_t f;
    if (summon_parse_command_fields(payload, &f) != ESP_OK) {
        ESP_LOGE(TAG, "command fields missing");
        return;
    }

    /* 1) 幂等：同 ID 同内容返回已有状态，绝不再次运动 */
    char hash[65];
    summon_dedup_hash_payload(payload, hash, sizeof(hash));
    summon_action_status_t prior = SUMMON_FAILED;
    err = summon_dedup_lookup(ctx, f.command_id, hash, &prior);
    if (err == ESP_OK) {
        send_result(ctx, f.command_id, prior, "dedup hit");
        return;
    }
    if (err != ESP_ERR_NOT_FOUND) {
        send_result(ctx, f.command_id, SUMMON_FAILED, "IDEMPOTENCY_CONFLICT");
        return;
    }

    /* 2) 租约与截止时间 */
    err = summon_lease_check_command(ctx, f.lease_epoch, f.expires_at_ms);
    if (err == ESP_ERR_INVALID_STATE) {
        send_result(ctx, f.command_id, SUMMON_FAILED, "STALE_LEASE");
        return;
    }
    if (err != ESP_ERR_TIMEOUT) {
        /* fallthrough: 其他错误也按拒绝处理 */
    }
    if (err != ESP_OK) {
        send_result(ctx, f.command_id, SUMMON_FAILED, "EXPIRED");
        return;
    }

    /* 3) 正在停止：拒绝新动作 */
    if (ctx->stopping) {
        send_result(ctx, f.command_id, SUMMON_FAILED, "STOPPING");
        return;
    }

    /* 4) 记录后执行；未确认的命令在停止/重连后报 UNKNOWN，不自动重放 */
    summon_dedup_remember(ctx, f.command_id, hash, SUMMON_UNKNOWN);
    if (summon_stop_fsm_enqueue(ctx, f.command_id) != ESP_OK) {
        send_result(ctx, f.command_id, SUMMON_FAILED, "QUEUE_FULL");
        return;
    }
    strlcpy(ctx->current_command_id, f.command_id, sizeof(ctx->current_command_id));

    const summon_port_t *port = ctx->port;
    if (strcmp(f.capability, "display.text") == 0 && port->display_text != NULL) {
        port->display_text(f.text, strlen(f.text), action_done_cb, ctx);
    } else if (strcmp(f.capability, "speech.say") == 0 && port->audio_pcm != NULL) {
        port->audio_pcm(f.pcm, f.pcm_len, f.hz, f.bits, f.ch, action_done_cb, ctx);
    } else if (strcmp(f.capability, "arm.gesture") == 0 && port->gesture != NULL) {
        port->gesture(f.gesture_name, f.repeat, action_done_cb, ctx);
    } else {
        send_result(ctx, f.command_id, SUMMON_FAILED, "CAPABILITY_UNSUPPORTED");
    }
}
