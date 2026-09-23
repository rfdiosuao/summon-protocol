# Passport Wi-Fi 语音通道

Passport 通过 2.4GHz Wi-Fi 直连 SUMMON 媒体桥，不需要 USB 电脑转发音频。麦克风音频经 WSS 上传，服务器调用语音识别，再提交给铭牌对应 Agent。Agent 的动作通过原 Hub 会话发送到部署者绑定的电脑；执行电脑必须运行 SUMMON 客户端。服务器将回复合成为语音，经同一连接回到 Passport。

当前 MVP 由服务器上的模型 Agent 负责思考和发出动作；电脑只运行 SUMMON Gateway 客户端，负责在本机执行并回传结果。铭牌 `SMN-CCM7-P6RF` 对应这个云端 Agent，媒体桥按部署者配置把它的动作送到绑定的电脑。语音识别只负责转文字。

## 设备操作

- 开机自动连接已保存的 2.4GHz Wi-Fi 和云端；长按下键可触发重连。
- 双击确定：取消当前录音或播放。
- 确定：开始说话，再按结束；静音自动提交，单段最长八秒。
- 上下短按：调整音量。联网后可拔 USB；执行电脑上的客户端仍须在线。

铭牌不是凭证。部署者给每台设备写入独立的 `device_token`，服务端绑定允许的铭牌和 `target_shell_id`。本版本采用 USB 首次注入凭证，不把凭证硬编码进公开固件。更换目标电脑或扩大铭牌范围由部署者修改私有绑定。

精简固件不再启动设备热点、Wi-Fi 扫描或 `192.168.4.1` 网页。现有 NVS 中的 Wi-Fi、音量、铭牌和设备 token 在只刷应用分区时保留。需修改网络或首次配置时，用 USB 串口发送一行私有配置（不要放到公开日志或仓库）：

```text
SUMMON1 {"type":"network.configure","ssid":"<2.4GHz SSID>","password":"<Wi-Fi password>","nameplate":"SMN-XXXX-XXXX","device_token":"<private device token>"}
```

字段可省略以保留原值；设备返回 `network.configured` 并自动重启。日常语音全走 Wi-Fi，不依赖 USB。只有维护配置需要 USB。

## Agent 可调用的接口

基址 `https://summon.entermodetwo.com`。所有接口使用 `Authorization: Bearer <sender_token>`，凭证由部署者私密发放，限定到一台 Passport。它不同于设备连接 token、Agent token、注册邀请或控制台访问码。

`GET /v1/passport/status`：返回设备在线/忙碌状态、绑定电脑与默认铭牌。

`POST /v1/passport/transcribe`：请求体直接放 WAV 二进制，`Content-Type: audio/wav`。只接受 16 kHz、单声道、16-bit PCM、0.1–8 秒；返回 `{"text":"识别文字","executed":false}`，不会自动执行。接入 Agent 根据文字思考后再决定动作。模型服务地址、模型和 API key 只配置在服务器。

`POST /v1/passport/messages`：

```json
{"request_id":"unique-message-id","text":"你好，任务已完成。","mode":"announce"}
```

`announce` 直接显示与播报，不执行命令；`agent` 把文字交给绑定铭牌的 Agent，通过绑定电脑执行，再在 Passport 播报回复。最长 250 字符。同一 request_id、同一内容重试只返回原记录；内容改变返回 409。设备离线返回 503，忙碌返回 409，不积压过期命令。

`GET /v1/passport/messages/<request_id>`：查询状态。202/ACCEPTED 只表示接收，`COMPLETED` 要求设备 `play.done`；`FAILED`、`CANCELLED`、`UNKNOWN` 不能当成成功，也不能自动换编号重放命令。服务重启会把未完成记录标记 UNKNOWN。

远端播报先进行 `remote.begin` / `remote.ready` 空闲握手；录音和双击返回会取消正在处理的请求。断线取消任务，不恢复旧录音、不重放旧命令。网络媒体帧使用 turn 和 seq，播放采用有界缓冲和滑动窗口，避免每块音频等待一次网络往返。

## 部署与经验声明

媒体进程 `python -m hub.passport_network --config /etc/summon/passport-network.json` 监听本机 8842，由现有 HTTPS 入口转发 `/v1/passport/`，不在媒体服务本机执行 shell 命令。服务器 Agent 由 [systemd 单元](../deploy/summon-passport-agent.service)运行，读取私有模型配置和现有 Agent 身份；其决策走 Hub 会话和 Gateway 回执。旧的本地 EvoX Agent 必须停用，避免同一 Agent token 双连；Gateway 客户端保持在线。

私有配置包括 `hub_url`、`origin`、`database`、`speech` 和 `devices`。每台设备配置 `device_token`、`sender_token`、`operator_code`、`default_plate`、`allowed_plates`、`target_shell_id`。操作者凭证保存在服务器，不下发到设备；其绑定范围由部署者确定。

原始音频只在内存中用于识别/播放，不写入云端音频档案。用户指令进入 Hub 会话，命令及有界输出沿原有回执机制沉淀到当前账号的云端执行经验。媒体库保存消息状态、会话编号和播放统计；不得将敏感配置或私有日志推送 GitHub。公开 API 文档与实际设备验收分开，模拟测试不代表电池供电或 Wi-Fi 实物通过。

## 2026-09-23 验证记录

服务器识别/合成 API 实测成功，公网 `/v1/passport/transcribe` 返回 HTTP 200；服务器桥通过真实 Hub 调用已授权 Windows 电脑执行时间查询，获得实际执行结果。媒体、鉴权、消息幂等和 Skill 相关 12 项测试通过。完整测试套件在原有 `GatewayIntegrationTests` 的 teardown 等待挂起，已中止；不声明全套通过。

固件 `be6bc6a` 的静态检查与 ESP-IDF 构建通过，应用区已刷写且哈希校验通过。首版手机保存超时，实测当时空闲堆约 11 KB；已释放闲置 BLE 内存、缩减 Wi-Fi 缓冲并先应答保存请求再切换频道。修复版重启后空闲堆约 84 KB，音频初始化正常。手机保存、WSS 真机连接、拔 USB 后录音与远端播报仍需实物复验。

## 2026-09-23 云端 Agent MVP

服务器已启用 `summon-passport-agent.service`，复用铭牌 `SMN-CCM7-P6RF` 的现有 Agent 身份与私有模型配置；本地 EvoX Agent 已停止，电脑 Gateway 保持运行。通过真实 Hub 会话发送“查看电脑当前时间”，云端模型下达动作、电脑执行、云端返回时间均已实测。Passport 旧固件的播放链路仍出现过 ACK 超时；精简版固件 `526d807` 已通过静态检查和 ESP-IDF 5.5.3 构建，但尚待真机验收，不能把服务器测试算作设备语音成功。
