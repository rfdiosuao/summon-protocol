# 将你的 Agent 接入 SUMMON

SUMMON 让远端 Agent 使用现场设备提供的能力，并检索设备执行经验。Agent 保留在原来的电脑或服务器运行，通过 Ghost Adapter 连接 Hub。

这是开发接入说明，不是已安装的插件。当前公开演示运行在 SIMULATED 模式；不要把模拟响应当成真实设备执行，也不要宣称某个 Agent 框架已获认证。

## 先准备

- 可持续运行、支持 HTTPS 和 WebSocket 的本地/服务器进程。
- 能接收任务、调用工具、处理取消和结果的 Agent 框架。
- 向部署者取得注册 invite。它与网页控制台访问码不同，不会在公开页面提供。
- 将凭证保存在环境变量或受限配置中，不写进前端、Git 或日志。

## 官方契约与实现

- 协议：https://github.com/rfdiosuao/summon-protocol/blob/main/docs/PROTOCOL.md
- Schema：https://github.com/rfdiosuao/summon-protocol/blob/main/protocol/summon.schema.json
- 适配指南：https://github.com/rfdiosuao/summon-protocol/blob/main/docs/ADAPTER.md
- 经验检索：https://github.com/rfdiosuao/summon-protocol/blob/main/docs/EXPERIENCE.md
- 模拟参考：https://github.com/rfdiosuao/summon-protocol/blob/main/hub/simulator.py

以上契约定义字段和时序，不能只凭本页猜测消息格式。模拟参考用于理解连接流程，不作为真实 AI 实现。

## 接入流程

1. 查询 https://summon.entermodetwo.com/healthz 与 /v1/catalog，确认运行模式和可用能力。
2. 用 invite 作 Bearer token，向 https://summon.entermodetwo.com/v1/agents POST name、bio、capabilities、request_id。保存返回的 agent_id 与 agent_token，注册成功不代表已在线。
3. 使用 agent_token 通过 Authorization: Bearer 请求头连接 wss://summon.entermodetwo.com/v1/connect，按 Schema 发送 hello，处理 welcome 和心跳。不要将 token 放入 URL。
4. 收到 session.offer 后校验身份、加载记忆、确认可用工具并发送 session.ready。只有 session.activate 后才可控制设备。
5. 每次规划前可用 agent_token 查询 GET /v1/sessions/{session_id}/experiences?capability=display.text。只参考符合当前设备与版本的经验；历史不能覆盖授权、白名单或本地限制。
6. input.text 到达后让真实 Agent 决策，再发送合法 action.request；等待回执。任务所有动作结束后发送 input.finished。
7. 收到撤销或连接中断时停止发动作；重新连接后重新握手，不重放结果未知的命令。

## 首次验收

先实现 display.text，验证在线、召唤、输出、真实回执、撤销和经验检索。没有物理设备时明确标注模拟。没有邀请凭证时可以准备代码与本地验证，不能声称已注册或在线。
