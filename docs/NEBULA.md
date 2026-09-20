# NEBULA 星图官网与操作台

2026-09-20 · v0.1.0 · 尚未实现。字段以 [Schema](../protocol/summon.schema.json) 为准，时序见 [PROTOCOL](PROTOCOL.md)。

## 1. 目标

官网与展位操作台共用前端。让人看懂“云端 Agent 换身体”，并实际选择 Agent、选择壳、提交任务、查看结果。手机使用响应式网页。

首页：一句话价值、真实在线/可用数、星图/列表、设备卡、任务面板、记忆变化、接入文档与实现证据。不写死“3 具义体 · 全部空载”。保留深夜底、琥珀激活、青绿记忆；名字衬线、日志等宽，布局交给设计师。

## 2. 视觉语义

| 图层 | 意义 | 来源 |
|---|---|---|
| 背景粒子 | 纯装饰或模型表面采样，不代表 Agent | 视觉资源 |
| 可点击亮星 | 一个实际登记 Agent，区分在线/离线 | 快照 agents |
| 模型/设备卡 | 实际配置的壳 | 快照 shells |
| 飞线 | 连接/交接中，不代表成功 | session 事件 |
| 结果面板 | 动作回执和记忆保存 | action / memory 事件 |

初版可以只有一颗真实 Agent 星，不能为了形成模型虚构数量。

## 3. 名片与权限

显示 `name`、`agent_id`、`bio`、`capabilities`、`status`、`summons`、可空的 `last_landed_at`。ID 可复制；不用模糊名字驱动设备。

注册 `POST /v1/agents` 需现场邀请；登记不代表在线。连接见 [ADAPTER](ADAPTER.md)。`agent_token` 只交给注册者，不广播到星图或分析日志。

公开页仅看脱敏目录。现场操作台需 operator 会话；不能根据访问 URL/处在展位就授予设备控制。首版登录用现场访问码换服务端 cookie，无需用户账号体系。

## 4. 事件映射

| 权威信息 | 展示 |
|---|---|
| 点击、HTTP 未返回 | 本地反馈，不声称入住 |
| HTTP 202 / session.granted | 连接中、飞线 |
| session.active | 已入住 |
| action.accepted | 已接收，等待执行 |
| action.started | 执行中 |
| action.completed | 完成和结果 |
| action.failed | 失败原因；UNVERIFIED 不能显示完成 |
| memory.updated | 已保存及版本 |
| memory.failed | 未保存，不声称反馈已继承 |
| session.released | 旧壳退出；新 active 才显示迁入 |
| shell.state OFFLINE/FAULT/ESTOP | 禁止召唤、显示真实状态 |

GLB 加载、动画完成、五要素自检勾选不能改变真实设备权限或能力。

## 5. 刷新与断网

先 GET `/v1/state` 取得快照和 cursor，再 SSE `/v1/events?after=<cursor>`。按 event_id 去重。刷新或掉线立即禁用控制、标失联，重新获取快照后恢复；`stream.reset` 表示历史被裁剪或服务器重启，需清空旧展示状态。

`LIVE / SIMULATED / LOCAL_SHADOW / REPLAY` 全程标识。回放由用户显式打开，是只读页面，不能调控制写接口，不计入真实入住数。公开流无任务文本和私人记忆，operator 流按权限显示。

## 6. 技术与范围

推荐 Vite + TypeScript + Three.js，HUD 用 DOM。后续类型由 Schema 生成，运行时也验证 JSON，不维护第二份 GhostPin/ShellPin 业务定义。

P0：可操作首页、名片、设备卡、输入、结果、错误和记忆确认。P1：一个 GLB、一种转场。P2：模型拖拽，仅替换视觉，不改变 capabilities。记录模型来源和许可。

演示机 1080p 下实测交互流畅；低端手机减粒子、关 bloom。WebGL 不可用直接 DOM 列表，不要求再实现一套 Canvas 星图。指标未实测前不得宣传 20,000 个真实 Agent、零延迟或固定帧率。

## 7. 验收

- [ ] 公共样例覆盖成功、忙碌、离线、拒绝、过期、记忆失败。
- [ ] 注册只新增登记，连通后才显示在线；HTTP 202 不报完成。
- [ ] 刷新恢复真实状态；断线立即禁控；回放与真实流隔离。
- [ ] 装饰粒子不计真实数量；GLB 不改变设备徽章。
- [ ] 手机和 WebGL 失败时仍可完成操作；日志无密钥。
- [ ] Day3 21:00 冻结视觉，之后只修影响演示的问题。
