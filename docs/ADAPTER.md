# Agent 与设备接入指南

本页描述待实现服务的适配顺序，不是可用在线 API。所有字段见 [公共契约](PROTOCOL.md)，可直接验证的数据见 [样例](../protocol/examples/README.md)。

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

## 前端（设计师）

公开 catalog → 现场访问码登录 → state → SSE → 按 ID 发召唤/输入/交接。初版用 examples 模拟接口，显式显示 SIMULATED。前端不推断成功，不控制电机，模型文件与真实能力无关。

三人独立开发时的共同完成门槛：同一条 task 从输入到结果能串起 session_id、input_id、command_id；错误路径也能由样例复现。接入仍需实测，不声明当前支持所有 Agent 框架。
