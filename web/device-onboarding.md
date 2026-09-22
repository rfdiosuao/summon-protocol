# SUMMON 硬件接入 Skill：在线使用

在线入口：https://summon.entermodetwo.com/skill.md 。浏览器可直接阅读，无需先下载安装包。把下面的提示词交给能读取网页、执行命令和修改代码的 Agent；它会先评估指定硬件，再按可行路线开发。

## 可复制提示词

Read https://summon.entermodetwo.com/skill.md to assess my hardware and connect it to SUMMON.

中文也可以：阅读 https://summon.entermodetwo.com/skill.md，评估我的硬件，并按指南将它接入 SUMMON。

接入时请提供：设备完整型号与版本、官方仓库、官方 Wiki/开发者文档、SDK/API/通信协议、示例工程，以及厂商提供的手册或 PDF；同时说明连接在哪台电脑/手机、连接方式和期望能力。不知道的可写“不知道”，Agent 会整理已有资料并协助查找。涉及固件、接线或运动控制时，还会按需核对构建/刷写/恢复、引脚/供电、限位/停止说明。完整填写模板见在线 Skill 的第 0 节。

## 可选：安装到本地技能目录

在线读取已经可以开始工作。只有需要宿主长期发现和复用该 Skill 时，才使用下面的安装包。

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
