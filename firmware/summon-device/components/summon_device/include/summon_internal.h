/**
 * @file summon_internal.h
 * @brief 框架内部上下文与子模块接口。移植者不需要读这个文件。
 */

#pragma once

#include <stddef.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "summon_device.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 控制帧上限：与 docs/PROTOCOL.md 第 4 节一致。 */
#define SUMMON_MAX_FRAME_BYTES (16 * 1024)

/** 心跳间隔与超时：与契约一致。 */
#define SUMMON_HEARTBEAT_INTERVAL_MS 1000
#define SUMMON_HEARTBEAT_TIMEOUT_MS  3000

/** 去重记录保留时长：与契约一致。 */
#define SUMMON_DEDUP_RETENTION_S (24 * 3600)
#define SUMMON_DEDUP_CAPACITY   32

/** 租约硬上限：与契约一致。 */
#define SUMMON_LEASE_MAX_SECONDS 60

typedef struct {
    char command_id[81];
    char payload_hash[65];   /* SHA-256 hex */
    summon_action_status_t state;
    int64_t recorded_at;
} summon_dedup_entry_t;

/** 从 device.command 的 payload 解析出的字段。 */
typedef struct {
    char command_id[81];
    char session_id[81];
    uint32_t lease_epoch;
    int64_t expires_at_ms;
    char capability[SUMMON_MAX_CAP_LEN];
    char text[512];            /* display.text */
    const uint8_t *pcm;        /* speech.say */
    size_t pcm_len;
    uint32_t hz;
    uint8_t bits;
    uint8_t ch;
    char gesture_name[24];     /* arm.gesture */
    uint8_t repeat;
} summon_command_fields_t;

typedef struct {
    const summon_device_config_t *config;
    const summon_port_t *port;

    /* 编解码 */
    uint8_t *frame_buf;
    size_t frame_buf_len;

    /* 去重 */
    summon_dedup_entry_t dedup[SUMMON_DEDUP_CAPACITY];

    /* 租约 */
    uint32_t lease_epoch;
    int64_t lease_expires_at_ms;

    /* 停止状态机 */
    volatile bool stopping;
    volatile bool stopped_confirmed;
    char pending[SUMMON_DEDUP_CAPACITY][81];
    size_t pending_len;

    /* 心跳 */
    volatile int64_t last_gateway_heartbeat_ms;

    /* 连接 */
    volatile bool connected;

    /* 当前正在执行的动作，用于完成回执 */
    char current_command_id[81];
} summon_device_ctx_t;

typedef struct {
    const char *device_id;
    const char *shell_id;
    const char *gateway_url;   /* 本地 WSS，如 wss://192.168.1.20/device */
    const char *device_token;  /* 一对一绑定 device_id 与 shell_id */
} summon_device_config_t;

/* ---- 子模块：每个文件一个职责，全部由框架持有 ---- */

esp_err_t summon_codec_init(summon_device_ctx_t *ctx);
esp_err_t summon_codec_encode(summon_device_ctx_t *ctx, const char *type,
                              const char *payload_json, uint8_t *out, size_t *out_len);
esp_err_t summon_codec_decode(summon_device_ctx_t *ctx, const uint8_t *raw, size_t len,
                              char *type_out, char *payload_out, size_t payload_cap);

esp_err_t summon_dedup_init(summon_device_ctx_t *ctx);
/** 同 ID 同内容返回已有状态；同 ID 不同内容返回 ESP_ERR_INVALID_STATE。 */
esp_err_t summon_dedup_lookup(summon_device_ctx_t *ctx, const char *command_id,
                              const char *payload_hash, summon_action_status_t *state_out);
esp_err_t summon_dedup_remember(summon_device_ctx_t *ctx, const char *command_id,
                                const char *payload_hash, summon_action_status_t state);

esp_err_t summon_lease_init(summon_device_ctx_t *ctx);
esp_err_t summon_lease_check_command(summon_device_ctx_t *ctx, uint32_t lease_epoch,
                                     int64_t expires_at_ms);

esp_err_t summon_stop_fsm_init(summon_device_ctx_t *ctx);
esp_err_t summon_stop_fsm_enqueue(summon_device_ctx_t *ctx, const char *command_id);
size_t summon_stop_fsm_clear(summon_device_ctx_t *ctx);
/** 调 Port 的 stop_all 并等待确认；未确认不得上报已停止。 */
esp_err_t summon_stop_fsm_stop(summon_device_ctx_t *ctx);

esp_err_t summon_heartbeat_init(summon_device_ctx_t *ctx);
void summon_heartbeat_task(void *arg);
bool summon_heartbeat_overdue(summon_device_ctx_t *ctx);

esp_err_t summon_ws_init(summon_device_ctx_t *ctx);
esp_err_t summon_ws_send_hello(summon_device_ctx_t *ctx);
esp_err_t summon_ws_send_heartbeat(summon_device_ctx_t *ctx);
/** 发送一帧已编码好的 BridgeMessage。 */
esp_err_t summon_ws_send(summon_device_ctx_t *ctx, const uint8_t *data, size_t len);
/** 收到一帧后交回框架处理；不在 WS 回调里做解码或执行。 */
void summon_ws_on_frame(summon_device_ctx_t *ctx, const uint8_t *raw, size_t len);
void summon_ws_reader_task(void *arg);

/* ---- 框架内部通知：供 stop_fsm 与 ws_client 回调使用 ---- */
void summon_device_notify_stopped(void);
void summon_device_notify_hello_ok(void);
void summon_heartbeat_note_gateway(summon_device_ctx_t *ctx);

/* ---- 去重辅助：由 command payload 算稳定散列 ---- */
void summon_dedup_hash_payload(const char *payload_json, char *out, size_t out_cap);

/* ---- command 字段解析：反序列化失败后不得继续读取字段 ---- */
esp_err_t summon_parse_command_fields(const char *payload_json,
                                      summon_command_fields_t *out);

/* ---- 时间：RFC3339 UTC -> 毫秒。非法输入返回 0。 ---- */
int64_t summon_parse_rfc3339_ms(const char *text);

#ifdef __cplusplus
}
#endif
