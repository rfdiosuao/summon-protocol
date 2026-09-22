# SUMMON 硬件接入 Skill：安装与使用

把下面的提示词交给能读取网页、执行命令和修改代码的 Agent。先安装 Skill，再评估指定硬件；本包不是通用固件，不会自动刷写任何设备。

## 可复制提示词

请阅读 https://summon.entermodetwo.com/assets/device-onboarding.md ，下载并核验其中的 SUMMON 设备接入 Skill，将它安装到你当前宿主支持的技能目录，再使用 summon-device-onboarding 完成我的设备接入。先只读检测本机和已连接硬件，核对官方型号、SDK/API/BSP，按 H1–H8 输出符合情况、证据、可实现能力和缺口，选择 USB、Wi-Fi、蓝牙或其他合适的网关路径。能用官方接口桥接就实现适配器；需要固件且板型明确时，结合 SUMMON 契约与官方工程开发、构建，给出产物哈希、具体目标端口、刷写步骤和恢复办法。只有目标与刷写授权明确后才执行刷写。最后验证联网、授权、真实动作回执、断线停止、交接、经验入库与检索。未知项不要算通过，缺少硬件或凭证时继续可完成的本地工作，并说明尚未接入。不要把模拟响应当实物结果。

可补充：设备型号/照片标签信息、官方项目 URL、连接在哪台电脑、期望显示/语音/感知/运动能力。未提供时先发现，不要猜板型。

## 安装包

- ZIP：https://summon.entermodetwo.com/assets/summon-device-onboarding.zip
- SHA-256：https://summon.entermodetwo.com/assets/summon-device-onboarding.zip.sha256
- 可审查源码：https://github.com/rfdiosuao/summon-protocol/tree/main/skills/summon-device-onboarding

下载到新临时目录，计算 ZIP 的 SHA-256 并与校验文件比对；不把 HTML 错误页当 ZIP。解压前确认条目均位于 summon-device-onboarding/ 下，不含绝对路径、父目录穿越或符号链接。先阅读 SKILL.md 与 scripts/install_skill.py，再执行安装；不要使用远端脚本管道直接执行。

在解压目录运行以下一种命令（Python 3.10+）：

```sh
# Codex：使用 CODEX_HOME/skills 或 ~/.codex/skills
python summon-device-onboarding/scripts/install_skill.py --agent codex

# Claude Code：~/.claude/skills
python summon-device-onboarding/scripts/install_skill.py --agent claude

# 其他兼容 SKILL.md 的宿主：先确认其目录规范，再指定技能根目录
python summon-device-onboarding/scripts/install_skill.py --skills-dir <宿主的技能根目录>
```

安装器拒绝覆盖已有同名技能；升级先检查本地变更与版本差异。安装完成后按宿主方式重新加载，必要时新建会话。仅复制提示词不代表安装已发生。不能执行命令的聊天工具可阅读流程，但不能代替本机开发和刷机。

## 使用与输出

显式调用 `$summon-device-onboarding`（宿主支持时），或要求 Agent 阅读已安装的 SKILL.md。它包含本机只读探针、准入报告模板、报告完整性检查器，以及固件/网关/运输层的条件化指引。

它应先回答：设备是什么、证据在哪里、满足哪些硬性条件、可以提供什么能力、采用哪种接入方式、还缺什么。之后在授权范围内开发并验证。芯片有 Wi-Fi 不等于固件已联网；能编译不等于真机通过。

USB/串口、Wi-Fi/以太网、BLE/经典蓝牙、蜂窝等均可通过适配器连接。LoRa 等低速链路需满足时延与带宽约束，否则只作为非实时输入。NFC/二维码是入口，不是持续联网通道。

Agent 通常留在原电脑/服务器运行。设备或现场网关必须在线，持有相应凭证并获得有效会话后才可接收任务。Skill 无法绕过设备封闭接口、固件签名、缺失驱动或缺失网络。

## 网络凭证

当前网络由部署者配置设备身份与 Gateway token；安装包不含凭证。网页访问码不等于设备 token，也不是注册 invite。可用本地模拟先开发，但不能据此宣称已上线。

## 当前包的验证边界

探针、报告检查、无覆盖安装和打包流程有本地测试。每种实际硬件的驱动、固件、刷写与演示仍须单独验证；本包没有预生成的万能 .bin。
