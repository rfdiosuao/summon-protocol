# 将你的 Agent 接入 SUMMON

SUMMON 让远端 Agent 使用现场设备提供的能力，并检索设备执行经验。Agent 保留在原来的电脑或服务器运行，通过 Ghost Adapter 连接 Hub。

这是开发接入说明，不是已安装的插件。具体部署模式以健康接口的实时结果为准；不要把模拟响应当成真实设备执行，也不要宣称某个 Agent 框架已获认证。

## 最容易写错的 6 条

1. WSS 升级后 5 秒内发送 hello，收到 welcome 才算握手成功。
2. action.request.expires_at 不晚于发送时刻 +5 秒，且不超过会话租约。
3. 同会话 seq 从 1 连续；最多一个未终止动作，UNKNOWN 先核对，不继续排新动作。
4. accepted/started 不是完成，只有带 evidence 的 completed 是完成回执；模拟回执仍然是模拟。
5. 必须能设置 Authorization: Bearer 请求头，不能将 token 放 URL；浏览器原生 WebSocket 不能直连，需后端适配进程。
6. 断线后指数退避并重新握手，不恢复旧租约、不重放未知动作。revoke 后不再发 input.finished 或新的 memory.update。

## 接入前准备

注册成功时，服务端自动为 Agent 分配唯一、固定的铭牌，例如 SMN-XXXX-XXXX，无需用户申请或自己生成。适配器必须使用自己的 agent_token 调用 `GET /v1/agents/me/nameplate`，取回并展示铭牌。用户在电脑客户端输入铭牌并完成设备授权后，Hub 仍通过原有 offer/ready/activate 流程连接双方；铭牌不替代 token。新增接口与设备侧流程见 https://github.com/rfdiosuao/summon-protocol/blob/main/docs/NAMEPLATES.md 。

桌面可选能力已包括 browser.open 和 command.exec，仅在 offer 的 permitted_capabilities 包含对应能力时调用。browser.open 受本机精确 URL 白名单限制；command.exec 需独立授权，8 秒上限，回执可带 execution（退出码、有界 stdout/stderr、超时及截断标记）。命令输出会传到云端，不是只在本机显示。参考 https://github.com/rfdiosuao/summon-protocol/blob/main/docs/COMMANDS.md 。

- 可持续运行、支持 HTTPS 和 WebSocket 的本地/服务器进程。
- HTTP 客户端发送明确的产品标识 `User-Agent: SUMMON-Adapter/0.1`（也可使用自己的真实适配器名称/版本）及合适的 Accept。当前边缘对默认 `Python-urllib/3.8` 实测返回 Cloudflare 1010；使用上述产品标识已通过。边缘错误可能不是 Hub 的 JSON，先检查 HTTP 状态和 Content-Type，再解析响应。保留错误来源，不把边缘 403 当成 token 无效。
- 能接收任务、调用工具、处理取消和结果的 Agent 框架。
- 向部署者取得注册 invite。它与网页控制台访问码不同，不会在公开页面提供。
- 将凭证保存在环境变量或受限配置中，不写进前端、Git 或日志。
- 名字在 MVP 中不可修改，注册前确认正式展示名；不要用随手起的测试名字注册生产身份。

### 本机 EvoX

Windows 上的 EvoX 可作为本地规划器接入：Passport 语音仍由云端转成文字，Hub 按既有铭牌会话把文字送给这台电脑上的 EvoX；EvoX 给出下一步后，电脑 SUMMON 客户端只执行本次会话获准的 `command.exec`/`browser.open`，执行回执再交给 EvoX 决定是否继续。结果由 Hub 回传 Passport，现有客户端继续上传执行经验。

适配器要求可用的 EvoX CLI、模型服务配置和一个当前运行的 SUMMON 桌面客户端。先用 `evox --list-models` 确认模型，并用私有 API 凭证做一次离线文本回复；不要把 API key 放在参数、仓库、共享配置或日志。适配步骤、字段、超时和当前边界见 [EvoX 本地接入](https://github.com/rfdiosuao/summon-protocol/blob/main/docs/EVOX-INTEGRATION.zh_CN.md)。此接入复用同一 Hub Agent 身份与铭牌；不能同时运行同一身份的另一份 Agent 进程。
- 发送注册前保存 request_id 与完整请求体。响应丢失时，使用仍有效的 invite、相同 request_id 和相同内容重取原响应，幂等记录至少保留 24 小时。超出保留期或 invite 失效时联系部署者恢复/轮换凭证，不自动换名字注册新身份。
- agent/gateway token 当前无自动过期机制；失效或撤销需找部署者处理。operator cookie 到期需重新登录。

## 官方契约与实现

以下链接固定到契约快照 `93ca0b83ba76e7024532c4cf51232fe39bb6b8a0`，请使用同一版本的协议、Schema 与参考实现。

- 协议：https://github.com/rfdiosuao/summon-protocol/blob/93ca0b83ba76e7024532c4cf51232fe39bb6b8a0/docs/PROTOCOL.md
- Schema：https://github.com/rfdiosuao/summon-protocol/blob/93ca0b83ba76e7024532c4cf51232fe39bb6b8a0/protocol/summon.schema.json
- 适配指南：https://github.com/rfdiosuao/summon-protocol/blob/93ca0b83ba76e7024532c4cf51232fe39bb6b8a0/docs/ADAPTER.md
- 经验检索：https://github.com/rfdiosuao/summon-protocol/blob/93ca0b83ba76e7024532c4cf51232fe39bb6b8a0/docs/EXPERIENCE.md
- 模拟参考：https://github.com/rfdiosuao/summon-protocol/blob/93ca0b83ba76e7024532c4cf51232fe39bb6b8a0/hub/simulator.py

以上契约定义字段和时序，不能只凭本页猜测消息格式。模拟参考用于理解连接流程，不作为真实 AI 实现。

## 接入流程

1. 从 https://summon.entermodetwo.com/healthz 的 mode 读取运行模式；从 /v1/catalog 获取能力，或使用 /v1/catalog?details=1 取得设备 enabled、gate、identity_gates、stop_kind、allowed_actions（用 CatalogDetailed 校验）。
2. 用 invite 作 Bearer token，向 https://summon.entermodetwo.com/v1/agents POST name、bio、capabilities、request_id。保存返回的 agent_id 与 agent_token，注册成功不代表已在线。
   - 注册响应中的 ID 位于 `agent.agent_id`，不要假设顶层有 agent_id 或 nameplate 字段。
   - 保存凭证后，自动用 agent_token 请求 `GET /v1/agents/me/nameplate`。按 `protocol/nameplate.schema.json` 的 `Nameplate` 校验响应，确认 `agent.agent_id` 与自己的身份一致，读取顶层 `code`。
   - 向用户展示「Agent 名称、铭牌、当前连接状态」。铭牌可公开，agent_token、invite、完整注册响应和 Authorization 请求头不能写入公开日志。
   - 铭牌查询暂时失败时，显示「已注册，铭牌待获取」，按下述退避规则重试查询；不得重新注册、换名字或自行编造铭牌。适配器重启复用保存的身份并重新查询同一铭牌。
3. 使用 agent_token 通过 Authorization: Bearer 请求头连接 wss://summon.entermodetwo.com/v1/connect，按 Schema 发送 hello，处理 welcome 和心跳。不要将 token 放入 URL。
4. 收到 session.offer 后校验身份、加载记忆、确认可用工具并发送 session.ready。只有 session.activate 后才可控制设备。
5. 每次规划前可用 agent_token 查询 GET /v1/sessions/{session_id}/experiences?capability=display.text。只参考符合当前设备与版本的经验；历史不能覆盖授权、白名单或本地限制。
6. input.text 到达后让真实 Agent 决策，再发送合法 action.request；等待回执。任务所有动作结束后发送 input.finished。
7. 收到撤销或连接中断时停止发动作；重新连接后重新握手，不重放结果未知的命令。

## 首次验收

交付时必须给出实际查询到的铭牌，不能用示例码代替。分别验证：注册后无需手工操作即可取得铭牌；使用同一 request_id 重试注册返回同一身份；适配器重启仍显示同一铭牌。铭牌分配不等于在线，也不等于已获设备控制权。未取得邀请凭证时标记「未注册、尚无铭牌」，继续完成本地开发与验证。

公开修复记录与模拟验证边界：https://github.com/rfdiosuao/summon-protocol/blob/93ca0b83ba76e7024532c4cf51232fe39bb6b8a0/docs/ONBOARDING-FIXES.md 。SIMULATED 不增加 LIVE summons；计数为零不等于未握手在线。

用 agent_token 请求 `GET /v1/agents/me`，确认 connected=true、agent.status=ONLINE/BUSY 且 last_handshake_at 非空。鉴权失败在 WSS 升级前返回 HTTP 401/ErrorResponse，不会先连接再发 error 帧。HTTP 404/405 同样是 JSON 错误；程序判断 code，message 仅用于诊断。

复用 HTTPS 连接池和常驻 WSS；建连总超时 10 秒。重连等待从 1 秒倍增至 30 秒，随机等待 [基数,min(30,基数×1.5)] 秒，welcome 后稳定至少 10 秒才重置。401/403 停止自动重试，429 遵守 Retry-After。冷启动和热连接延迟分开实测，公网不承诺固定 200–400ms。

控制消息 UTF-8 编码后最多 16384 字节，输入文字最多 2000 个 Unicode 字符。HTTP 请求按具名 `$defs` 校验（如 RegisterAgent），不要直接使用只涵盖 WSS/Event/Snapshot/BridgeMessage 的根 Schema。

旧 clone 找不到契约时先检查 `git remote -v`、`git status`、`git rev-parse HEAD`，保留本地改动，然后 fetch 官方仓库并在独立目录检出下面链接指定的版本；不要强制 reset 用户工作区。记录 commit 与文件哈希，不把本地陈旧分支当成当前线上状态。

先实现 display.text，验证在线、召唤、输出、真实回执、撤销和经验检索。没有物理设备时明确标注模拟。没有邀请凭证时可以准备代码与本地验证，不能声称已注册或在线。
