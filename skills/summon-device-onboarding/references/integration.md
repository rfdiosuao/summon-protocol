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

Gateway 使用自己的 shell token 与 shell_id，经 hello/welcome、心跳、shell.report、offer/ready/activate 获得会话后才执行。不要用 Agent 的注册接口登记设备。缺凭证时先做本地验证，提供部署者需要配置的 shell/设备绑定清单。

设备桥使用 device.hello、device.heartbeat、device.command、device.result、device.stop、device.stopped。字段、错误与枚举以 Schema 为准。每秒心跳，3 秒未收到网关心跳进入停止流程；大数据另走有界媒体通道，不能将音视频无限塞进 16 KiB 控制帧。

驱动适配必须区分“发送成功”“设备接收”“操作完成”。渲染/播放/运动真实完成才发送 COMPLETED；SDK 没提供相应证据时标 UNKNOWN 或降低能力承诺。controller_feedback 也只证明控制层状态，不证明抓取任务成功。

经验由 Hub 接收终态后保存，设备不必拥有云数据库。部署时配置 device_profiles[ shell_id ] 的 model、firmware、adapter_version；更改版本同步指纹。Agent 激活后使用其自己的 token 读取 `/v1/sessions/{sid}/experiences`；Gateway token 不能冒用该接口。当前经验限定同账号、同设备、同版本与模式，不等于全网公开或自动训练。

完成后运行契约校验和参考端自检，再运行真实适配器与所选设备。使用受控演示动作，不把模型输出直接当 shell 命令、SDK 函数名或原始电机控制帧。
