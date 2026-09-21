# SUMMON 固件移植手册

2026-09-21 · v0.1.0 · **骨架阶段，未在真机验证。**

本手册说明如何把 SUMMON 协议移植到自己的硬件。落地点是
[`firmware/summon-device/README.md`](../firmware/summon-device/README.md)，
其中 `main/port_summon.c` 是唯一需要你修改的文件。

## 一、移植前必须知道的两件事

**1. 本模板不提供物理急停。**

刹车优先于一切。物理急停不经过网络、不经过软件、不需要任何人同意。
框架只能上报 `ESTOP`，不能代替它。**没有急停的机器不要接入本协议。**

**2. 目标平台仅 ESP-IDF。**

我们不承诺支持其他平台。协议本身语言无关——任何平台按
[`protocol/summon.schema.json`](../protocol/summon.schema.json) 的
`BridgeMessage` 自行实现即可互联，用不着这个模板。

## 二、`summon_port_t` 逐字段说明

定义在 [`firmware/summon-device/components/summon_device/include/summon_device.h`](../firmware/summon-device/components/summon_device/include/summon_device.h)。

### 能力声明

| 字段 | 类型 | 说明 |
|---|---|---|
| `capabilities` | `const char *const *` | 本壳实际具备的能力名数组，如 `{"display.text", "arm.gesture"}` |
| `capabilities_len` | `size_t` | 上面的元素个数，上限 8 |

实际使用时取**你的声明与网关本地允许动作的交集**。声明不扩大设备权限。

### 输出端（三个，全部只收完成回调）

| 字段 | 签名要点 |
|---|---|
| `display_text` | `(const char *text, size_t len, summon_done_cb_t done, void *user)` |
| `audio_pcm` | `(const uint8_t *pcm, size_t bytes, uint32_t hz, uint8_t bits, uint8_t ch, done, user)` |
| `gesture` | `(const char *name, uint8_t repeat, done, user)`；`name` 取 `nod/wave/point_left/point_center/point_right`，`repeat` 为 1–3 |

**三者都没有同步返回值**，只能通过 `done` 回调报告完成。因此：

> 阻塞式渲染/播放**必须**另起工作任务。本签名不允许同步等待——
> 这不是约定，是接口形状强制的结果。

`done` 的 `status` 只有三个值：`SUMMON_COMPLETED`（真的做完）、
`SUMMON_FAILED`、`SUMMON_UNKNOWN`（结果未能确认）。

### 认人入口

`on_identity(const char *address_hint)`——一次身份输入到达。
只负责投递，**不在本函数内做 DNS、网络、解码或 PCM 写入**。
`address_hint` 是寻址线索，不是凭证。

### 停止

`stop_all(summon_done_cb_t done, void *user)`——停止所有执行器并清空待执行队列。
必须让执行器**真的停下来**，停稳后才调 `done`。

> 因此 `device.stopped` 只能在实际停稳之后发出，**无法用计时器伪造完成**。

## 三、六条板侧规则与框架强制点

[`docs/ADAPTER.md`](ADAPTER.md) 的六条实测规则，每条在模板里都有一个强制点：

| # | 板侧规则 | 框架强制点 |
|---|---|---|
| 1 | JSON 容量按 16 KiB 上限推导 | `src/bridge_codec.c` 的 `SUMMON_MAX_FRAME_BYTES` 统一分配，超限即拒绝 |
| 2 | PCM 阻塞必须进工作任务 | `audio_pcm` 无同步返回，签名无法表达同步等待 |
| 3 | 音频单持有者 | Port 自持队列；模板的 `port_audio_pcm` 在未启用时直接失败 |
| 4 | 复用 BSP 总线 | `main/port_summon.c` 只调 BSP，不为 ES8311/CW2017 另建 I2C0 驱动 |
| 5 | 完成回执不得伪造 | `stop_all` 与三个输出都需回调；`src/stop_fsm.c` 未确认即不上报已停止 |
| 6 | 记录空闲堆与最大连续块 | 移植者自留：模板不代劳，见 [`ACCEPTANCE.md`](ACCEPTANCE.md) |

**规则 3 与 6 需要移植者自己负责**——框架能强制"必须异步完成"，
但不能验证你的音频是否只有一个持有者，也不能替你测堆碎片。

## 四、框架替你持有的安全规则

这些代码在 `components/summon_device/` 里，**移植者接触不到，也就无法绕过**：

| 规则 | 实现 |
|---|---|
| `command_id` 去重；同 ID 不同内容返回 `IDEMPOTENCY_CONFLICT` | `src/dedup.c` |
| 旧 `lease_epoch` 返回 `STALE_LEASE`，过期 `expires_at` 返回 `EXPIRED` | `src/lease.c` |
| 3 秒收不到 Gateway 心跳即本地停止并清队列 | `src/heartbeat.c` |
| `device.stopped` 只能在实际停稳后发出 | `src/stop_fsm.c` |
| 重连后未确认命令报 `UNKNOWN`，不自动重放 | `src/summon_device.c` 的分发顺序 |
| token 放在握手的 Authorization 头，不放入 URL | `src/ws_client.c` |

分派顺序固定，不得调换：**幂等命中先于 seq 与租约比较，但调用身份必须先验证**。

## 五、验收

模板层的结构验收由契约校验承担：

```sh
python tools/validate_contract.py
python tools/summon_device_ref.py --selftest
```

真机验收见 [`ACCEPTANCE.md`](ACCEPTANCE.md)，至少包括：

- 连续 10 次完整真实流程，记录失败数与耗时分布，不删除失败记录；
- 在连接前、TLS 建立后、解码器创建后、稳定播放时和清理后，
  记录空闲堆、最大连续块和任务栈高水位；
- 急停按钮位置对所有人可见、可触达，且切断后不自动恢复运动；
- 未实现的能力从 capabilities 与承诺中移除，不得口头声称已支持。

## 六、变更纪律

接口只在 `summon_device.h` 一处定义。需要加字段时：

1. 先改 [`protocol/summon.schema.json`](../protocol/summon.schema.json) 与
   [`protocol/examples/08-device-bridge.json`](../protocol/examples/08-device-bridge.json)；
2. 同步 [`docs/PROTOCOL.md`](PROTOCOL.md) 与 [`docs/ADAPTER.md`](ADAPTER.md)；
3. 破坏性改动升级 wire 主版本，旧端必须明确拒绝；
4. 记录到 `docs/ADR/`，不静默扩字段。

Python 参考客户端与 C 组件以 Schema 为唯一权威，任一改动必须同步，
否则两者会漂移。
