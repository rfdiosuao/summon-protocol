# B601-DM 实机 Agent 演示

这台笔记本运行 `Arm Console → Gateway → LIVE Hub → 模型 Agent`。浏览器在 `http://127.0.0.1:8840/demo` 提交自然语言请求；模型先读取六轴实测角度、末端位置、模型净空与负载，再根据用户意图提出短路点。Gateway 只接受现场开放的轴和角度窗口，并按 URDF/STL 模型重算轨迹，最后由 COM 口控制器执行。页面展示可核对的计划依据、关节实测角度和最终回执，不展示模型内部思维链。录制动作可作为模型理解动作效果的示例，仍可通过 `arm.gesture` 调用。

## 启动

使用安装了 `numpy`、`pinocchio`、`motorbridge`、`aiohttp` 的现场 Python，安装 `gateway/requirements.txt`。先从 `arm-console/` 启动 `python -m backend.app --allow-hardware`，在 `http://127.0.0.1:8870/` **人工连接**正确的 COM 口。机械臂底座、支撑、运动空间和物理急停须在现场确认。演示启动器不会自动打开串口。

在仓库根目录，把 [`tools/arm_demo.example.json`](../tools/arm_demo.example.json) 复制到**仓库外**的私有路径。填写模型的 `base_url`、`name`、`api_key`，以及现场验收过的手势、`motion_bounds`、`max_motion_speed_dps` 和 `min_clearance_mm`；完成验收后才把两个确认标志设为 `true`。DeepSeek 官方接口可用 `https://api.deepseek.com` 和 `deepseek-flash`。`motion_bounds` 可分别开放 J1–J6；模型不能越过本地窗口或调高本地速度上限。示例中的角度与净空阈值只是格式；实机按当前模型、姿态和支撑重新验收。

```powershell
python tools/arm_demo.py --config <仓库外的私有配置.json> --run-dir <仓库外的私有状态目录>
```

启动后打开 `http://127.0.0.1:8840/demo`，使用 `<私有状态目录>/secrets.json` 中的 `operator_code` 登录。输入“向左招手并回到原位”等自然语言，观察模型如何根据实测姿态规划、Gateway 如何检查边界、设备如何返回真实角度。访问码和 Gateway token 由启动器生成在私有目录，不写入仓库。点击“结束会话”会请求软件停止并确认保持；现场物理急停仍须可用。

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
