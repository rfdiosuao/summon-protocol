# SUMMON 设备端模板

> ## 本模板**不提供物理急停**。急停是硬件，必须由你自己实现。
>
> 框架只能上报 `ESTOP`，不能代替一个任何人都能按下的实体按钮。
> **没有急停的机器不要接入本协议。** 详见 [docs/FIRMWARE.md](../../docs/FIRMWARE.md)。

**目标平台仅 ESP-IDF。** 我们不承诺支持其他平台；协议本身语言无关，
任何平台按 [protocol/summon.schema.json](../../protocol/summon.schema.json)
的 `BridgeMessage` 自行实现即可互联。

## 三步移植

1. **复制端口表**：把 `main/port_summon.c` 复制成你自己的文件，
   实现 [`summon_port_t`](components/summon_device/include/summon_device.h)
   里的能力声明与五个回调，并让 `summon_port_get()` 返回它。
2. **不要改框架**：`components/summon_device/` 下的代码持有 WSS、编解码、
   去重、租约、心跳与停止状态机。改它就意味着绕过安全规则。
3. **实现你自己的急停**：一个实体按钮，切断所有外部控制，
   不经过网络、不经过软件、不需要任何人同意。

```sh
source <ESP-IDF-v5.5.3-路径>/export.sh
idf.py set-target esp32c3
idf.py build
```

## 这个模板替你保证什么

| 规则 | 由谁保证 |
|---|---|
| `command_id` 去重，同 ID 同内容不重复执行 | `src/dedup.c` |
| 旧 `lease_epoch` 与过期 `expires_at` 一律拒绝 | `src/lease.c` |
| 3 秒收不到 Gateway 心跳即本地停止并清队列 | `src/heartbeat.c` |
| `device.stopped` 只能在实际停稳后发出 | `src/stop_fsm.c` + 接口签名 |
| 控制帧 16 KiB 上限，容量按上限推导 | `src/bridge_codec.c` |
| 重连后未确认命令报 `UNKNOWN`，不自动重放 | 框架持有未确认集合 |

## 这个模板不替你保证什么

- **物理急停**——必须是你自己的硬件。
- **执行器驱动**——显示、音频、运动都要你自己接。
- **配网与证书分发**——需要自己补。
- **真实渲染/播放完成回执**——框架只规定"必须真实完成后才回调"，
  谎言由你的 Port 说，框架无法验证。

## 板侧实现规则

移植前先读 [`docs/ADAPTER.md`](../../docs/ADAPTER.md) 的「板侧实现规则」
六条。其中两条最容易在真机上死人：

1. **JSON 容量按 16 KiB 上限推导。** 上游有一个真实死机案例：固定 4096 字节的
   JSON 文档去解析实际约需 6971 字节的响应。
2. **PCM 读写必须进工作任务。** 绝不能放在 LVGL 或按键回调里同步等待——
   本模板的接口签名已经让同步等待无法表达。

## 当前状态

**骨架阶段，未在真机验证。** 协议层与安全模型已定，`components/summon_device/`
可在 ESP-IDF 5.5.3 下编译；Passport 的真实显示与按键联调尚未完成。
不要把本模板描述为已被第三方采用——没有采用记录。

## 相关文档

- [协议规范](../../docs/PROTOCOL.md)
- [接入指南](../../docs/ADAPTER.md)
- [移植手册](../../docs/FIRMWARE.md)
- [验收说明](../../docs/ACCEPTANCE.md)
- [Python 参考客户端](../../tools/summon_device_ref.py)（可在无硬件时先跑通协议）
