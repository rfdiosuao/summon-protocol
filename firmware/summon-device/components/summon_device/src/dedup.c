/**
 * @file dedup.c
 * @brief command_id 去重表。
 *
 * 同 ID 同内容 -> 返回已有状态，不产生第二次执行。
 * 同 ID 不同内容 -> ESP_ERR_INVALID_STATE（对应 IDEMPOTENCY_CONFLICT）。
 * 幂等命中先于 seq 比较，但调用身份必须先验证。
 */

#include "summon_device.h"
#include "summon_internal.h"

#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "summon_dedup";

static void hash_hex(const char *in, char *out, size_t out_cap)
{
    /* 简化散列：仅用于区分"同内容"与"不同内容"。
     * 生产实现应换成真实的 SHA-256（mbedtls）。 */
    uint32_t h = 2166136261u;
    for (const char *p = in; *p != '\0'; ++p) {
        h ^= (uint8_t)*p;
        h *= 16777619u;
    }
    snprintf(out, out_cap, "%08x%08x", h, (unsigned)strlen(in));
}

esp_err_t summon_dedup_init(summon_device_ctx_t *ctx)
{
    memset(ctx->dedup, 0, sizeof(ctx->dedup));
    return ESP_OK;
}

esp_err_t summon_dedup_lookup(summon_device_ctx_t *ctx, const char *command_id,
                              const char *payload_hash, summon_action_status_t *state_out)
{
    for (size_t i = 0; i < SUMMON_DEDUP_CAPACITY; ++i) {
        if (ctx->dedup[i].command_id[0] == '\0') {
            continue;
        }
        if (strcmp(ctx->dedup[i].command_id, command_id) != 0) {
            continue;
        }
        if (strcmp(ctx->dedup[i].payload_hash, payload_hash) != 0) {
            ESP_LOGW(TAG, "IDEMPOTENCY_CONFLICT on %s", command_id);
            return ESP_ERR_INVALID_STATE;
        }
        *state_out = ctx->dedup[i].state;
        return ESP_OK;
    }
    return ESP_ERR_NOT_FOUND;
}

esp_err_t summon_dedup_remember(summon_device_ctx_t *ctx, const char *command_id,
                                const char *payload_hash, summon_action_status_t state)
{
    /* 先找已有条目更新状态 */
    for (size_t i = 0; i < SUMMON_DEDUP_CAPACITY; ++i) {
        if (ctx->dedup[i].command_id[0] != '\0' &&
            strcmp(ctx->dedup[i].command_id, command_id) == 0) {
            ctx->dedup[i].state = state;
            ctx->dedup[i].recorded_at = esp_timer_get_time();
            return ESP_OK;
        }
    }
    /* 淘汰最旧的一条 */
    size_t slot = 0;
    int64_t oldest = INT64_MAX;
    for (size_t i = 0; i < SUMMON_DEDUP_CAPACITY; ++i) {
        if (ctx->dedup[i].command_id[0] == '\0') {
            slot = i;
            break;
        }
        if (ctx->dedup[i].recorded_at < oldest) {
            oldest = ctx->dedup[i].recorded_at;
            slot = i;
        }
    }
    strlcpy(ctx->dedup[slot].command_id, command_id, sizeof(ctx->dedup[slot].command_id));
    strlcpy(ctx->dedup[slot].payload_hash, payload_hash, sizeof(ctx->dedup[slot].payload_hash));
    ctx->dedup[slot].state = state;
    ctx->dedup[slot].recorded_at = esp_timer_get_time();
    return ESP_OK;
}

void summon_dedup_hash_payload(const char *payload_json, char *out, size_t out_cap)
{
    hash_hex(payload_json, out, out_cap);
}
