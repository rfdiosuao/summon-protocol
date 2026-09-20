# 硬件与部署基线

2026-09-20 · 已核验产品资料，尚未接线、刷机或实物测试。

## 设备分工

| 设备 | 角色 |
|---|---|
| 远端高性能电脑 | 原有 Agent + Ghost Adapter，不因现场有电脑就把推理偷换到现场 |
| 现场电脑 | Gateway、Passport 设备桥、机械臂 SDK、本地校验与展示 |
| 服务器 A | HTTPS/WSS Hub、单写 SQLite、官网和 SSE |
| 服务器 B | 可选 ASR/TTS 音频桥和备份，不自动双活发控制权 |
| Passport A | 手持语音/显示壳，首版按键选名字 |
| Passport B | 展位壳；机械臂可用时成为组合壳的屏幕、耳朵和嘴 |

两台电脑暂按高性能电脑包含在内，具体系统与部署地点仍需确认。

## Passport

[官方规格](https://github.com/folotoy/ai-passport/blob/main/docs/hardware-design/specifications.zh_CN.md)：ESP32-C3、8 MB Flash、无 PSRAM，240×320 TFT、三个按键、ES8311 音频、2.4 GHz Wi-Fi/BLE、被动 NTAG213。

NFC 标签不是读卡器，也不假设读标签会触发 MCU 中断。首版按键/网页召唤；另配 NTAG213/NDEF 兼容 USB 读卡器后才做碰一下。保留厂商现有标签用途，SUMMON 地址优先独立贴纸，不覆盖恢复/绑定入口。

使用 [FoloToy 官方项目](https://github.com/folotoy/ai-passport) BSP。原始示例的 Wi-Fi 扫描不等于已实现联网应用，需要补配网、连接、设备认证、消息和回执。不默认刷通用小智固件。

首版使用有界文本消息与屏幕；按住说话/松开提交/播放回答为增强，不做全双工或声纹。无 PSRAM，音频分块、缓冲有界，显示与音频任务不能阻塞网络。固件不存完整对话历史。

## 板侧桥接 v1

本地网关 WSS `/device`，设备 token 一对一绑定 device_id 与 shell_id，配网时配置地址与可信证书。无法正确校验证书时先用有线开发桥验证，不能把跳过 TLS 校验作为部署默认。

BridgeMessage 16 KiB 上限，类型：device.hello、device.heartbeat、device.command、device.result、device.stop、device.stopped。Gateway→device.command 携带 command_id、session_id、lease_epoch、expires_at、action；板固件对 command_id 去重，停止/离线后清空未执行工作。设备→result 的 COMPLETED 必须是真实渲染/播放完成。停止回执与心跳规则见 [接入指南](ADAPTER.md)。

一个组合壳内 display/speech 路由到 Passport B，arm.gesture 到现场厂商 SDK。音频原始传输另走语音桥，此版不定义音频 codec 协议，未适配前不把 speech.say 列入 capabilities。

## 机械臂申请与验收

[赛事清单](https://autogame.feishu.cn/sheets/X3nnsDQHGhPtSMtaFxxciVWCnOM?sheet=0b9148) revision 36：reBot-DM 3 台、RS 6 台、Star Arm 102 9 台、SO-ARM101 1 台。这不是剩余库存。优先申请能运行官方示例的完整 reBot-DM/RS 套件：电源、固定底座、线材、通信适配器、急停和对应 SDK。Star Arm 是否为主控/从动用途须确认，不能直接当独立执行臂。

动作库 nod/wave/point_left/point_center/point_right 只发布已校准可用项。Schema 的 repeat<=3 不是完整运动保护；本地速度、幅度、工作区域按实际硬件限制。厂商控制器反馈才可报告运动完成。

急停和看门狗独立于模型与公网。不得假设断电或自动回零一定安全；无制动关节可能下坠，先验证受控停止方法。恢复需现场确认，不自动重连继续运动。

## 认人入口：两种形态，都不在机械臂上

Passport 的 NFC 是**被动 NTAG213**，只能被读、不能读别人。因此"贴牌召唤"不可能由 Passport 完成，需要外加读卡器。

认人入口有两种形态，二者都独立于"手"：

| 形态 | 载体 | 延迟 | 适用 |
|---|---|---|---|
| **固定接驳台** | Booth 上的 USB/串口 NFC 读卡器，接现场电脑 | 10–50ms | 主路径。仪式感最强，可围观 |
| **手持或手机** | NTAG213 名牌 + 游客手机碰一下打开星图网页 | 100–300ms | 兜底。不需要任何额外硬件 |

**读卡器不挂在机械臂上**，三个理由：

1. 契约的五要素里"认人入口"与"手"是两个独立要素，焊在一起等于把协议要消除的耦合又造回来。
2. 失败模式叠加：臂移动到读卡位失败再重试，在评委眼里就是"坏了"。拆开后读卡失败只是一次输入没进来，臂不必动。
3. 机械臂本身尚未确认（见上一节），给未确认的负载再加射频 payload 是纯增风险。

名牌可直接把 `summon://<agent_id>?v=1` 写进 NTAG213——地址在卡里，不在服务端。但见 [PROTOCOL](PROTOCOL.md)：**它只是寻址线索，不是凭证**，所以贴牌只能"选定一个 Booth 上已获授权的 Agent"，不能认证持卡人。

现场设备清单里没有 NFC 读卡器，**需确认能否自带**（USB 读卡器或 PN532 模块均为小件，自带比现场借可靠）。读卡输入走 Gateway 的 `input.submit`，见 [ADAPTER](ADAPTER.md)。

## 板侧硬件事实

- **无 PSRAM。** Wi-Fi/TLS、解码器、HTTP 客户端、JSON 解析器与 LVGL 争用同一块内部 RAM，必须当一份总预算核算。
- **ES8311 与 CW2017 共用 I2C0**（SDA GPIO10 / SCL GPIO7 / 7 位地址 0x18）。应用必须复用 BSP 总线，不得再建一套同端口驱动。
- **I2S 引脚**：MCLK GPIO6、BCLK GPIO5、WS GPIO3、DOUT GPIO2、DIN GPIO4。
- 引脚、总线与运行约束的**唯一事实来源是 `components/bsp/include/bsp_pins.h`**。缺定义时询问，不得用其他 ESP32-C3 开发板参数补全。
- BSP 的 LVGL 使用 20 行绘制缓冲，RGB565 下约 9.6 KiB；放大它会直接压缩网络与解码空间。
- **520 mAh + 常驻 Wi-Fi + 每秒心跳**，展会一天约 8–10 小时基本必须持续 USB-C 供电。部署与启动顺序需按此安排。

## 网络与启动顺序

现场电脑优先 RJ45，有线专线仍需实际测试到服务器的连通。Passport 需要已验证的 2.4 GHz 局域网；设备互访、热点隔离和现场接入规则需确认。

启动：Hub/DB → Gateway 本地检查和设备配对 → Agent → operator 官网。只显示真实上线设备，未拿到的机械臂不伪造在线。服务重启不自动恢复租约或执行未完成动作。
