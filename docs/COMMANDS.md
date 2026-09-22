# Windows 客户端命令执行

`command.exec` 是独立授权的桌面能力。设备配置必须同时启用 `adapter.kind=desktop`、`enable_commands=true` 和已存在的 `command_directory`；服务端 shell 白名单及用户配对授权也必须包含 command.exec。浏览器授权页默认不勾选此能力。

参数：`{"capability":"command.exec","args":{"command":"Get-Date -Format o"}}`。默认使用 Windows PowerShell，无配置文件、非交互运行。command_directory 是初始工作目录，不是文件系统沙箱；命令具有运行客户端的 Windows 用户权限。不要把普通设备授权误当成命令授权。

首版约束：命令 1–1000 字符、最长执行 8 秒、不允许交互输入；stdout/stderr 各最多 500 字符，超出标记 truncated。0 退出产生 COMPLETED；非零退出和超时产生 FAILED/DRIVER_ERROR；无法确认或断线取消仍按 UNKNOWN 处理，不自动重放。

ActionCompleted/ActionFailed 可带 execution：exit_code、stdout、stderr、timed_out、truncated、duration_ms。命令与有界输出会经正常请求/回执存到云端，并提供给本次 Agent；专用经验表仍只保存执行元数据。这是对原显示适配器“固定回执、不传正文”的扩展。命令原文和输出不写入滚动日志文件；有界输出可显示在终端界面。

PowerShell 在读取命令前加入 Windows Job Object；取消、超时和任务结束都关闭 Job，停止该任务的子进程，不留下后台命令。不会终止不属于此任务的进程。子进程不继承以 SUMMON_ 开头的环境变量。桌面浏览器打开动作独立于命令运行，不通过 PowerShell 绕过 URL 白名单。

长时间安装、交互应用、后台守护任务和其他操作系统命令执行不属于首版。Agent 和 Hub 须使用新版 Schema，不能向旧客户端发送 command.exec。

## 多步 Agent 与桌面应用启动

Passport 模型 Agent 现在根据实际工具回执进行最多四次调用，可以先检查环境、再执行、再验证；配置模型时不再用关键词覆盖模型命令。单条命令仍受上述时长、输出及当前 Windows 用户权限约束，因此不能承诺所有任务都能完成。

打开已安装 GUI 软件时，先用 `Get-StartApps` 查真实 AppID，再调用 `Start-Process explorer.exe -ArgumentList 'shell:AppsFolder\实际AppID'; Start-Sleep -Milliseconds 700`，由 Windows 桌面接收启动请求，随后检查进程或窗口标题。这是有意启动用户桌面软件，不等同于允许普通任务留下任意后台子进程；取消网关会话不会关闭已经由桌面接收的软件。直接 `Start-Process 软件.exe` 或在 PowerShell 中创建 Shell.Application COM 对象可能随命令 Job 回收而终止，不应将退出码 0 当成打开成功。

2026-09-23：本机通过上述 Explorer 路径实际启动飞书，命令任务结束后进程仍在，窗口标题为“飞书”。Passport Agent 模型已切换到现有 SiliconFlow 服务中的 `Qwen/Qwen3-30B-A3B-Instruct-2507`，语音识别模型不变。它仍是服务器 Agent，不是本地 Codex。

云端复验会话 `session_4cd95c9603d7bfe568d29f1c` 通过 Hub 向 Windows 客户端发出查找、启动和验证请求，Agent 返回“飞书客户端已成功启动，主窗口已确认。”本机同时确认窗口仍在。此复验通过云端文字输入，不能代替 Passport 麦克风/Wi-Fi 验收；当时 Passport 的网络状态为离线。

2026-09-22 已通过 Windows 本地真实执行测试和 [云端执行记录](command-cloud-smoke-20260922.json)：远端测试 Agent 经 Hub 请求本机执行 Get-Date 和输出 SUMMON_CLOUD_COMMAND_OK，退出码 0，stdout 回传，经验入库且持久化确认完成。

现有测试 Agent 铭牌为 `SMN-VZJS-9CV3`。它是固定规则联调工具：授权 command.exec 后，在 TUI 按 T 输入 `powershell:Get-Date -Format o` 可复测；普通文字触发 browser.open，仍要求该能力已授权。不能将其描述为具备自主规划能力的大模型 Agent。
