# 三端共用联调样例

这些是静态契约样例，不是已经运行过的服务日志。全部事件使用 SIMULATED；文件名 live-flow 指未来真实链路的顺序，不代表实机完成。

每个 JSON 包含 description 与 checks；每项的 schema 指向主 Schema 的 `$defs`，valid 表示该数据结构应通过还是应被拒绝，data 是实际请求/响应/消息，note 是解释。不同类型混排用于走读业务顺序，不代表它们经过同一种传输通道。消息方向见 [公共契约](../../docs/PROTOCOL.md)。固定日期只用于校验样例，不可直接向设备重放。

| 文件 | 共同验收的行为 |
|---|---|
| [01-live-flow](01-live-flow.json) | 就绪报告、登记、连接、双方 ready、激活、输入和执行回执 |
| [02-memory-handoff](02-memory-handoff.json) | 保存偏好后停止旧壳，再将同一版本加载到新壳 |
| [03-errors](03-errors.json) | 统一错误结构和全部错误码 |
| [04-duplicate-command](04-duplicate-command.json) | 同 command_id 重试返回原结果，只有一次 started |
| [05-handoff-blocked](05-handoff-blocked.json) | 没有旧壳停止确认，不激活新壳 |
| [06-memory-failure](06-memory-failure.json) | 写入失败版本不变、不发成功 |
| [07-snapshot-reconnect](07-snapshot-reconnect.json) | 快照、游标失效、重新取得快照 |
| [08-device-bridge](08-device-bridge.json) | 板子显示回执、停止和停止确认 |
| [09-invalid-payloads](09-invalid-payloads.json) | 8 种结构错误必须被 Schema 拒绝 |
| [10-stale-and-expired](10-stale-and-expired.json) | 结构合法的旧租约/过期动作仍须运行时拒绝 |

前端以这些数据制作状态组件，Hub 实现相应路由和事件，Gateway 实现对应回执。不要把样例播放器当作真实后端。完整内存只发给 Agent；发给 Gateway 的 session.offer 删除 memory 字段。

在仓库根目录执行：

```sh
python -m pip install -r protocol/requirements.txt
python tools/validate_contract.py
```

校验包括 JSON Schema 本身、正反例、关键样例顺序及 Markdown 本地文件链接。它不能证明运行时鉴权、事务、网络恢复或电机停止正确；这些按 [ACCEPTANCE](../../docs/ACCEPTANCE.md) 实测。
