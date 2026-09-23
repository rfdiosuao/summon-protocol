# Agent 个人后台登录

官网的两个入口使用同一登录方式：新 Agent 无需邀请码，直接自助注册并获得服务端随机分配的固定铭牌；完成 WSS `hello`/`welcome` 后生成一次性连接码。已有 Agent 复用原身份，直接生成连接码。铭牌与名字用于展示和定位，不能充当登录凭证。

| 接口 | 调用方 | 请求 | 返回 |
| --- | --- | --- | --- |
| `POST /v1/agent-login/codes` | 在线 Agent，Bearer agent_token | `{}` | `201 {code, expires_at}` |
| `POST /v1/agent-login/redeem` | 同源网页，精确 Origin | `{code}` 或 `{code, nameplate}` | `200 {agent, nameplate, devices}` + HttpOnly cookie |
| `GET /v1/agent-dashboard/me` | 已登录网页 | 无 | `{agent, nameplate, devices}` |

Agent 只有完成在线握手后才能生成连接码。连接码为 10 位易辨识字符，以 `XXXXX-XXXXX` 展示，5 分钟有效、一次性使用。Hub 仅保存连接码散列；验证有失败次数限制。输入铭牌进入的流程必须同时提交 `nameplate`，Hub 核对它与连接码对应的 Agent 一致。登录 cookie 有效 8 小时，服务重启后需重新获取连接码。网页登录不接收 agent_token，也不赋予设备控制权。

个人后台星图以该 Agent 为中心，默认显示电脑客户端节点。`devices` 只返回当前 Agent 的设备，不含配对码或授权令牌。个人后台批准设备配对后，设备节点会作为展示记录保留。`state` 表示当前 Gateway 在线状态；`authorized` 只在该 Agent 仍有有效设备授权时为真；`capabilities` 仅列当前授权能力；`session_state` 表示交接状态。Hub 重启会使设备授权失效，但不会抹掉星图里的适配记录，重新配对后才能再次调用。默认电脑节点即使尚未配对也会展示，但不会被标为已授权。

个人后台显示该 Agent 的名字、铭牌、在线状态。点击「配置硬件」后可以复制设备接入 Skill；设备客户端已有配对码时，打开 `/assets/device.html`，使用当前后台 cookie 核对设备并逐项授权能力。该 cookie 的设备授权仅限当前 Agent，Gateway 不能拿它连接另一铭牌。旧团队访问码不出现在新版官网和设备授权页，旧接口暂留给已有客户端兼容使用。实际设备授权仍遵循 [NAMEPLATES.md](NAMEPLATES.md) 的独立能力审批，接入 Skill 不能代替授权。
