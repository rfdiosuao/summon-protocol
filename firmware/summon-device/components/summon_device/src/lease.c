/**
 * @file lease.c
 * @brief 租约校验：旧 epoch 与过期动作一律拒绝，不运动。
 */

#include "summon_device.h"
#include "summon_internal.h"

#include <time.h>

#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "summon_lease";

esp_err_t summon_lease_init(summon_device_ctx_t *ctx)
{
    ctx->lease_epoch = 0;
    ctx->lease_expires_at_ms = 0;
    return ESP_OK;
}

esp_err_t summon_lease_check_command(summon_device_ctx_t *ctx, uint32_t lease_epoch,
                                     int64_t expires_at_ms)
{
    int64_t now_ms = esp_timer_get_time() / 1000;
    if (lease_epoch < ctx->lease_epoch) {
        ESP_LOGW(TAG, "STALE_LEASE: %u < %u", (unsigned)lease_epoch, (unsigned)ctx->lease_epoch);
        return ESP_ERR_INVALID_STATE;
    }
    if (expires_at_ms <= now_ms) {
        ESP_LOGW(TAG, "EXPIRED: %lld <= %lld", (long long)expires_at_ms, (long long)now_ms);
        return ESP_ERR_TIMEOUT;
    }
    if ((expires_at_ms - now_ms) > (int64_t)SUMMON_LEASE_MAX_SECONDS * 1000) {
        ESP_LOGW(TAG, "lease exceeds hard cap of %d s", SUMMON_LEASE_MAX_SECONDS);
        return ESP_ERR_INVALID_ARG;
    }
    /* 更新到的 epoch 立即成为当前值；旧的从此不可执行 */
    if (lease_epoch > ctx->lease_epoch) {
        ctx->lease_epoch = lease_epoch;
        ctx->lease_expires_at_ms = expires_at_ms;
    }
    return ESP_OK;
}

/* RFC3339 UTC（形如 2026-09-23T06:00:05Z）转毫秒。非法输入返回 0。 */
int64_t summon_parse_rfc3339_ms(const char *text)
{
    if (text == NULL || strlen(text) < 20) {
        return 0;
    }
    struct tm tm_val;
    memset(&tm_val, 0, sizeof(tm_val));
    if (strptime(text, "%Y-%m-%dT%H:%M:%SZ", &tm_val) == NULL) {
        return 0;
    }
    time_t secs = mktime(&tm_val);
    if (secs == (time_t)-1) {
        return 0;
    }
    return (int64_t)secs * 1000;
}
