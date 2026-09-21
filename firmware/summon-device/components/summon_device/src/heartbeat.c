/**
 * @file heartbeat.c
 * @brief 心跳：每秒发 device.heartbeat，3 秒收不到 Gateway 心跳即本地停止。
 */

#include "summon_device.h"
#include "summon_internal.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"

static const char *TAG = "summon_hb";

esp_err_t summon_heartbeat_init(summon_device_ctx_t *ctx)
{
    ctx->last_gateway_heartbeat_ms = esp_timer_get_time() / 1000;
    return ESP_OK;
}

bool summon_heartbeat_overdue(summon_device_ctx_t *ctx)
{
    int64_t now_ms = esp_timer_get_time() / 1000;
    return (now_ms - ctx->last_gateway_heartbeat_ms) > SUMMON_HEARTBEAT_TIMEOUT_MS;
}

void summon_heartbeat_note_gateway(summon_device_ctx_t *ctx)
{
    ctx->last_gateway_heartbeat_ms = esp_timer_get_time() / 1000;
}

void summon_heartbeat_task(void *arg)
{
    summon_device_ctx_t *ctx = (summon_device_ctx_t *)arg;
    for (;;) {
        if (!ctx->stopping) {
            summon_ws_send_heartbeat(ctx);
        }
        /* 3 秒收不到 Gateway 心跳：本地停止并清空待执行队列。
         * 断网不等于失控——壳必须能安全待机。 */
        if (summon_heartbeat_overdue(ctx) && !ctx->stopping) {
            ESP_LOGW(TAG, "gateway heartbeat overdue; 本地停止");
            summon_stop_fsm_stop(ctx);
        }
        vTaskDelay(pdMS_TO_TICKS(SUMMON_HEARTBEAT_INTERVAL_MS));
    }
}
