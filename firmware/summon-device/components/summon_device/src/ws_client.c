/**
 * @file ws_client.c
 * @brief 本地 WSS /device 连接、TLS 认证与收发循环。
 *
 * 两条硬规则：
 *  1. token 放在 TLS 握手的 Authorization 头，**不放入 URL**——URL 会进日志。
 *  2. 无法正确校验证书时先用有线开发桥验证，**不得把跳过 TLS 校验作为部署默认**。
 */

#include "summon_device.h"
#include "summon_internal.h"

#include <string.h>

#include "esp_log.h"
#include "esp_websocket_client.h"
#include "freertos/task.h"

static const char *TAG = "summon_ws";

static esp_websocket_client_handle_t s_client;

static void ws_event_handler(void *handler_args, esp_event_base_t base,
                             int32_t event_id, void *event_data)
{
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;
    summon_device_ctx_t *ctx = (summon_device_ctx_t *)handler_args;

    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "connected to %s", ctx->config->gateway_url);
        ctx->connected = true;
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "disconnected");
        ctx->connected = false;
        break;
    case WEBSOCKET_EVENT_DATA:
        if (data->op_code == 0x08) {           /* close */
            break;
        }
        if (data->data_len > SUMMON_MAX_FRAME_BYTES) {
            ESP_LOGE(TAG, "frame %d exceeds %d; 拒绝", data->data_len, SUMMON_MAX_FRAME_BYTES);
            break;
        }
        /* 交回框架处理，不在本回调里做解码/执行 */
        summon_ws_on_frame(ctx, (const uint8_t *)data->data_ptr, data->data_len);
        break;
    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGE(TAG, "ws error");
        break;
    default:
        break;
    }
}

esp_err_t summon_ws_init(summon_device_ctx_t *ctx)
{
    const esp_websocket_client_config_t cfg = {
        .uri = ctx->config->gateway_url,
        .buffer_size = SUMMON_MAX_FRAME_BYTES,
        .task_stack = 8192,
        .skip_cert_common_name_check = false,   /* 不跳过校验 */
        /* 证书由部署者配置并随固件提供；不要烧录 Hub 管理密钥。 */
        .cert_pem = NULL,
        .headers = ctx->config->device_token,   /* Authorization 头，不进 URL */
    };
    s_client = esp_websocket_client_init(&cfg);
    if (s_client == NULL) {
        return ESP_FAIL;
    }
    return esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY,
                                         ws_event_handler, ctx);
}

esp_err_t summon_ws_send(summon_device_ctx_t *ctx, const uint8_t *data, size_t len)
{
    if (!ctx->connected || s_client == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (esp_websocket_client_is_healthy(s_client) != true) {
        return ESP_ERR_INVALID_STATE;
    }
    return esp_websocket_client_send_bin(s_client, (const char *)data, (int)len,
                                         portMAX_DELAY);
}

void summon_ws_reader_task(void *arg)
{
    /* esp_websocket_client 自带事件循环，这里只负责保持任务存活并
     * 在断开时按指数退避重连。重连后重新握手，不恢复旧执行权。 */
    summon_device_ctx_t *ctx = (summon_device_ctx_t *)arg;
    int backoff_ms = 1000;
    for (;;) {
        if (!ctx->connected) {
            ESP_LOGW(TAG, "reconnecting in %d ms", backoff_ms);
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            if (backoff_ms < 30000) {
                backoff_ms *= 2;
            }
            esp_websocket_client_start(s_client);
        } else {
            backoff_ms = 1000;
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}
