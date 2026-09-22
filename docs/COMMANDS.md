# Windows 客户端命令执行

`command.exec` 是独立授权的桌面能力。设备配置必须同时启用 `adapter.kind=desktop`、`enable_commands=true` 和已存在的 `command_directory`；服务端 shell 白名单及用户配对授权也必须包含 command.exec。浏览器授权页默认不勾选此能力。

参数：`{"capability":"command.exec","args":{"command":"Get-Date -Format o"}}`。默认使用 Windows PowerShell，无配置文件、非交互运行。command_directory 是初始工作目录，不是文件系统沙箱；命令具有运行客户端的 Windows 用户权限。不要把普通设备授权误当成命令授权。

首版约束：命令 1–1000 字符、最长执行 8 秒、不允许交互输入；stdout/stderr 各最多 500 字符，超出标记 truncated。0 退出产生 COMPLETED；非零退出和超时产生 FAILED/DRIVER_ERROR；无法确认或断线取消仍按 UNKNOWN 处理，不自动重放。

ActionCompleted/ActionFailed 可带 execution：exit_code、stdout、stderr、timed_out、truncated、duration_ms。命令与有界输出会经正常请求/回执存到云端，并提供给本次 Agent；专用经验表仍只保存执行元数据。这是对原显示适配器“固定回执、不传正文”的扩展。命令原文和输出不写入滚动日志文件；有界输出可显示在终端界面。

PowerShell 在读取命令前加入 Windows Job Object；取消、超时和任务结束都关闭 Job，停止该任务的子进程，不留下后台命令。不会终止不属于此任务的进程。子进程不继承以 SUMMON_ 开头的环境变量。桌面浏览器打开动作独立于命令运行，不通过 PowerShell 绕过 URL 白名单。

长时间安装、交互应用、后台守护任务和其他操作系统命令执行不属于首版。Agent 和 Hub 须使用新版 Schema，不能向旧客户端发送 command.exec。

2026-09-22 已通过 Windows 本地真实执行测试和 [云端执行记录](command-cloud-smoke-20260922.json)：远端测试 Agent 经 Hub 请求本机执行 Get-Date 和输出 SUMMON_CLOUD_COMMAND_OK，退出码 0，stdout 回传，经验入库且持久化确认完成。

现有测试 Agent 铭牌为 `SMN-VZJS-9CV3`。它是固定规则联调工具：授权 command.exec 后，在 TUI 按 T 输入 `powershell:Get-Date -Format o` 可复测；普通文字触发 browser.open，仍要求该能力已授权。不能将其描述为具备自主规划能力的大模型 Agent。
