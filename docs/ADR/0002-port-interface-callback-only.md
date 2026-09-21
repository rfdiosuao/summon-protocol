# 0002. 端口接口只收完成回调、无同步返回

- 日期：2026-09-21
- 状态：Accepted
- 影响面：`firmware/summon-device/components/summon_device/include/summon_device.h` 的
  `summon_port_t`；`tools/summon_device_ref.py` 的 `Port` 基类

## 背景

FoloToy 仓库有一篇实测经验（`docs/reference/phoenixzhc/network-audio-streaming-and-memory.md`），
结论很硬：**PCM 读写是阻塞操作，必须放到工作任务中，不能放进 LVGL 或按键回调**；
音频设备只能有一个持有者。还有一个真实死机案例：固定 4096 字节的 JSON 文档去解析
实际约需 6971 字节的响应。

这些规则如果只写在文档里，靠移植者自觉，就一定会被违反——尤其在 63 小时赛期里。
ESP32-C3 单核、无 PSRAM，同步等待渲染或播放结束会直接饿死网络任务。

## 决策

`summon_port_t` 的三个输出函数（`display_text` / `audio_pcm` / `gesture`）与
`stop_all` **只接收一个完成回调，没有同步返回值**。

```c
void (*display_text)(const char *text, size_t len,
                     summon_done_cb_t done, void *user);
void (*stop_all)(summon_done_cb_t done, void *user);
```

因此：

- 阻塞式渲染/播放**必须**另起工作任务——签名不允许同步等待；
- `device.stopped` 只能在实际停稳之后发出，**无法用计时器伪造完成**；
- 框架独享 WSS、去重、租约、心跳，移植者接触不到这些代码，也就无法绕过。

## 为什么不是另一个方案

| 替代方案 | 否决理由 |
|---|---|
| 输出函数返回 `esp_err_t` 表示同步成功 | 这是最直觉的 C 写法，但它**允许**移植者在函数里阻塞等待渲染完成，从而饿死网络任务。我们要的是让错误写法无法表达 |
| 返回成功 + 单独的完成查询接口 | 把"是否完成"变成移植者的记忆负担，必然出现忘记查、或查到旧状态的情况 |
| 只在文档里写"必须异步" | 赛期里一定被违反；违反的后果是真机死机，不是编译错误，现场很难定位 |
| 用信号量让移植者自己同步 | 等于把工作任务模型外包给移植者，而这是最容易写错的部分 |

## 后果

换来：三条最危险的板侧规则由接口形状强制，不靠自觉；完成回执不可伪造。

付出：移植者的代码变复杂——必须自己管理工作队列和任务（`main/port_summon.c` 里的
`display_task` 示范了这一点）；Python 参考客户端同理，`Port` 基类也没有同步返回。

## 变更

无。
