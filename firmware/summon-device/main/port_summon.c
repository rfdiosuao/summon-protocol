/**
 * @file port_summon.c
 * @brief 参考端口：把 SUMMON 端口表绑定到 FoloToy AI Passport 的 BSP。
 *
 * **这是第三方唯一需要修改的文件。** 换硬件时只改这里，
 * 不要改 components/summon_device/ 下的任何代码。
 *
 * 三条纪律（来自 docs/ADAPTER.md 的板侧实现规则）：
 *  1. PCM 读写是阻塞操作，必须放进工作任务，不能放在这里同步等待；
 *  2. 音频设备只有一个持有者，新请求要么拒绝，要么先取消并等待旧任务退出；
 *  3. ES8311 与 CW2017 共用 I2C0，必须复用 BSP 总线，不另建驱动。
 */

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "bsp_button.h"
#include "bsp_display.h"
#include "bsp_audio.h"
#include "esp_log.h"

#include "summon_device.h"

static const char *TAG = "port_summon";

/** 一个待执行的显示任务。音频与手势同理各建一个队列。 */
typedef struct {
    char text[512];
    summon_done_cb_t done;
    void *user;
} display_job_t;

static QueueHandle_t s_display_queue;

/** 能力声明：本壳实际具备的能力。使用时取它与网关允许动作的交集。 */
static const char *const kCaps[] = {"display.text"};

static const summon_port_t kPort = {
    .capabilities = kCaps,
    .capabilities_len = 1,
    .display_text = port_display_text,
    .audio_pcm = port_audio_pcm,
    .gesture = port_gesture,
    .on_identity = port_on_identity,
    .stop_all = port_stop_all,
};

const summon_port_t *summon_port_get(void)
{
    return &kPort;
}

/* ---------------- 输出端 ---------------- */

static void display_task(void *arg)
{
    display_job_t job;
    for (;;) {
        if (xQueueReceive(s_display_queue, &job, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        /* 渲染放在工作任务里；持锁后才碰 LVGL，且不得阻塞网络任务。 */
        if (bsp_lvgl_lock(pdMS_TO_TICKS(200))) {
            /* 这里由移植者填入自己的 LVGL 渲染逻辑。 */
            render_text(job.text);
            bsp_lvgl_unlock();
            /* 完成回执必须是真实渲染之后，不得用动画计时伪造。 */
            job.done(SUMMON_COMPLETED, "rendered", job.user);
        } else {
            job.done(SUMMON_FAILED, "lvgl lock timeout", job.user);
        }
    }
}

void port_display_text(const char *text, size_t len, summon_done_cb_t done, void *user)
{
    if (s_display_queue == NULL || len >= sizeof(((display_job_t *)0)->text)) {
        done(SUMMON_FAILED, "queue not ready or text too long", user);
        return;
    }
    display_job_t job;
    memset(&job, 0, sizeof(job));
    memcpy(job.text, text, len);
    job.text[len] = '\0';
    job.done = done;
    job.user = user;
    /* 只入队，不在本函数内等待渲染完成。 */
    if (xQueueSend(s_display_queue, &job, 0) != pdTRUE) {
        done(SUMMON_FAILED, "display queue full", user);
    }
}

void port_audio_pcm(const uint8_t *pcm, size_t bytes, uint32_t hz, uint8_t bits,
                    uint8_t ch, summon_done_cb_t done, void *user)
{
    /* 本版未启用语音：板侧只接受 PCM，且音频单持有者。
     * 未适配前不要把 speech.say 列入 capabilities。 */
    done(SUMMON_FAILED, "speech not enabled in v1", user);
}

void port_gesture(const char *name, uint8_t repeat, summon_done_cb_t done, void *user)
{
    /* Passport 不是运动设备；arm.gesture 由现场电脑的厂商 SDK 执行。 */
    done(SUMMON_FAILED, "no actuator on this shell", user);
}

/* ---------------- 认人入口 ---------------- */

static void button_cb(void *user)
{
    /* 按键回调只负责投递，不做 DNS/网络/解码/PCM 写入。 */
    summon_port_t *port = (summon_port_t *)user;
    if (port->on_identity != NULL) {
        port->on_identity("button");
    }
}

void port_on_identity(const char *address_hint)
{
    ESP_LOGI(TAG, "identity hint: %s", address_hint);
}

/* ---------------- 停止 ---------------- */

static void stop_done_cb(summon_action_status_t status, const char *detail, void *user)
{
    EventGroupHandle_t ev = (EventGroupHandle_t)user;
    xEventGroupSetBits(ev, BIT0);
}

void port_stop_all(summon_done_cb_t done, void *user)
{
    /* 必须让执行器真的停下来才回调；框架据此才发 device.stopped。 */
    xQueueReset(s_display_queue);
    done(SUMMON_COMPLETED, "stopped", user);
}

/* ---------------- 初始化 ---------------- */

esp_err_t port_summon_init(void)
{
    s_display_queue = xQueueCreate(8, sizeof(display_job_t));
    if (s_display_queue == NULL) {
        return ESP_FAIL;
    }
    if (xTaskCreate(display_task, "summon_disp", 4096, NULL, 5, NULL) != pdPASS) {
        return ESP_FAIL;
    }
    /* 复用 BSP 的按键与显示，不另建一套同端口驱动。 */
    return bsp_button_init(button_cb, (void *)summon_port_get());
}
