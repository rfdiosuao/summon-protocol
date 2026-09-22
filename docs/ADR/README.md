# 架构决策记录（ADR）

这里记录**影响协议、接口或安全模型的决策**，以及当时为什么这么选。

不是所有改动都需要 ADR。需要的是那种"三个月后有人问当初为什么这样"的决策——
尤其是接口形状、错误语义、以及任何与安全相关的取舍。

## 什么时候写一篇

- 要改 `summon_port_t`、`BridgeMessage` 或任何 wire 格式
- 要加/改一个错误码的语义
- 在两个方案之间做了取舍，且另一个方案看起来也合理
- 发现之前的决策错了，要反转它

## 格式

复制 [`template.md`](template.md)，文件名用 `NNNN-短横线标题.md`，编号只增不减。
作废的决策不要删，新写一篇标记它为 Superseded。

## 索引

| 编号 | 决策 | 状态 |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | 用 ADR 记录架构决策 | Accepted |
| [0002](0002-port-interface-callback-only.md) | 端口接口只收完成回调、无同步返回 | Accepted |
| [0003](0003-nfc-reader-not-on-the-arm.md) | NFC 读卡器不挂在机械臂上 | Accepted |
| [0004](0004-speculative-preparation-is-optional.md) | 概率预判只用于可取消的准备工作 | Accepted（实现未开始） |

## 纪律

ADR 与 [`protocol/summon.schema.json`](../../protocol/summon.schema.json) 与
[`protocol/examples/README.md`](../../protocol/examples/README.md)
是同一份真相的不同侧面。
改协议必须同步这三处，并在对应的 ADR 里追加一段"变更"。
