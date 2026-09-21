/**
 * @file summon_device.h
 * @brief SUMMON 设备端框架的移植接口。
 *
 * 第三方把固件移植到自己的硬件时，**只需要实现这一个头文件里的
 * summon_port_t**，其余代码（WSS、编解码、去重、租约、心跳、停止状态机）
 * 由本框架持有，不要修改。
 *
 * 三条安全规则由签名本身强制，不靠移植者的自觉：
 *
 *  1. 三个输出函数**没有同步返回值**，只能通过 done 回调报告完成。
 *     因此阻塞式渲染/播放必须另起工作任务——本签名不允许同步等待。
 *  2. stop_all 也要回调，因此 device.stopped 只能在实际停稳之后发出，
 *     无法用计时器伪造完成。
 *  3. command_id 去重、租约与截止时间校验、心跳超时即停、重连不重放，
 *     全部在框架内，移植者接触不到这些代码，也就无法绕过。
 *
 * 本模板**不提供物理急停**。急停是硬件，框架只能上报 ESTOP。
 */

#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** 动作完成状态。只有设备真的做完才报 COMPLETED。 */
typedef enum {
    SUMMON_COMPLETED = 0,  /**< 真实完成（渲染/播放/运动结束） */
    SUMMON_FAILED = 1,     /**< 执行失败 */
    SUMMON_UNKNOWN = 2,    /**< 结果未能确认，需现场核对 */
} summon_action_status_t;

/**
 * 动作完成回调。status 与 detail 由 Port 在**真的做完**之后调用。
 * detail 是人类可读说明，不超过 64 字节。
 */
typedef void (*summon_done_cb_t)(summon_action_status_t status, const char *detail, void *user);

/** 能力名上限。超出视为配置错误。 */
#define SUMMON_MAX_CAPS 8
#define SUMMON_MAX_CAP_LEN 32

/**
 * 移植端口。第三方实现这一个结构体并让 summon_port_get() 返回它。
 */
typedef struct {
    /* ---------- 能力声明 ---------- */

    /** 实际具备的能力名数组，如 {"display.text", "arm.gesture"}。 */
    const char *const *capabilities;
    /** capabilities 的元素个数。 */
    size_t capabilities_len;

    /* ---------- 输出端 ---------- */

    /**
     * 显示一段文字。必须异步执行：另起工作任务渲染，渲染完成后调 done。
     * 禁止在本函数内阻塞等待渲染结束。
     */
    void (*display_text)(const char *text, size_t len, summon_done_cb_t done, void *user);

    /**
     * 播放 PCM。hz/bits/ch 描述采样格式。板侧只接受 PCM，
     * 因此 speech.say 的合成必须在网关或服务器侧完成。
     */
    void (*audio_pcm)(const uint8_t *pcm, size_t bytes,
                      uint32_t hz, uint8_t bits, uint8_t ch,
                      summon_done_cb_t done, void *user);

    /**
     * 执行一个预设动作。name 取 nod/wave/point_left/point_center/point_right，
     * repeat 为 1–3。只允许已校准的预设轨迹，不接收任意关节角或自由坐标。
     */
    void (*gesture)(const char *name, uint8_t repeat, summon_done_cb_t done, void *user);

    /* ---------- 认人入口 ---------- */

    /**
     * 一次身份输入到达。只负责投递，不在本函数内做 DNS、网络、解码或 PCM 写入。
     * address_hint 是寻址线索，不是凭证。
     */
    void (*on_identity)(const char *address_hint);

    /* ---------- 停止 ---------- */

    /**
     * 停止所有执行器并清空待执行队列。必须让执行器**真的停下来**，
     * 停稳后才调 done；框架据此才发送 device.stopped。
     */
    void (*stop_all)(summon_done_cb_t done, void *user);
} summon_port_t;

/**
 * 第三方必须实现的唯一函数：返回自己的端口表。
 * 框架在 summon_device_start() 时调用它。
 */
const summon_port_t *summon_port_get(void);

#ifdef __cplusplus
}
#endif
