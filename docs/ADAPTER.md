# Agent 与设备接入指南

Hub 与模拟联调服务已实现；真实 Agent 与设备仍需各自实现适配器。首页提供 [Agent 接入说明](../web/agent.md)，注册需部署者提供 invite。所有字段见 [公共契约](PROTOCOL.md)，可直接验证的数据见 [样例](../protocol/examples/README.md)。

## Ghost Adapter（队长）

1. 从现场取得 invite，POST `/v1/agents` 登记 name/bio/capabilities/request_id。保存 agent_id 与注册响应中的 agent_token；不能把注册响应原样发到公共日志。
2. 在已有 Agent 运行进程旁启动 Adapter，主动用 token 连接 WSS `/v1/connect`，发送 hello(role=agent,id=agent_id)。收到 welcome 后按间隔 heartbeat。
3. 收到 session.offer：核验 agent_id，加载 Hub 提供的该操作者 Memory，将 permitted_capabilities 转成可调用工具，发送 session.ready。收到 activate 前不调用设备。
4. input.text 到达后，由真实 Agent 结合记忆决定工具和参数，逐一发送 action.request。等待真实完成/失败，再决定下一步；任务所有动作终止后发送 input.finished。
5. 已确认反馈发送 memory.update，保存成功才告诉用户“记住了”。收到 revoke 立即停止提交动作和更新。
6. 断线重连必须重新握手与召唤，不恢复旧租约。现有家庭 Agent 框架需有插件/工具适配入口，不能靠三个字符串字段代替运行连接。

可以给出简短的 SDK 调用示例，但底层实现长度不设 20 行目标。外部 Agent 作者需要理解：连接、任务回调、工具结果、记忆确认、撤销。

## Shell Gateway（小胖）

现场电脑持有 Gateway token，绑定允许的 shell_id。Gateway 聚合本地 Passport 和可选机械臂：板侧是低资源设备客户端，厂商 SDK 留在电脑。

1. 本地配置壳身份、capabilities、允许动作、速度/幅度约束、本地启用和停止路径。
2. 连接 Hub，hello(role=gateway,id=shell_id)；一条连接对应一具逻辑壳。两具壳由同一网关进程维护两条身份连接。
   握手后及物理状态改变时发送 shell.report；READY 只能在本地核验就绪后报告，故障/急停立即上报。
3. offer 时验证租约、策略与硬件就绪，持久化 epoch 后 ready；activate 后才接受动作。
4. 对 action.request 校验、先写幂等记录，再驱动实际设备；发 accepted/started/真实结果。
5. revoke/过期/Hub 失联/急停时停止新动作、清队列，使用厂商停止方法；只有确认无未终止命令后发送 stopped。

板侧桥接也使用本仓库的 BridgeMessage Schema：本地 Wi-Fi WSS `/device`，独立设备 token 与配对的 shell_id，禁止把 Hub 管理密钥烧入固件。板连接先 device.hello；Gateway 维护设备心跳。`display.text` 完成需固件渲染后回执，`speech.say` 完成需播放结束回执。多执行器组合时由 Gateway 路由，Passport 不控制机械臂电机。

板桥双方每秒发送 device.heartbeat（device_id 为配对板的 ID）；板侧 3 秒收不到 Gateway 心跳即停止播放、清空待执行队列。device.stop 撤销指定 session/epoch，板实际停止后回 device.stopped，并拒绝该会话后续命令。Gateway 等待所有组合执行器停止，才能向 Hub 回 session.stopped。device.command 检查截止时间并按 command_id 去重；重连不重放未确认命令，改报 UNKNOWN，由 Gateway 核对。板侧不接收长期 Memory。

## Gateway 侧输入源（NFC / 语音转写）

非文字输入与按键走同一条路：Gateway 本地捕获 → 通过受认证的 `input.submit` 提交 → 进入与文字输入完全相同的去重与调度路径。

| 输入源 | 捕获方 | 说明 |
|---|---|---|
| 按键 | Passport 板侧 | 已有路径 |
| NFC 读卡 | Booth 读卡器 + Gateway | 读卡即得地址线索，仍需 operator 会话授权 |
| 语音转写 | Gateway 本地 ASR | 转写后提交，原始音频不持久化 |

三者不改变协议语义，只改变"认人入口"这一要素的实现。**换一个入口，故事一个字不用改。**

## 板侧实现规则

可选的 Gateway 预判组件遵循 [ADR 0004](ADR/0004-speculative-preparation-is-optional.md) 与 [实验规格](REFLEX.md)：只准备本地无副作用资源，不创建会话、不预取个人记忆、不执行影子计划。它默认关闭，不改变以下板侧实现规则。

以下规则来自 FoloToy 仓库的实测经验，不是理论值；违反任何一条都可能在真机上死机。

1. **JSON 容量按允许的最大响应推导。** 契约的控制帧上限是 16 KiB，板侧 JSON 文档容量必须容得下它。已知长度时解析前拒绝超限；长度未知时只允许累计到明确上限；反序列化失败后不得继续读取字段。上游有一个真实死机案例：固定 4096 字节的 JSON 文档去解析实际约需 6971 字节的响应。
2. **PCM 读写是阻塞操作，必须放工作任务。** 绝不能放进 LVGL 或按键回调。按键回调只负责投递命令。
3. **音频设备只有一个持有者。** 新请求要么拒绝，要么先取消并等待旧任务退出，之后才能再次打开 I2S。
4. **复用 BSP 总线**，不为 ES8311/CW2017 另建 I2C0 驱动。
5. **`display.text` 完成回执必须是真实渲染后**；`speech.say` 完成回执必须是播放结束后。不得用动画计时伪造完成。
6. **同时记录空闲堆与最大连续空闲块。** 常见故障不是缓冲太小，而是堆碎片——总空闲堆够但连续块不够。

## 参考客户端（设备端样板）

`tools/summon_device_ref.py` 是协议的可执行样板，标准库即可跑通全部自检。

```sh
python tools/summon_device_ref.py --selftest      # 六个子用例，CI 也跑这条
python tools/summon_device_ref.py --case dedup    # 单独跑一个子用例
python tools/summon_device_ref.py --port cli      # 终端当屏幕，交互试用
python tools/summon_device_ref.py --emit out.jsonl  # 导出收发消息供校验
python tools/summon_device_ref.py --connect ws://127.0.0.1:8800/device  # 连真实 Gateway
```

六个子用例各自对应一条安全规则，失败时直接指出是哪条被破坏：

| 子用例 | 断言的规则 |
|---|---|
| `full_sequence` | hello → command → result → stop → stopped 齐全，执行一次 |
| `dedup` | 同 `command_id` 同内容只执行一次；同 ID 不同内容报 `IDEMPOTENCY_CONFLICT` 且不执行 |
| `stale_lease` | 旧 `lease_epoch` 返回 `STALE_LEASE` |
| `expired` | 过期 `expires_at` 返回 `EXPIRED` 且不执行 |
| `heartbeat_timeout` | 超 3 秒收不到 Gateway 心跳即本地停止并清队列 |
| `reconnect` | 重连后未确认命令报 `UNKNOWN`，绝不自动重放 |

**它同时是 P0 的模拟壳**：Hub 与前端在没有真实固件时，用它顶替一台设备；`--emit` 导出的消息由 `tools/validate_contract.py` 按同一份 Schema 校验，因此样板与静态样例不会漂移。

**它不提供物理急停。** 急停是硬件，框架只能上报 `ESTOP`。



公开 catalog → 现场访问码登录 → state → SSE → 按 ID 发召唤/输入/交接。初版用 examples 模拟接口，显式显示 SIMULATED。前端不推断成功，不控制电机，模型文件与真实能力无关。

三人独立开发时的共同完成门槛：同一条 task 从输入到结果能串起 session_id、input_id、command_id；错误路径也能由样例复现。接入仍需实测，不声明当前支持所有 Agent 框架。
