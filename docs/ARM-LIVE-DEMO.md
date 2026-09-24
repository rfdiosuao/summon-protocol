# B601-DM 实机 Agent 演示

这台笔记本运行 `Arm Console → Gateway → LIVE Hub → 模型 Agent`。浏览器在 `http://127.0.0.1:8840/demo` 提交自然语言请求；模型先读取六轴实测角度、末端位置、模型净空与负载，再根据用户意图提出短路点。Gateway 只接受现场开放的轴和角度窗口，并按 URDF/STL 模型重算轨迹，最后由 COM 口控制器执行。页面展示可核对的计划依据、关节实测角度和最终回执，不展示模型内部思维链。录制动作可作为模型理解动作效果的示例，仍可通过 `arm.gesture` 调用。

`--cross-device` 模式另注册一具 `display_demo` 显示壳，同一个 Agent 同时声明 `display.text`、`arm.observe`、`arm.motion`。先在显示壳讲解并保存偏好，再从完整控制台交接到 `arm_demo` 机械臂壳；Agent 在新会话读取记忆及实机姿态。这条本地 LIVE 链路已验证显示、记忆保存和交接；公开网站仍使用 SIMULATED Hub，公网演示须另行部署 LIVE Hub 和凭证。

## 启动

使用安装了 `numpy`、`pinocchio`、`motorbridge`、`aiohttp` 的现场 Python，安装 `gateway/requirements.txt`。先从 `arm-console/` 启动 `python -m backend.app --allow-hardware`，在 `http://127.0.0.1:8870/` **人工连接**正确的 COM 口。机械臂底座、支撑、运动空间和物理急停须在现场确认。演示启动器不会自动打开串口。

在仓库根目录，把 [`tools/arm_demo.example.json`](../tools/arm_demo.example.json) 复制到**仓库外**的私有路径。填写模型的 `base_url`、`name`、`api_key`，以及现场验收过的手势、`motion_bounds`、`max_motion_speed_dps` 和 `min_clearance_mm`；完成验收后才把两个确认标志设为 `true`。DeepSeek 官方接口可用 `https://api.deepseek.com` 和 `deepseek-flash`；官方 API 使用非思考模式以便快速返回结构化动作。`motion_bounds` 可分别开放 J1–J6；`max_relative_offset_deg` 可设 1–30，`motion_axis_speed_caps_dps` 可逐轴设置上限（每轴 0.2–10°/s，且不超过 `max_motion_speed_dps`）。模型不能越过本地窗口或调高本地速度上限。示例中的角度与净空阈值只是格式；实机按当前模型、姿态和支撑重新验收。

```powershell
python tools/arm_demo.py --config <仓库外的私有配置.json> --run-dir <仓库外的私有状态目录>
# 同一个 Agent 在显示屏与机械臂之间交接：
python tools/arm_demo.py --config <仓库外的私有配置.json> --run-dir <新的私有状态目录> --cross-device
```

启动后打开 `http://127.0.0.1:8840/demo`，使用 `<私有状态目录>/secrets.json` 中的 `operator_code` 登录。输入“向左招手并回到原位”等自然语言，观察模型如何根据实测姿态规划、Gateway 如何检查边界、设备如何返回真实角度。跨设备模式可在 `http://127.0.0.1:8840/assets/console.html` 选择 `display_demo`，召唤 Agent、输入讲解偏好并请求保存；再交接到 `arm_demo`，用自然语言要求它基于当前姿态动作。访问码和 Gateway token 由启动器生成在私有目录，不写入仓库。点击“结束会话”会请求软件停止并确认保持；现场物理急停仍须可用。

现场展示可直接打开 `http://127.0.0.1:8840/assets/console.html?demo=1`：首条提问自动接入笔记本显示屏，页面实时列出原始请求、模型回复和设备结果；保存回答风格后点击「交接到另一台」，同一 Agent 转到机械臂。若机械臂折叠，先确认底座、支撑、空间和急停，在页面勾选现场确认并点击「分段抬臂至演示起点」。此操作只在本机接受已登录操作员请求，按当前姿态逐段让 J3 离开折叠止挡、J2 抬起、J4 回到动作窗口，最后在肩肘到位后使能 J6；若已部分抬起，仅补齐缺少的阶段。执行前预检各段模型路径，执行中检查角度、应力、温度和故障反馈，失败则停止并保持。到位后在机械臂会话直接输入自然语言动作要求；2026-09-24 的一次实机六轴招手由 `deepseek-flash` 根据当前姿态规划并收到 `controller_feedback` 的 `COMPLETED` 回执。

控制台按本次 `input_id` 显示模型文字和 Gateway 设备回执，不混用历史请求；会话剩余不足 25 秒时，下一次提交会先释放并重新接入同一设备。模型或会话失败会显示本次错误，不会一直停在“模型正在处理”。现场若还需用本机 3D 控制台手动调整姿态，可在**私有**演示配置的 `adapter` 中设置 `"allow_manual_reposition": true`，让 Gateway 接受手动调姿后的实测反馈；默认保持 `false`。Agent 动作执行期间，目标改变仍会被拦截。

六轴同步调试可先运行 `python tools/arm_choreo.py J1=5@10 J2=-20@2 J3=-30@3 J4=-8@5 J5=15@10 J6=0@3` 查看实时起点的模型预演。只有附加 `--execute` 才下发目标；高于 10°/s 的轴还须附加 `--confirm-risk`。目标值必须重新按现场姿态决定，示例并非通用动作。命令要求选中轴已使能，并检查直接及各轴不同速度的模型路径、实时反馈。控制台的 J1/J2/J3/J5 位置指令前瞻上限已调至 4°；只有重启控制台后才会应用新参数。

## COM9 主臂 → COM6 从臂全轴遥操

[`tools/arm102_teleop.py`](../tools/arm102_teleop.py) 读取 reBot Arm 102 主臂的七个舵机，把前六轴的相对角度按官方方向映射到 B601-DM 的 J1–J6。`--execute` 按官方主臂接入流程将 COM9 舵机逐轴卸载以便手动引导，不改零点或多圈计数；从臂通过 Arm Console 的真实反馈和控制器互锁执行。主臂和从臂都必须有可靠支撑。启动命令：

```powershell
python tools/arm102_teleop.py --leader-port COM9 --follower-url http://127.0.0.1:8870 --execute --seconds 60 --speed-dps 5
```

启动前主从臂必须处于对应的起点，底座、支撑、扫过空间和物理急停已经现场确认。控制台要求 J3 ≤ −5°、J2 ≤ −3° 才能使能 J6；处于折叠止挡时先通过现场监督下的本地控制退出折叠姿态。遥操脚本在主臂任何轴角度反馈变旧、从臂故障/过载、硬件角度越界、模型自碰撞或桌面碰撞时请求从臂停止并保持当前姿态。脚本限制每轴相对起点的角度、单周期步幅和速度；六轴同时变化时按各轴不同到达时间检查模型路径。结束后电机仍保持使能，不会自动释放承重关节。

## 录制动作预设

录制器只采样**已稳定的实机反馈**，不会自行使能或转动电机。开始时记下受支撑的起点；用现有本地控制台执行**已经逐段验证过**的动作；每到一个关键姿态运行一次 `sample`，包括最后回到起点的姿态；最后运行 `finish`。预设只记录相对角度，因此 Gateway 会从下一次执行时的实际起点重新计算目标，并重新检查整段运动。

```powershell
python tools/arm_gesture.py start --record <私有录制文件.json> --name wave_short --description "小幅招手，适合轻声问候" --speed 8
python tools/arm_gesture.py sample --record <私有录制文件.json>
# 在本地控制台将机械臂返回录制起点，确认姿态稳定
python tools/arm_gesture.py sample --record <私有录制文件.json>
python tools/arm_gesture.py finish --record <私有录制文件.json> --config <私有配置.json>
```

每个预设含 2–4 个关键姿态、最后必须回到起点，单轴偏移不超过 10°；录制器会检查硬件角度范围与整个 URDF/STL 轨迹。重新启动演示后，模型即可看到新预设的名字、中文含义、轴、相对路点和速度。模型只返回预设名与次数，Gateway 仍独立检查、执行和确认。

现场已录得 `wave_short`（J4 约 −3°、返回）及 `wave_wide`（J4 约 −6°、返回），均为 8°/s；DeepSeek Flash 分别根据“小幅”“远距离明显”的请求选中对应预设，Gateway 收到 `controller_feedback` 的 `COMPLETED` 回执。这两组轨迹只对应当时的现场姿态，不等同于任意起点或其他机械臂的通用动作。

## 远端接入

上面的单命令演示在本机运行私有 LIVE Hub，页面只监听 `127.0.0.1`。跨设备演示时，使用已部署的 HTTPS Hub，按 [Gateway 部署步骤](GATEWAY.md#笔记本作为-b601-dm-gateway) 为机械臂分配独立 shell、Gateway token 和 `arm.observe`、`arm.motion`、`arm.gesture` 策略；笔记本 Gateway 与远端 Agent 都主动连 Hub。现场录制预设和 `hub.passport_agent` 的 `gesture_catalog` 帮助模型理解各轴动作效果；远端设备不连接 COM 口。
