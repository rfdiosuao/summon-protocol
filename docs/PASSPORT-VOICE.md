# Passport 语音到电脑执行

本应用让 Passport 成为 Agent 的麦克风、屏幕和喇叭，电脑保留现场执行器职责。模型不下载到 ESP32；输入来源与受控设备分开，因此不绕过一个 Agent 同时只能控制一个 Shell 的约束。

## 当前实现

`Passport 麦克风 → USB JSONL 音频桥 → 电脑网关 → SiliconFlow STT → SUMMON 云端 Agent → Hub → 已授权电脑执行 → 执行回执/经验 → Passport 屏幕和喇叭`。

- 固件：FoloToy ESP32-C3，使用官方 BSP、16 kHz / 16 bit 单声道，检测到说话后的约 640 ms 静音自动结束，也可再按确定结束；单次最长 8 秒；分块发送，不在板上缓存整段音频。
- 语音识别：`XingChenAGI/XingChenGSR-V1.0`，`POST /v1/audio/transcriptions`；本机只在内存构造 WAV 并上传。
- 云端 Agent：`python -m hub.passport_agent`，OpenAI-compatible chat API；部署使用 `Qwen/Qwen2.5-7B-Instruct`。未配置模型时明确进入有限指令模式，不冒充模型对话。
- “查看时间、电脑名称、打开官网”使用固定工具映射，防止模型只回复进度文字而没有执行。其他请求由模型生成结构化计划，仍受会话授权、电脑 URL 白名单、命令长度与超时限制。
- TTS：Windows 已安装的中文 System.Speech，转换成 16 kHz PCM 后分块传回 Passport；每块等待设备入队确认，设备预缓冲后连续播放，结束时返回播放字节数、耗时和断粮次数；入队 ACK 不等于播放完成。
- 电脑动作结果通过既有 Gateway outbox 上传云端，离线不重放未知动作。回复须等本地终态及云端确认后再播报。

这版需要 Passport 通过 USB 连接运行网关的 Windows 电脑。手机用于连接设备热点、填写 Wi-Fi 和 Agent 铭牌。保存 Wi-Fi 不意味着已经实现无线语音传输；纯 Wi-Fi 独立对话、BLE 语音、免按键唤醒不在本版实现范围。

## 启动与操作

配置复用桌面 Gateway 的 `gateway.json` 和私有 token。另建受限私有 JSON：`base_url`、`api_key`、`stt_model`。`SUMMON_OPERATOR_CODE` 用于为这台电脑完成设备授权；不要将它写入源码、命令行参数或公开日志。配置来源必须是部署者。

```powershell
python -m gateway.passport --config <gateway.json> --private <private.json> --port COM6 --nameplate <Agent铭牌>
```

不要与原桌面 Gateway 同时运行同一 journal；数据库锁会阻止重复实例。当前仅支持 Windows，需安装中文语音合成组件。第一次接入先核对 COM 端口与实物身份，不能把示例 COM6 当作所有机器的固定端口。

设备确定键开始录音，说完静音自动提交，再按确定立即结束；双击确定取消/返回；上下键调节音量，长按上键打开配网页，手机连接设备屏幕显示的热点并访问 `192.168.4.1`。USB 模式可以只填写铭牌，不必填写 Wi-Fi；手机页面还提供音量滑块。配置完成双击确定返回。固件保存的铭牌优先于网关启动参数；空铭牌使用启动参数。

先测试“查看电脑当前时间”，再测试“打开浏览器”。电脑命令最多 8 秒，stdout/stderr 各最多 500 字符。取消会释放会话并停止本次执行，不会撤销已经完成的操作。

## 数据和凭证

API key 仅在电脑 STT 私有配置和服务器 Agent 私有配置中，固件和 USB 帧不包含服务凭证。音频会发送给所配置的 STT 服务；文字会发送给模型服务及 SUMMON。动作、有限输出和执行经验按既有账号权限保存云端，不自动公开。TTS 临时文件在结束时删除；网关窗口会显示转写和回复。

当前铭牌标识同一云端 Agent，不表示它拥有所有设备权限。语音桥只为部署配置中的电脑申请授权，不能用手机铭牌直接取得其他电脑控制权。网页中的 SIMULATED 是平台部署模式；本应用执行的 USB 采音、电脑命令和音频驱动调用是真实操作，应分别记录证据。

## 验收

分别报告编译、静态/主机测试、刷写校验、USB 握手、麦克风转写、真实电脑退出码、云端经验确认、屏幕显示和人工听感。不能用合成音频 API 测试代替真机麦克风，也不能把音频驱动 ACK 当作用户已听见清晰语音。

主机检查：`python -m unittest tests.test_passport_media tests.test_passport_agent`。
固件在 [SUMMON 语音分支](https://github.com/rfdiosuao/ai-passport/tree/feature/summon-usb-voice)，使用该仓库的完整构建与测试流程。升级时核对分区表，保留 NVS 应使用已验证的分段应用镜像，而不是将完整镜像从 0x0 覆盖数据区。

## 最终通道目标与当前验证边界

目标是 Passport 输入 → Hub → 电脑 A 的既有本地 Agent → Hub → 电脑 B 的已授权客户端。铭牌绑定 Agent 身份，不绑定它的运行地点。本轮测试使用服务器上专门注册的 Passport 测试 Agent，不能把这个测试身份说成用户原来的本地 Agent。迁移到本地 Agent 需要其适配器持续处理 input.text、action.request 和执行回执；更换 Agent 前须核实实际铭牌。

目前 gateway.passport 把语音入口和执行 Gateway 放在同一进程，因此完成的是 Passport 到这一台电脑的闭环；不能仅改变铭牌就声称任意另一台电脑已受控。两台电脑场景还需独立输入源路由和明确的目标电脑授权，未完成之前不得标记跨电脑验收通过。
