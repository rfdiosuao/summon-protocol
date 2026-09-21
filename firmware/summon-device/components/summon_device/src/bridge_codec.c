/**
 * @file bridge_codec.c
 * @brief BridgeMessage 编解码。
 *
 * 容量按 SUMMON_MAX_FRAME_BYTES 推导，不用小固定值去解析可能达到上限的帧。
 * 上游有一个真实死机案例：固定 4096 字节的 JSON 文档去解析实际约需
 * 6971 字节的响应。这里统一按上限分配，超限直接拒绝。
 */

#include "summon_device.h"
#include "summon_internal.h"

#include <string.h>

#include "esp_log.h"

static const char *TAG = "summon_codec";

static const char *const kTypes[] = {
    "device.hello", "device.heartbeat", "device.command",
    "device.result", "device.stop", "device.stopped",
};

esp_err_t summon_codec_init(summon_device_ctx_t *ctx)
{
    ctx->frame_buf_len = SUMMON_MAX_FRAME_BYTES;
    ctx->frame_buf = calloc(1, ctx->frame_buf_len);
    if (ctx->frame_buf == NULL) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

static bool is_known_type(const char *type)
{
    for (size_t i = 0; i < sizeof(kTypes) / sizeof(kTypes[0]); ++i) {
        if (strcmp(type, kTypes[i]) == 0) {
            return true;
        }
    }
    return false;
}

esp_err_t summon_codec_encode(summon_device_ctx_t *ctx, const char *type,
                              const char *payload_json, uint8_t *out, size_t *out_len)
{
    if (!is_known_type(type)) {
        ESP_LOGE(TAG, "unknown type %s", type);
        return ESP_ERR_INVALID_ARG;
    }
    int n = snprintf((char *)out, SUMMON_MAX_FRAME_BYTES,
                     "{\"v\":1,\"message_id\":\"m%lld\",\"sent_at\":\"%s\","
                     "\"type\":\"%s\",\"payload\":%s}",
                     (long long)esp_timer_get_time(), "1970-01-01T00:00:00Z",
                     type, payload_json);
    if (n <= 0 || (size_t)n >= SUMMON_MAX_FRAME_BYTES) {
        ESP_LOGE(TAG, "frame exceeds %d bytes", SUMMON_MAX_FRAME_BYTES);
        return ESP_ERR_INVALID_SIZE;
    }
    *out_len = (size_t)n;
    return ESP_OK;
}

esp_err_t summon_codec_decode(summon_device_ctx_t *ctx, const uint8_t *raw, size_t len,
                              char *type_out, char *payload_out, size_t payload_cap)
{
    if (len > SUMMON_MAX_FRAME_BYTES) {
        ESP_LOGE(TAG, "incoming frame %u exceeds %d", (unsigned)len, SUMMON_MAX_FRAME_BYTES);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(ctx->frame_buf, raw, len);
    ctx->frame_buf[len] = '\0';

    /* 反序列化失败后不得继续读取字段。 */
    const char *type = strstr((const char *)ctx->frame_buf, "\"type\":\"");
    if (type == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    type += 8;
    const char *type_end = strchr(type, '"');
    if (type_end == NULL || (size_t)(type_end - type) >= 32) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    char typebuf[32] = {0};
    memcpy(typebuf, type, (size_t)(type_end - type));
    if (!is_known_type(typebuf)) {
        ESP_LOGE(TAG, "reject unknown type %s", typebuf);
        return ESP_ERR_INVALID_RESPONSE;
    }

    const char *payload = strstr((const char *)ctx->frame_buf, "\"payload\":");
    if (payload == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    payload += 10;
    size_t remaining = strlen(payload);
    if (remaining >= payload_cap) {
        ESP_LOGE(TAG, "payload %u exceeds buffer %u", (unsigned)remaining, (unsigned)payload_cap);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(payload_out, payload, remaining + 1);
    strlcpy(type_out, typebuf, 32);
    return ESP_OK;
}

/* ------------------------------------------------------------------ *
 * command 字段解析。                                                   *
 * 规则：反序列化失败后不得继续读取字段；缺字段即拒绝。                  *
 * ------------------------------------------------------------------ */

static const char *find_key(const char *json, const char *key)
{
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\":", key);
    return strstr(json, pattern);
}

static bool copy_string_value(const char *json, const char *key, char *out, size_t cap)
{
    const char *p = find_key(json, key);
    if (p == NULL) {
        return false;
    }
    p += strlen(key) + 3;
    if (*p != '"') {
        return false;
    }
    p++;
    const char *end = strchr(p, '"');
    if (end == NULL || (size_t)(end - p) >= cap) {
        return false;
    }
    memcpy(out, p, (size_t)(end - p));
    out[end - p] = '\0';
    return true;
}

static long long read_int_value(const char *json, const char *key, bool *ok)
{
    const char *p = find_key(json, key);
    if (p == NULL) {
        *ok = false;
        return 0;
    }
    *ok = true;
    return strtoll(p + strlen(key) + 3, NULL, 10);
}

esp_err_t summon_parse_command_fields(const char *payload_json,
                                      summon_command_fields_t *out)
{
    memset(out, 0, sizeof(*out));
    bool ok = false;

    if (!copy_string_value(payload_json, "command_id", out->command_id,
                           sizeof(out->command_id))) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!copy_string_value(payload_json, "session_id", out->session_id,
                           sizeof(out->session_id))) {
        return ESP_ERR_INVALID_ARG;
    }
    long long epoch = read_int_value(payload_json, "lease_epoch", &ok);
    if (!ok || epoch < 1) {
        return ESP_ERR_INVALID_ARG;
    }
    out->lease_epoch = (uint32_t)epoch;

    char expires[32];
    if (!copy_string_value(payload_json, "expires_at", expires, sizeof(expires))) {
        return ESP_ERR_INVALID_ARG;
    }
    out->expires_at_ms = summon_parse_rfc3339_ms(expires);
    if (out->expires_at_ms <= 0) {
        return ESP_ERR_INVALID_ARG;
    }

    if (!copy_string_value(payload_json, "capability", out->capability,
                           sizeof(out->capability))) {
        return ESP_ERR_INVALID_ARG;
    }
    if (strcmp(out->capability, "display.text") == 0) {
        if (!copy_string_value(payload_json, "text", out->text, sizeof(out->text))) {
            return ESP_ERR_INVALID_ARG;
        }
    } else if (strcmp(out->capability, "arm.gesture") == 0) {
        if (!copy_string_value(payload_json, "name", out->gesture_name,
                               sizeof(out->gesture_name))) {
            return ESP_ERR_INVALID_ARG;
        }
        long long repeat = read_int_value(payload_json, "repeat", &ok);
        if (!ok || repeat < 1 || repeat > 3) {
            return ESP_ERR_INVALID_ARG;
        }
        out->repeat = (uint8_t)repeat;
    }
    /* speech.say 的 PCM 由网关侧合成后随命令下发；本版只校验能力名。 */
    return ESP_OK;
}
