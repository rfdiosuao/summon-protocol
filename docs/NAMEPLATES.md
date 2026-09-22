# 铭牌与电脑客户端：已实现接口

2026-09-22。后端实现位于 hub/nameplates.py，消息定义见 [nameplate.schema.json](../protocol/nameplate.schema.json)。旧 Catalog、注册响应与 wire v1 不变。首次加载为已有 Agent 分配铭牌；新注册同事务分配；SQLite 保证 code 和 agent_id 唯一，重启保留。

## 用户操作

Agent 适配器注册后必须自动查询 `GET /v1/agents/me/nameplate`，使用自己的 agent_token，校验响应的 `agent.agent_id`，并把 `code` 展示给用户。注册返回值仍是 `agent`、`agent_token`、`address`，不增加字段以免破坏旧客户端。取铭牌失败只重试查询，不重复创建 Agent；重启复用持久化身份。铭牌可以分享，邀请凭证和 Agent token 不可公开。

双击桌面启动入口，A 申请配对 → B 打开 [设备授权页](https://summon.entermodetwo.com/assets/device.html) → 使用团队访问码登录 → 输入终端配对码 → 核对设备与允许能力 → 确认授权。回到终端，L 查看铭牌目录，M 输入铭牌，核对身份后 C 连接；双端握手完成显示 ACTIVE 后，T 输入任务。R 停止并释放后可切换铭牌。Q 停止退出。输入模式下 Q/B 为文字，Enter 提交、Esc 返回。

Windows 交互终端使用分区 TUI；Linux/macOS 或重定向 stdin 仍运行已有日志网关，不提供 Windows 键盘交互。已有 `--tui --log-file` 启动命令不变；新增 rich 依赖在 gateway/requirements.txt。

铭牌格式为 SMN-XXXX-XXXX，可省略横线或使用小写，8 位 Crockford 字符不含 I/L/O/U。铭牌公开可分享，但不是控制密码。一个 Agent 同时只控制一台设备；一个设备仅一个控制会话。

## 权限和生命周期

- gateway token 只代表所属 shell。设备授权通过已登录 operator 审批，权限取设备实现、部署白名单、Agent 能力和审批能力的交集。
- 配对请求有效 5 分钟，配对码仅用于本地窗口和用户输入，不放 URL；审批前先预览设备，审批时核对 shell_id 与能力。领取授权必须使用创建请求的 Gateway 凭证。
- device_grant 有效 8 小时、仅限一个 shell 和 operator，电脑只存内存。Hub 的配对和授权也只存内存，服务重启后全部失效，须重新配对；铭牌和执行经验不丢失。服务器只保留授权散列，5 分钟内另保留受所属 Gateway 限制的领取恢复材料。
- 会话租约仍为 60 秒，到期自动释放。授权撤销/到期阻止新输入与动作，并释放相关会话。重连不自动恢复旧控制权。停止和旧结果补传不要求有效 device_grant。
- 机器创建/输入请求同时携带 `Authorization: Bearer <gateway_token>` 与 `X-Summon-Device-Grant: <device_grant>`；浏览器操作使用 operator cookie 与精确 Origin。新增机器写路由逐条豁免 Origin，不豁免整个前缀。
- POST 统一带 request_id。新模块的幂等响应保存至多 24 小时，配对/领取仅保留到配对过期；Hub 重启会清除该模块幂等缓存，同时清除全部设备授权。因此旧创建/输入请求无法在重启后凭旧授权再次执行。旧协议接口的持久化幂等规则不变。
- 经验继续按 operator、Agent、设备、版本及模式保存。铭牌查询与 Gateway 状态接口不返回记忆、设备授权 token 或其他 operator 数据。

## HTTP 契约

请求及响应类型均来自 nameplate.schema.json；InputAccepted 复用 summon.schema.json。无请求体的 GET 不携带 request_id。JSON 请求未知字段拒绝。

| 方法与路径 | 认证 | 请求 → 响应 |
|---|---|---|
| GET /v1/agents/me/nameplate | Agent token | → Nameplate |
| GET /v1/nameplates | operator / Gateway | → NameplateDirectory |
| GET /v1/nameplates/{code} | operator / Gateway | → Nameplate |
| POST /v1/gateway/pairings | Gateway | NameplateRequest → 201 PairingCreated |
| GET /v1/gateway/pairings/{pid} | 所属 Gateway | → PairingStatus |
| POST /v1/device-pairings/preview | operator + Origin | PairingPreview → PairingDevice |
| POST /v1/device-pairings/approve | operator + Origin | PairingApproval → DeviceApproval |
| POST /v1/gateway/pairings/{pid}/claim | 所属 Gateway | NameplateRequest → DeviceGrant |
| POST /v1/gateway/sessions | Gateway + device_grant | DeviceConnect → 202 DeviceSession |
| GET /v1/gateway/session | Gateway | → DeviceSession（仅本 shell，无会话为 null） |
| POST /v1/gateway/sessions/{sid}/inputs | Gateway + 会话原授权 | DeviceInput → 202 InputAccepted |
| POST /v1/gateway/sessions/{sid}/release | 所属 Gateway | NameplateRequest → 202 DeviceSession |
| POST /v1/device-authorizations/{gid}/revoke | 授权所属 operator + Origin | NameplateRequest → DeviceRevoked |

例如创建会话：`{"request_id":"connect_1","code":"SMN-7K2M-9Q4X","agent_id":"agent_example"}`。示例号码不是生产 Agent。Hub 重新解析铭牌并核对预览 agent_id，且在同一锁内检查忙碌、授权和能力；202 不表示已经 ACTIVE。

错误沿用 ErrorResponse：401 UNAUTHORIZED、403 FORBIDDEN/SHELL_DISABLED、404 NOT_FOUND、409 IDEMPOTENCY_CONFLICT/SHELL_BUSY/AGENT_BUSY/TASK_BUSY/SESSION_NOT_ACTIVE、422 CAPABILITY_UNSUPPORTED/ACTION_NOT_ALLOWED、429 RATE_LIMITED、503 AGENT_OFFLINE/SHELL_OFFLINE。格式错误 400 INVALID_MESSAGE。不要将权限或占用错误误判为断网。

## 仍未实现

自定义铭牌、用户改名、公开的铭牌注销/冻结接口、NFC/二维码扫描、自动续租、跨平台交互 TUI、任意 Agent 多设备并发。已实现的是固定铭牌和设备授权撤销，不能把两种撤销混为一谈。固件 SoftAP 配网与实际按键仍需按具体板型实现及实测。

## 验证记录

2026-09-22：服务器 Python 3.12 环境运行 28 项测试通过。公网完成设备配对审批、铭牌查询、电脑终端执行、云端经验持久化确认、会话释放与授权撤销，见 [联调记录](nameplate-cloud-smoke-20260922.json)。使用规则模拟 Agent 与真实本机终端，未测试机械臂；总场景时长含登录和轮询，不能当作动作延时。
