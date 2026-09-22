# 开发时读取的公共契约

仓库：https://github.com/rfdiosuao/summon-protocol

读取并记录所用 commit：

- docs/PROTOCOL.md 与 protocol/summon.schema.json：唯一线协议依据。
- docs/ADAPTER.md：Gateway 与 Agent 生命周期、认证和板桥行为。
- protocol/examples/08-device-bridge.json：板侧消息。
- tools/summon_device_ref.py：可执行样板，自检使用 --selftest。
- docs/EXPERIENCE.md：结果入库、会话内经验检索、版本隔离。
- hub/app.py：当前实际提供的 HTTP 与 WSS 路由。

当前 Hub 对外是 `/v1/connect`，使用 agent 或 gateway 身份。板侧 `/device` 是现场 Gateway 应实现的端点，不能把公开 Hub 的 `/device` 当成现成服务。模拟 DemoFleet 不是通用硬件 Gateway；USB/BLE/厂商 SDK 驱动需要针对设备实现。

请求 `GET /healthz`，解析 JSON 后读取 `mode` 字段（不是请求 `/healthz.mode`）。用 `/v1/catalog?details=1` 获取 CatalogDetailed；details 仅接受 0/1，省略等于 0。HTTP 响应按具名 `$defs` 校验。

HTTP 与 WSS 握手必须携带非空真实产品 User-Agent，例如 `SUMMON-Adapter/0.1`；空 UA 和部分默认 UA 曾被边缘拦截，非空也不保证放行。先检查状态和 Content-Type，再尝试解析 JSON 并检查结构：`cloudflare_error` 或 `error_code=1010` 表示边缘错误；通过 ErrorResponse 校验的 `error.code` 才是 Hub 错误。JSON 格式本身不能证明来自 Hub。未知结构保留脱敏摘要，不直接索引 error.code。1010、401/403 不自动重试，不关闭 TLS 校验。

最小请求：`curl -H "User-Agent: SUMMON-Adapter/0.1" -H "Accept: application/json" https://summon.entermodetwo.com/healthz`。

`wss://summon.entermodetwo.com/v1/connect` 是 GET Upgrade 的 WebSocket 入口，不接受 POST。仓库固定 websockets==13.1：使用 `websockets.connect(url, extra_headers={"Authorization": "Bearer " + token}, user_agent_header="SUMMON-Adapter/0.1")`；不要照搬其他版本的 additional_headers。InvalidStatusCode 可读 status_code/headers，但没有响应体。握手失败时保留状态和 ray id；必要时用相同 UA、Authorization 发一次普通 GET /v1/connect 辅助排障，并校验 HTTP 响应结构；该新请求只能作为辅助证据，不能证明上次握手的根因。有效凭证的普通 GET 也不能建立会话。禁止把 403 单独判成 token 无效。

operator 的 POST 写接口需要精确 `Origin: https://summon.entermodetwo.com`；Origin 校验先于凭证，缺失/不匹配返回 403 FORBIDDEN 并说明 Origin 原因。Agent 注册使用 invite，不要求 Origin；Gateway 使用 WSS，不通过 operator 登录接口冒充用户。

从技能目录回到仓库根目录后验证（Windows 也可直接切换到仓库绝对路径）：

```sh
cd ../..
python -m pip install -r protocol/requirements.txt
python tools/validate_contract.py
python tools/summon_device_ref.py --selftest
```

复用 HTTPS 连接池和每个身份的 WSS 长连接；重连从 1 秒指数退避到 30 秒并抖动，成功 welcome 后稳定 10 秒才重置，401/403 停止自动重试。撤销后不再发送新的动作、记忆更新或 input.finished，迟到输入本地丢弃。Agent 可通过 `/v1/agents/me` 核验当前握手，通过命令查询接口核对自己的旧命令；这些都不恢复旧租约。详细时序以 PROTOCOL 为准。

Gateway 使用自己的 shell token 与 shell_id，经 hello/welcome、心跳、shell.report、offer/ready/activate 获得会话后才执行。不要用 Agent 的注册接口登记设备。缺凭证时先做本地验证，提供部署者需要配置的 shell/设备绑定清单。

设备桥使用 device.hello、device.heartbeat、device.command、device.result、device.stop、device.stopped。字段、错误与枚举以 Schema 为准。每秒心跳，3 秒未收到网关心跳进入停止流程；大数据另走有界媒体通道，不能将音视频无限塞进 16 KiB 控制帧。

驱动适配必须区分“发送成功”“设备接收”“操作完成”。渲染/播放/运动真实完成才发送 COMPLETED；SDK 没提供相应证据时标 UNKNOWN 或降低能力承诺。controller_feedback 也只证明控制层状态，不证明抓取任务成功。

经验由 Hub 接收终态后保存，设备不必拥有云数据库。部署时配置 device_profiles[ shell_id ] 的 model、firmware、adapter_version；更改版本同步指纹。Agent 激活后使用其自己的 token 读取 `/v1/sessions/{sid}/experiences`；Gateway token 不能冒用该接口。当前经验限定同账号、同设备、同版本与模式，不等于全网公开或自动训练。

完成后运行契约校验和参考端自检，再运行真实适配器与所选设备。使用受控演示动作，不把模型输出直接当 shell 命令、SDK 函数名或原始电机控制帧。
