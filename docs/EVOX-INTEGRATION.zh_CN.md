# EvoX 本地接入

SUMMON 可以把本地 EvoX 用作规划器，让它通过 Passport 铭牌接收语音请求，并调用同一台 Windows 电脑正在运行的 SUMMON 客户端执行获准动作。语音识别、铭牌路由、电脑执行、语音回传和经验上传仍沿用 SUMMON 现有链路。

## 数据流程

```text
Passport 音频 → Hub 语音识别 → 铭牌绑定的本地 EvoX → SUMMON action.request
    → 电脑客户端执行并返回证据 → EvoX 决定下一步 → Hub 播报 Passport 并沉淀经验
```

EvoX 每次收到的是包含原始任务和此前工具回执的短期 JSON 对话。适配器以 `--no-tools --no-session` 启动独立 CLI 规划，不会在本机直接执行 Planner 返回的 shell。所有动作仍要通过 SUMMON 会话能力、电脑客户端授权和动作时限；UNKNOWN 回执停止当前规划，不自动重试。

## 前置条件

- Windows 已安装 EvoX CLI；确认 `evox --help` 和 `evox --list-models` 可用。
- 指定 EvoX 可执行文件、私有 runtime 目录、provider 和 model ID。
- runtime 的 `settings.json` 配置对应 provider/model；只在私有文件或受保护凭证存储中保存模型 API key。
- 对 EvoX 做一次真实的短文本请求，确认模型可用。
- 部署者提供当前 Passport Agent credential 文件；不要新注册同名 Agent。切换时使用同一 Agent 身份。
- 运行 Gateway 的电脑客户端，目标 shell 状态须为 ONLINE。

Windows 私有配置示例：

```json
{
  "hub_url": "https://summon.entermodetwo.com",
  "name": "唤名 · Passport 语音 Agent",
  "conversation_log": "C:/Users/you/AppData/Local/SUMMON/conversation.jsonl",
  "model": {
    "backend": "evox",
    "executable": "C:/Users/you/AppData/Local/evox/bin/evox.exe",
    "agent_dir": "C:/Users/you/AppData/Roaming/evox-agent",
    "provider": "your-provider",
    "model": "your-model-id",
    "credential_file": "C:/Users/you/AppData/Local/SUMMON/private-model.json"
  }
}
```

`credential_file` 是仅本机使用的 JSON，例如 `{"api_key":"..."}`；适配器把密钥注入 EvoX 子进程环境变量，不作为命令行参数传递。可省略此字段以使用 EvoX 已有的私有认证配置。私有配置文件应限制为当前用户与 SYSTEM 读取，不放入仓库或共享目录。

启动时指定现有 Agent credential 文件：

```powershell
python -m hub.passport_agent --config C:/path/private/evox-agent.json --credentials C:/path/private/passport-agent-credentials.json
```

同一个 Agent token 只能有一个活动连接。切换时停止另一份 Agent 进程，核对日志显示已有铭牌及 `mode: evox`，再用 `/v1/agents/me` 确认 connected。不要并行启动服务端 Passport Agent 和本地 EvoX 适配器。

## 验收和限制

先通过桌面 Gateway 确认目标 shell ONLINE。然后从 Passport 发送低风险观察命令，确认 EvoX 规划、客户端回执、最终回复、Passport `play.done` 及云端 experience 记录全部对应同一请求。只有听到播报和看到目标应用/命令真实回执才算完成。任何未知执行结果都不得重放。

启动 [SUMMON · EvoX 云端会话窗口](https://github.com/rfdiosuao/summon-protocol/blob/main/hub/evox_viewer.py)，并将 `conversation_log` 指向本机私有日志。窗口即时展示 Hub 转来的文字、EvoX 回复和脱敏执行状态，不展示 PowerShell 命令或输出。可用 `D:/desktop/启动 SUMMON EvoX 云端会话.cmd` 同时启动窗口和本地 Agent；Gateway 客户端需另行运行。窗口退出不影响 Agent，重新打开后会读取本机保留的最近会话事件。

当前集成使用 EvoX 单次 CLI 对话，没有接管 EvoX 桌面应用的内部会话、长期历史或跨会话记忆；独立 SUMMON 会话窗口展示本地记录。SUMMON 经验仍由客户端上传到 Hub。Planner 当前每轮最多4次动作、每次 EvoX 调用最多12秒。超时会结束本次本地 EvoX 子进程，报告任务未完成。执行成功与否取决于目标客户端授权的 capability 和本机策略。

## 当前验收状态

- [x] 本地 EvoX CLI 能用已配置模型服务返回真实文本。
- [x] SUMMON Hub Agent 可用同一铭牌切换为 `mode: evox`。
- [x] Passport Wi-Fi 配网页可发现网络并保存配置（用户实测）。
- [x] 关闭未使用的 BLE 组件版本通过 GitHub Firmware/Static checks，并已刷写；降低显示缓冲的后续修复正在构建。
- [ ] 设备在 Wi-Fi 下保持稳定连接，完成 Passport 语音、EvoX 规划、在线 shell 执行、回执播报及经验入库全链路复验。
