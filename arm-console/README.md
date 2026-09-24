# SUMMON Arm Console

基于 Seeed reBot Arm B601-DM 官方 URDF 的本地三维控制台。它运行在连接机械臂的现场电脑上，供本机运维/调试及 [SUMMON Shell Gateway](../docs/GATEWAY.md#笔记本作为-b601-dm-gateway) 适配器调用。

## Agent CLI（离线可用）

在仓库根目录运行 `tools/summon_arm.ps1`（其他平台可用 `python tools/summon_arm.py`）；PowerShell 包装器优先选择已安装的 SenseCraft Python。输出统一为 JSON，成功退出码 `0`，拒绝或错误为 `2`，错误体有稳定的 `error.code`。`describe` 与指定起点的 `preview` 不需要启动控制台，也不会打开串口：

```powershell
.\tools\summon_arm.ps1 describe
.\tools\summon_arm.ps1 preview J3=-6 --from-pose folded
.\tools\summon_arm.ps1 preview J2=-35 J3=-70 --from J1=0 J2=-3 J3=-5 J4=0 J5=0 J6=0
```

`describe` 给出官方 URDF 路径、J1–J7 参数、安全角范围、预设姿态、碰撞规则、使能顺序、速度限制、重力模式及命令名。离线预演需要 `numpy` 与 `pinocchio`；现场 SenseCraft Robotics Python 已含这两个依赖。未能加载模型时，预演和执行均返回 `MODEL_CHECK_UNAVAILABLE`，不会下发目标。

连接到控制台后，`status` 返回实时角度、目标、速度、力矩、温度、故障、重力估计和反馈年龄。默认地址为 `http://127.0.0.1:8870`，可用全局 `--url` 指定另一个**本机**端口。执行命令需由操作人员先启动带 `--allow-hardware` 的控制台并显式连接实机；CLI 本身不连接串口：

```powershell
.\tools\summon_arm.ps1 status
.\tools\summon_arm.ps1 move J3=-8 --speed 3
.\tools\summon_arm.ps1 move J3=-8 --speed 3 --execute
.\tools\summon_arm.ps1 enable J3 J2                 # 只输出 J4→J3→J2 计划
.\tools\summon_arm.ps1 enable J3 J2 --execute       # 依序使能并保持实测角
.\tools\summon_arm.ps1 stop --execute               # 停止推进，保持使能
```

`move` 默认仅预演；`--execute` 才向**已连接实机**下发，并会重新读取反馈、检查故障/采样年龄、验证姿态未漂移和全段模型轨迹。返回的 `targetAccepted: true` 只表示后端接收目标，`motionComplete: false` 表示需继续读取 `status` 确认到位。多轴目标用同一个 `move` 命令给出。`enable` 会先使能 J4 再使能 J3/J2；J2 折叠端和 J6 起身联锁仍由后端执行。大于 10°/s 需在**每次动作**增加 `--confirm-risk`，上限为 25°/s。动作结束后承重轴保持使能，CLI 不自动失能；得到可靠支撑后，`disable J3 J4 --supported --execute` 才可解除保持。`clear-fault J3 --execute` 只调用现有清故障接口。夹爪需先标定 J7 电机角到宽度的映射，才能运行 `gripper 40 --execute`。

模型检查使用与网页相同的 URDF `collision_runtime` STL：每 ≤1° 采样非相邻连杆的实体碰撞及底座上方 6 mm 桌面平面；20 mm 内报告预警，重合/触桌则拒绝下发。它不能识别模型外的人员、物体、线缆和支撑。本地 CLI 与 `arm-console` Gateway 适配器分别在写入前执行模型预演；其他直接调用控制台 REST 的写入口不会自动经过它们的预演。

`gravity on` 仅调用现有后端实验重力模式，需要 `--execute --confirm-risk` 且后端以 `--experimental-gravity-feedforward` 启动；状态不可用会拒绝。之前 J3 前馈实机试验曾触发超速保护，所以这不是已经完成现场标定的碰撞保护。日常动作仍使用后端的位置速度控制及其 J4 支撑、目标跟踪、速度、温度和力矩保护。

## 已实现

- 官方 URDF/STL 完整机械臂渲染、全息扫描台、自由轨道相机和 TCP 坐标显示；
- J1–J6 真实关节轴上的全息角度环，可直接拖动目标角并显示实际角/目标角；
- 加载官方 collision STL，通过 BVH 三角网格重合检测持续检查非相邻连杆、夹爪、底座和桌面净空；
- J1–J6 实时角度、目标、速度、扭矩、温度、状态码和故障显示；
- 平行夹爪 0–100 mm 开合动画；
- 单轴使能并保持当前位置、确认目标时自动使能、角度控制、轨迹停止与全轴解锁；
- 仿真模式和真实 MotorBridge/COM6 驱动共用一套界面；
- J2/J3 负角约束、J3 折叠端 5° 余量、J6 起身联锁；
- 扭矩、温度、状态码及目标范围保护；
- 实机轨迹中对 J2/J3/J4 使用渐进的 MIT 重力力矩前馈，并在位置持续跟踪不上时冻结全部目标；
- 默认只监听 `127.0.0.1`，默认只启用仿真。

## 启动

首次安装并构建：

```powershell
cd arm-console
npm install
npm run build
```

仿真模式：

```powershell
python -m backend.app
```

或者直接运行：

```powershell
.\run.ps1
```

打开 <http://127.0.0.1:8870>。

## 三维交互

- 左键拖动空白处旋转镜头，右键拖动平移，滚轮以指针为中心缩放，双击恢复全景；
- 拖动机械臂各关节旁的发光圆环修改目标角度，白色指针表示实测角，绿色指针表示目标角；
- 点击右上角 `RISK` 显示或隐藏风险部位的黄/红定位线；发生重合时在模型接触点显示红色放射高亮。正常状态不显示围栏或网格；
- 点击控制卡片中的 `J1`–`J6` 标记可将镜头聚焦到对应关节；
- 角度下发前会以最大 1° 步长预测整段轨迹；目标模型重合时，3D 光环和侧栏滑块停在最后一个安全角度，不能继续向碰撞方向旋转；
- 实机运动期间若当前反馈姿态发生模型实体重合，网页会发送“停止并保持”指令，避免伺服继续互相挤压；20 mm 内的包围盒净空会先给出接近预警。

实时碰撞边界属于基于 URDF 几何的操作辅助层；现场物理急停、固定底座和人工确认仍是实机操作的最终保护。

## 连接实机

实机模式必须使用带有 `motorbridge` 的 SenseCraft Robotics Python，并显式传入 `--allow-hardware`：

```powershell
.\run.ps1 --allow-hardware --serial-port COM6
```

启动参数只开放后端能力，并不会立刻占用串口或使能电机。还需要在网页中点击“连接 COM6 实机”。连接时只读取反馈；单轴“使能 / 上锁”只保持实测当前位置，确认目标时可自动使能，目标角按设定速度渐进发送。动作结束后继续保持扭矩，承重轴不会自动失能。

使能或移动 J2/J3 前会先使能 J4 并保持腕部当前位置；抬起或运动中的 J2/J3 不允许在未确认支撑时解除 J4 使能。此前两次 J3 的 MIT 重力前馈实机试验都触发超速保护，因此默认关闭 MIT 轨迹前馈和全轴手动引导。默认实机轨迹使用位置速度模式，保留有限的位置偏置辅助；页面会明确显示“前馈暂停”，不能把它当作有效的力矩补偿。实验性前馈仅供完成故障分析和现场标定后显式启动：

```powershell
.\run.ps1 --allow-hardware --serial-port COM6 --experimental-gravity-feedforward
```

实验模式下，J2/J3/J4 在未使能时切入 MIT，确认使能后保持实测位置。轨迹开始且实测力矩方向与 URDF 重力矩一致时，控制器以每秒最多 1.5 Nm 渐进施加模型重力矩的 40%，J2/J3 上限 2.5 Nm、J4 上限 0.8 Nm；J4 的支撑前馈由 J3 抬起角度触发。若实测速度超过目标速度，会暂缓位置指令并快速撤去前馈。位置指令最多领先实测角度 2°；若持续 0.6 秒未取得足够进展，或 MIT 轴速度超限，则停止轨迹并保持故障触发时的位置。超速后本次控制器会暂停再次使用 MIT 前馈，并在 `/api/state` 的 `faultDiagnostics.samples` 中保留故障前最多 30 帧 J3/J4 的位置、速度、力矩和指令。软件保护停机时电机状态码仍可能为正常的 1；确认后解除软件保护不会向健康电机发送硬件清故障指令。若电机已经处于位置速度模式，控制器不会在承重状态下为了前馈而热切 MIT 模式。实验参数未经安全标定，不能仅凭仿真或单元测试视为可用。

### 只读采样

控制台连接 COM6 后，可从 `arm-console` 目录运行下面的命令。采样器只订阅 `/api/ws`，不会使能电机、发送目标或切换控制模式；输出是逐行 JSON，包含每帧 J2/J3/J4 的实测角度、命令角、目标角、上报速度、力矩、前馈、状态码，以及主机和控制器时间。`--seconds` 可设为 1–600。

```powershell
& 'C:\Users\Lenovo\AppData\Local\SenseCraft Robotics\r\c929cf611806b458\python.exe' tools/capture_telemetry.py --seconds 10
```

文件默认保存到 `diagnostics/telemetry-*.jsonl`；`--output` 可指定路径。当前默认安全模式也能采静止基线及普通位置控制的数据，但前馈本身为关闭状态，不能由这些数据判断其有效性。实验模式中的故障帧还会在文件的 `faultTrace` 字段写入一次；控制器重启会清空内存里的故障帧，所以发生故障时应先保存采样文件，再考虑重启。分析速度时必须结合相邻帧的角度变化、时间间隔和采样年龄，静止电机的上报速度可能存在固定偏置。

若电机反馈故障码，控制台会锁止全部使能和运动指令，并保留故障提示。状态码 `13 / 0xD` 表示达妙电机通讯丢失；确认现场状态后，可点击故障轴的“清除故障”，该操作不会自动使能或运动。清除后需要重新确认目标。驱动在显式使能该轴时才将其通讯超时设为 2000 ms，并在使能前后持续发送当前位置保持指令；失能得到反馈确认后，通讯超时恢复为 0 ms，避免控制台关闭时失能电机反复报故障。

网页软件停止不能替代现场物理急停。解除 J2/J3 或全轴解锁前，机械臂必须已经受到支撑。

### 夹爪标定

官方 URDF 描述的是左右夹指各 0–50 mm 的直线位移，没有给出 J7 电机角到夹指行程的传动关系。未标定时，网页会显示 J7 电机状态，但阻止实机开合指令。

完成现场测量后，可传入闭合与全开电机角：

```powershell
.\run.ps1 --allow-hardware --serial-port COM6 `
  --gripper-closed-deg <闭合角度> `
  --gripper-open-deg <全开角度>
```

## 开发模式

分别启动后端与 Vite：

```powershell
python -m backend.app
npm run dev
```

Vite 地址为 <http://127.0.0.1:5173>，`/api` 和 WebSocket 会代理到端口 8870。

验证：

```powershell
npm run build
python -m unittest discover -s ../tests -p "test_*arm*.py"
```

## 结构

```text
arm-console/
├── backend/           # aiohttp API、仿真器和 MotorBridge 单线程驱动
├── public/model/DM/   # 官方 DM URDF 与 STL
├── src/               # Three.js/URDFLoader 界面
├── dist/              # Vite 生产构建，不提交
└── run.ps1            # Windows 启动入口
```

MotorBridge 的所有串口操作都固定在一个专用线程中，避免反馈读取、轨迹发送和界面请求并发访问串口。浏览器断开只停止新的界面请求；后台继续监控并保持已有目标。服务正常退出时，如果仍有承重轴上锁，会停止目标推进并保留当前电机保持状态，而不会擅自解除承重轴。

## 模型来源

模型来自 [Seeed-Projects/reBot-DevArm / Rebot_Arm_description/DM](https://github.com/Seeed-Projects/reBot-DevArm/tree/main/Rebot_Arm_description/DM)，导入时对应提交 `62443b5f762c736901a6296b9c3120037a714e4d`。仓库内 DM 模型文件保留上游 [CERN-OHL-W-2.0 许可](public/model/DM/LICENSE)；控制台自身代码遵循项目根目录许可。模型使用米和弧度；浏览器控制台显示角度和毫米并在边界处转换。
