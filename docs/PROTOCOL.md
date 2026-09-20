# SUMMON 公共契约

版本 **0.1.0** · wire `v: 1` · 2026-09-20 · 规范草案已可校验，服务尚未实现。

这是前端、Hub、Ghost Adapter、Shell Gateway 的共同约定。字段/枚举以 [summon.schema.json](../protocol/summon.schema.json) 为准；本文件定义 Schema 无法表达的时序、认证和状态约束。Schema 中的 `$defs` 名称是以下各表使用的类型名。

## 1. 范围与身份

MVP：一个 Hub、一个权威记忆库、一个真实 Agent、两个壳。一具壳只有一个执行会话，一个 Agent 同时只能持有一具壳；拒绝忙碌请求，不排队、不抢占。Hub 不接受外部 callback URL，Agent 与 Gateway 都主动建立出站 WSS。

| 字段 | 生成/归属 | 规则 |
|---|---|---|
| agent_id | Hub | 稳定、不复用、不随名字变；展示/寻址均可用 |
| name | 注册者 | NFC Unicode 规范化后唯一，1–40 字符；MVP 不支持改名 |
| shell_id | Hub / 部署配置 | 主人授权下登记；Gateway 只可代表绑定的壳 |
| operator_id | Hub 现场访问码绑定 | 操作者/匿名访客的记忆隔离主体；不是浏览器自报 |
| session_id | Hub | 一次接入；换壳创建新 ID，不复用旧 ID |
| lease_epoch | Hub | 每个壳单调递增、持久化；旧 epoch 永远不可执行 |
| command_id | Ghost Adapter | 同一逻辑动作重试必须保持 ID |
| request_id / update_id | 调用方 | HTTP 写幂等键 / 记忆更新幂等键 |
| message_id | 发送端 | 一次传输的追踪标识，不替代逻辑幂等键 |
| event_id / cursor | Hub | 不透明 `stream_id:sequence`，不要求客户端解析 |

ID 使用 ASCII 字母、数字、下划线、短横线，1–80 字符。时间统一 UTC RFC3339 字符串，延迟为整数毫秒。Schema 校验要启用 date-time format 校验。名字是展示/解析字段，内部执行只用 ID。`summon://<agent_id>?v=1` 仅为寻址线索，不是凭证。

`summons` 只在 LIVE 会话第一次进入 ACTIVE 时加一，重发事件、失败连接、回放不加。`last_landed_at` 未入住时为 null。公开展示只开放部署时选定的演示 Agent 和壳，不泄露其他操作者信息。

## 2. 认证与权限

- Hub 部署时配置短期接入邀请、operator 访问码、Gateway 凭证；不实现通用账号/密码系统，但不是无认证系统。
- `POST /v1/agents` 带 `Authorization: Bearer <invite>`。注册响应含 `agent_token`，绑定 agent_id；同一已授权幂等请求在保留期内可重取原响应，其他查询不再返回凭证。该 token 仅允许自身连接和当前获准会话动作。名字重复 409，邀请不能充当动作 token。
- `POST /v1/operator-session` 用现场访问码换 cookie，返回服务端绑定的 operator_id。cookie 为 Secure、HttpOnly、SameSite=Strict；写接口验证精确 Origin，CORS 仅允许指定前端源。失败返回 401，生产日志不记录访问码/凭证。
- Gateway 凭证由部署者配置并限定 shell_id。壳策略和动作限额在 Gateway 本地配置，Hub 保留镜像。Agent 的 capability 声明不扩大设备权限。
- 公开只读目录 `/v1/catalog` 不含 session、operator_id、任务或记忆。`/v1/state`、SSE、召唤/输入/反馈仅 operator 会话访问，并过滤到其允许的壳和自己的会话。
- WSS 客户端在 TLS 握手的 Authorization 头携带专用 token；不把 token 放 URL。握手后的 hello.id/role 必须匹配 token。浏览器不直接连接这条 WSS。
- 现场开放策略仍要求短期 operator 授权；readonly 只允许查询状态，不允许任何执行器动作，包括语音播放。展示模式选择不能绕过授权。
- 邀请注册限流，建议每邀请每分钟最多 5 次；错误码 RATE_LIMITED。具体部署限额可更严，不对外开放任意设备注册或任意代码执行。

## 3. HTTP 接口

响应成功按表中类型；失败统一 ErrorResponse。所有写请求除 operator-session 外携带 request_id。相同 principal + route + request_id + 相同 JSON 语义内容返回原结果，不重复产生效果；内容不同返回 IDEMPOTENCY_CONFLICT。幂等记录至少保存 24 小时。

| 方法 / 路径 | 身份 | 请求 `$defs` | 成功 | 响应 `$defs` |
|---|---|---|---|---|
| POST /v1/operator-session | 现场访问码 | OperatorLogin | 200 | OperatorLoginResult + cookie |
| POST /v1/agents | invite | RegisterAgent | 201 | RegisteredAgent |
| GET /v1/catalog | 公开 | 无 | 200 | Catalog |
| GET /v1/state | operator | 无 | 200 | Snapshot |
| POST /v1/sessions | operator | CreateSession | 202 | SessionResult |
| POST /v1/sessions/{session_id}/inputs | 会话主人 | SubmitInput | 202 | InputAccepted |
| POST /v1/sessions/{session_id}/release | 会话主人 | ReleaseSession | 202 | SessionResult |
| POST /v1/sessions/{session_id}/handoff | 会话主人 | HandoffSession | 202 | HandoffAccepted |
| GET /v1/sessions/{session_id}/memory | 会话主人/当前对应 agent | 无 | 200 | Memory |
| POST /v1/sessions/{session_id}/feedback | 会话主人 | Feedback | 200（保存后） | Memory |
| GET /v1/events?after={cursor} | operator | SSE | 200 | Event 流 |
| GET /v1/commands/{command_id} | 会话主人/对应 agent | 无 | 200 | ActionRecord |

URL 路径的会话/命令先做所有权检查再操作，不相信 JSON 自报身份。不存在对象返回 404；越权 403。`operator_id` 总由 cookie 取得，不能在 CreateSession 中指定。

`CreateSession` 包含 agent_id、shell_id、request_id。用户先查目录再按 ID 提交，不靠同名搜索猜目标。离线 503、占用 409、不兼容能力 422、设备未本地启用 403。至少具备双方共同的一种允许动作。

`SubmitInput` 限定文字 1–2000 字符，Hub 生成 input_id。一次会话最多一个进行中的输入，前一个任务终止前新输入返回 TASK_BUSY。语音桥转写后通过现场 Gateway 的受认证 `input.submit` 发送，同样进入该去重和调度路径。

`Feedback` 为明确确认的结构化偏好 `response_style`（brief / detailed），带 expected_version 和 update_id。解释/抽取自然语言反馈是 Agent 的应用逻辑；不把未经确认的全部对话自动当长期记忆。HTTP feedback 与 Agent `memory.update` 共用同一写入路径。

`HandoffAccepted` 返回 handoff_id、source_session_id、target_shell_id，表示已开始，不是迁移完成；完成后 `handoff.completed` 给新会话，失败有 `handoff.failed`。请求体不出现“旧壳停止了”的客户端断言。

## 4. WSS 连接与消息

端点 `/v1/connect`。Message 信封：`v, message_id, sent_at, type, payload`。控制帧 UTF-8 JSON 最大 16 KiB，超限拒绝；音频不进入此 JSON 通道。未知版本/类型/字段拒绝，不静默执行。

| 消息 | 方向 | 含义 |
|---|---|---|
| hello / welcome | Client→Hub / Hub→Client | role/id 绑定，返回 connection_id、心跳间隔 |
| heartbeat | 双向 | 当前 connection_id 存活；不表示动作成功 |
| shell.report | Gateway→Hub | physical_state=READY/FAULT/ESTOP、enabled、detail；不自报会话控制权 |
| session.offer | Hub→Agent 与 Gateway | Session、允许能力；只发给 Agent 的副本必须带 Memory，Gateway 副本省略 memory |
| session.ready | Agent/Gateway→Hub | 当前租约已安装/上下文就绪 |
| session.activate | Hub→Agent 与 Gateway | 双端 ready 后激活；Gateway 必须收到才接动作 |
| input.submit | Gateway→Hub | 按键/语音转写输入，绑定已授权会话 |
| input.text | Hub→Agent | input_id、内容及记忆版本；按该版本上下文推理 |
| input.finished | Agent→Hub | 输入任务结束：成功/失败；所有动作须已终止 |
| action.request | Agent→Hub→Gateway | 有限能力动作，包含命令/租约/顺序/截止时间 |
| action.accepted / started / completed / failed | Gateway→Hub→Agent | 实际执行状态，Hub 同步给 operator SSE |
| memory.update | Agent→Hub | 版本校验的偏好更新 |
| memory.updated / failed | Hub→Agent | 保存确认或失败；operator 得到脱敏版本事件 |
| session.revoke | Hub→Agent 与 Gateway | 禁止新动作，停止/清空当前队列 |
| session.stopped | Gateway→Hub | 已完成本地停止，没有未终止命令 |
| error | Hub→Client | 对应 reply_to 的 ErrorResponse |

hello 必须在握手后 5 秒内到达。相同角色/id 的第二连接返回 CONNECTION_EXISTS，不接管旧连接；旧连接关闭后可重连，但不恢复旧执行权。默认 heartbeat 1 秒、3 秒无 Hub 心跳时 Gateway 停止接新动作并按厂商方式停止。Agent 失联时 Hub 立即撤销其会话。

WebSocket 保证单连接有序不等于重连后恰好一次。根据 command_id 去重，根据 epoch/会话/截止时间拒绝迟到动作。Hub 重启清除在线状态，撤销所有未结束会话；新 Agent/Gateway 连接要重新握手并报告/核对设备状态。

Agent 通过握手后标 ONLINE，持有会话时标 BUSY。Gateway 握手后必须上报 shell.report，Hub 才能将壳标为可用；壳能力、入口和策略来自部署配置，报告不得扩大权限。FAULT/ESTOP/disabled 立即触发撤销；READY 不能覆盖活动会话或清除未核对的故障。故障后的 READY 仅可由现场完成停止确认与复位后发出，Hub 核对待终止命令再解锁，并发布 shell.state。

## 5. 壳、会话和控制权

壳 `state`：OFFLINE / IDLE / CONNECTING / ACTIVE / RELEASING / FAULT / ESTOP。动作执行进度单独由 ActionRecord 表达，不将“在线、空闲、具备急停、急停已按下”挤成一个 bool。

Session `state`：CONNECTING → ACTIVE → RELEASING → RELEASED；任一异常 → FAILED。Shell 的 `current_session_id` 与该状态保持一致。Agent status 为 OFFLINE/ONLINE/BUSY。

建立流程：
1. Hub 认证并原子锁定 Agent 与壳、检查兼容和本地 enabled，分配会话/新 epoch，发布 session.granted。
2. Hub 把 session.offer 发双端；Gateway 核对本地策略并安装租约，Agent 加载 memory_version。两端发 session.ready。
3. 5 秒内两端 ready，Hub 发 session.activate，并发布 session.active。否则撤销/释放，返回 CONNECT_TIMEOUT；不能显示已入住。
4. 租约硬上限默认 60 秒，不自动续期。它以 UTC expires_at 表达，Gateway 转为本地单调时钟截止时间；主机需校时，偏差超 500ms 拒绝 CLOCK_SKEW。心跳失效、急停或期限到达均可提前结束权限。

动作必须同时满足：会话 ACTIVE、agent/shell 与凭证绑定、epoch 当前、未过期、本地启用、未急停、动作允许。Hub 校验一次，Gateway 再校验一次。

迁移顺序：拒绝新输入/动作 → 等待或拒绝未提交的记忆写入并取得持久化版本 → revoke 原会话 → Gateway 确认 stopped 且命令终止 → 释放旧锁 → 新壳创建新 session/epoch 并加载已保存版本 → 双端 ready/activate → handoff.completed。

目标预检忙碌/离线时立即拒绝，原会话不变；一旦旧会话撤销，不自动回滚旧控制权。原 Gateway 失联/停止确认超时，迁移标 HANDOFF_BLOCKED，**不授予新会话**；即使远端租约到期，也不能仅凭墙钟认定物理已停止。需现场确认并通过本地恢复流程重新登记 IDLE。目标在释放后故障则 handoff.failed，Agent 保持无壳，允许操作者另行召唤。

普通 release 与迁移使用同一停止流程，但不创建新会话。故障/急停之后只能现场复位并重新本地启用；网络重连不自动清除 ESTOP。不要自动断电或回零，无制动关节的停止必须按厂商验证路径执行。

## 6. 动作与回执

首版能力：`display.text`（1–500 字符）、`speech.say`（1–500 字符，可选）、`arm.gesture`（nod/wave/point_left/point_center/point_right；repeat 1–3）。Gateway 在注册快照中声明实际 capabilities 和本地 allowed_actions，实际使用取双方交集。

预设轨迹封装在现场，远端 Agent 选择动作和顺序，不生成任意代码、关节角或自由坐标。所有运动参数上限由本地配置；Schema 验证只是第一层，设备仍须检查自己的更严格约束。

同一 session 的 seq 从 1 连续增长，最多一个未终止 command；下一动作等上一 completed/failed。超序返回 OUT_OF_ORDER/COMMAND_BUSY。`expires_at` 不超过租约截止且距发送最多 5 秒，用于启动有效期；运行中的动作由本地 watchdog 与租约停止机制约束。

Gateway **执行前**持久化 `(session_id, command_id, canonical_payload_hash)`；同 ID 同内容返回已有状态，不能再次运动；同 ID 不同内容 IDEMPOTENCY_CONFLICT。检查幂等命中先于 seq 比较，但必须先验证调用身份。网关重启后 EXECUTING/ACCEPTED 未确认结果标 UNKNOWN，进入核对/停止流程，不自动重放。记录至少保留 24 小时。

ACCEPTED 不等于 COMPLETED。`action.completed` 必须含 evidence=`device_ack`（屏幕/音频完成回执）或 `controller_feedback`（机械臂控制器确认）。只有“命令发出”而无结束反馈时，用 failed/UNVERIFIED，并保留观察信息；不得用动画计时伪造完成。

动作超过配置执行上限（演示默认 10 秒）先本地停止，确认结果前标 UNKNOWN；不可盲目发新 command 重试。Agent 可 GET command 状态，必要时请求用户重新授权。不是对真实物理动作作严格 exactly-once 保证。

## 7. 记忆

MVP 权威存储在 Hub SQLite，键 `(operator_id, agent_id)`；不能只按 agent_id 混合不同访客偏好。记忆含 memory_version（从 0）、preferences.response_style、updated_at。完整聊天记录与家庭记忆库自动双向同步不在 v1。

只有 Hub 写库，Agent 和 operator 提交 update；Gateway 只接最小执行上下文。Hub 比较 expected_version：成功原子写入并加一，返回 memory.updated；冲突返回 MEMORY_CONFLICT，客户端先重新读取，不盲覆盖；写入失败 MEMORY_WRITE_FAILED，版本不变。

update_id 在记忆作用域内去重至少 24 小时，payload 包括 expected_version。重复成功请求返回原保存结果（即便此后记忆版本更高），不再次加一；内容不同报 IDEMPOTENCY_CONFLICT。写入事务提交前绝不发成功事件。移交中的旧会话更新拒绝 SESSION_NOT_ACTIVE，防止旧端覆盖新壳上下文。

发给 Agent 的 session.offer 含持久化 Memory，发给 Gateway 的副本省略 memory；后续 input.text 含当前版本。若 Agent 的缓存版本不一致，先读取/更新记忆再处理；不能自行沿用旧状态。记忆 GET 对应 Agent 专用 token 也可访问，但仅限自身正在参与的会话。

EvoMap 经验发布为未来 opt-in 扩展，不自动上传个人偏好/对话。语音原始音频默认不持久化，转写仅用于当前任务；公共事件不包含转写/记忆正文。

## 8. 快照与事件恢复

Snapshot：`v, cursor, generated_at, mode, agents, shells, sessions, commands`。没有永久密钥、访问码或记忆正文。GET /v1/state 返回的数组和 cursor 必须取自同一一致性时点。

Event：`v, event_id, at, mode, type, payload`，SSE 的 id 与 event_id 一致。首次快照后 `/v1/events?after=cursor` 补发该 cursor 之后的已提交事件再继续订阅，避免快照和订阅之间漏事件。

前端按 event_id 去重，不要求全局序号连续（权限过滤后可能跳号）。Hub 日志保留最近 10 分钟；游标失效/换 stream/重启时发送 stream.reset 并关闭流，客户端重新取快照。断开立即禁控；本版恢复路径统一重新取快照，避免对未保存 UI 状态做增量猜测。

模式：LIVE 真服务、SIMULATED 联调、LOCAL_SHADOW 显式本地预载、REPLAY 录制回放。当前首版只实现 LIVE/SIMULATED，后两者是预留展示值且控制禁用；实现前不能宣称影子演示已可用。公开 catalog 不包含模式切换权限。

## 9. 错误约定

ErrorResponse：`error.code, message, retryable, request_id`；WSS error 另有 reply_to。message 给人读，程序分支只判断 code。

| HTTP | 代码 | 处理 |
|---|---|---|
| 400 | INVALID_MESSAGE / UNSUPPORTED_VERSION | 修输入或版本，不重试动作 |
| 401/403 | UNAUTHORIZED / FORBIDDEN / SHELL_DISABLED | 重新取得授权或现场启用 |
| 404 | NOT_FOUND | 刷新目录 |
| 409 | NAME_TAKEN / AGENT_BUSY / SHELL_BUSY / TASK_BUSY / COMMAND_BUSY / CONNECTION_EXISTS | 状态改变后新请求 |
| 409 | IDEMPOTENCY_CONFLICT / OUT_OF_ORDER / STALE_LEASE / SESSION_NOT_ACTIVE | 查状态，禁止盲重放 |
| 409/500 | MEMORY_CONFLICT / MEMORY_WRITE_FAILED | 重新读版本 / 同 update_id 核对写结果 |
| 409 | HANDOFF_BLOCKED | 现场核对旧壳停止，无自动双活 |
| 422 | CAPABILITY_UNSUPPORTED / ACTION_NOT_ALLOWED / CLOCK_SKEW | 修配置、能力或时钟 |
| 429 | RATE_LIMITED | 退避，仅对无副作用登记或同幂等请求重试 |
| 503/504 | AGENT_OFFLINE / SHELL_OFFLINE / CONNECT_TIMEOUT | 显示不可用，不假成功 |
| 动作失败 | EXPIRED / ESTOP / DRIVER_ERROR / UNVERIFIED / UNKNOWN | 停止或核对硬件；不自动新 ID 重试 |

retryable=true 只表示允许检查状态后用**原幂等键**重试；绝不等于可重复物理动作。

## 10. 演进与校验边界

三端先走读 [场景样例](../protocol/examples/README.md)。Schema 可验证结构，校验工具可检查样例时序；实际权限、断网制动、数据库事务与硬件行为必须按 [ACCEPTANCE](ACCEPTANCE.md) 另验。

v1 拒绝未知字段，不能随意添加字段而不升级接收端；实验字段先留在应用内部。0.x 修订发布时同步本文件、Schema、样例并通知三人；不兼容 wire 改动改 v。物理停止能力、网络时延和容错期限以实测及厂商约束校准。
