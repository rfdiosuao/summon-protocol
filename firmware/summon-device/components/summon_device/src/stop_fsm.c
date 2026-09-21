/**
 * @file stop_fsm.c
 * @brief 停止状态机：清队列 -> 等 Port 确认停稳 -> 才允许发 device.stopped。
 *
 * 关键约束：Port 的 stop_all 必须回调表示真的停下了，框架才认为停止完成。
 * 因此无法用计时器伪造完成——这是接口形状强制的结果，不是约定。
 */

#include "summon_device.h"
#include "summon_internal.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

static const char *TAG = "summon_stop";

/** stop_all 回调通过这个事件组把结果交回状态机。 */
#define SUMMON_EV_PORT_STOPPED BIT0

static EventGroupHandle_t s_stop_events;
static summon_action_status_t s_port_status;
static char s_port_detail[64];

static void port_stopped_cb(summon_action_status_t status, const char *detail, void *user)
{
    s_port_status = status;
    strlcpy(s_port_detail, detail != NULL ? detail : "", sizeof(s_port_detail));
    if (user != NULL) {
        xEventGroupSetBits((EventGroupHandle_t)user, SUMMON_EV_PORT_STOPPED);
    }
}

esp_err_t summon_stop_fsm_init(summon_device_ctx_t *ctx)
{
    ctx->stopping = false;
    ctx->stopped_confirmed = false;
    ctx->pending_len = 0;
    memset(ctx->pending, 0, sizeof(ctx->pending));
    s_stop_events = xEventGroupCreate();
    return s_stop_events != NULL ? ESP_OK : ESP_ERR_NO_MEM;
}

esp_err_t summon_stop_fsm_enqueue(summon_device_ctx_t *ctx, const char *command_id)
{
    if (ctx->stopping) {
        /* 正在停止：拒绝新动作入队 */
        return ESP_ERR_INVALID_STATE;
    }
    if (ctx->pending_len >= SUMMON_DEDUP_CAPACITY) {
        return ESP_ERR_NO_MEM;
    }
    strlcpy(ctx->pending[ctx->pending_len], command_id,
            sizeof(ctx->pending[ctx->pending_len]));
    ctx->pending_len++;
    return ESP_OK;
}

size_t summon_stop_fsm_clear(summon_device_ctx_t *ctx)
{
    size_t cleared = ctx->pending_len;
    ctx->pending_len = 0;
    memset(ctx->pending, 0, sizeof(ctx->pending));
    return cleared;
}

esp_err_t summon_stop_fsm_stop(summon_device_ctx_t *ctx)
{
    if (ctx->stopping) {
        return ESP_OK;
    }
    ctx->stopping = true;

    /* 1) 先清空待执行队列，不再接受新动作 */
    size_t cleared = summon_stop_fsm_clear(ctx);
    ESP_LOGI(TAG, "cleared %u pending commands", (unsigned)cleared);

    /* 2) 让 Port 真的停下来，并等它确认 */
    if (ctx->port->stop_all == NULL) {
        ESP_LOGE(TAG, "port has no stop_all; 不上报已停止");
        return ESP_ERR_INVALID_STATE;
    }
    xEventGroupClearBits(s_stop_events, SUMMON_EV_PORT_STOPPED);
    ctx->port->stop_all(port_stopped_cb, (void *)s_stop_events);

    EventBits_t bits = xEventGroupWaitBits(
        s_stop_events, SUMMON_EV_PORT_STOPPED, pdFALSE, pdTRUE, pdMS_TO_TICKS(10000));
    if ((bits & SUMMON_EV_PORT_STOPPED) == 0) {
        ESP_LOGW(TAG, "port stop confirmation timed out; 不上报已停止");
        return ESP_ERR_TIMEOUT;
    }
    if (s_port_status != SUMMON_COMPLETED) {
        ESP_LOGW(TAG, "port reported stop status %d (%s); 不上报已停止",
                 (int)s_port_status, s_port_detail);
        return ESP_ERR_INVALID_STATE;
    }

    /* 3) 只有确认停稳，才置位；summon_device_stop() 据此才允许发 device.stopped */
    ctx->stopped_confirmed = true;
    summon_device_notify_stopped();
    return ESP_OK;
}
