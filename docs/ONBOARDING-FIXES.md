# 接入问题清单修复记录 · 2026-09-22

依据接入方《唤名-接入问题清单》逐项核对。清单中的第三方延迟与故障窗口属于接入方测量，不直接等同于当前部署实测。本文件区分修复、验证和仍需现场证据的内容。

| 条目 | 处理与证据 |
|---|---|
| P0-1 | `/assets/*.md` 统一 text/plain UTF-8、inline；CSS/JS 补 charset。与 `/skill.md` 一致，避免部分网页工具不识别 text/markdown。HTTP 回归覆盖。 |
| P0-2 | API 404/405/尾斜杠统一 ErrorResponse，405 保留 Allow。 |
| P0-3 | 区分缺少 cookie、无效 cookie、过期 cookie、缺少 Bearer、无效 token、无效 invite。无证据不把 token 无效写成过期。 |
| P0-4 | PROTOCOL §5 增加冷建连和热会话预算、连接池、预热、超时与分段统计。20 次公网冷连接结果见下方。 |
| P0-5 | 规定 1–30 秒指数退避及抖动、welcome 后稳定 10 秒才重置；模拟参考实现同步实现，401/403 停止重试。 |
| P0-6 | agent.md 顶部新增六条常见错误。 |
| P1-1/2 | 撤销/断线停止发送新的动作、记忆与 input.finished；迟到 input.text 本地丢弃记录。Hub 拒绝非 ACTIVE input.finished；回归测试验证。 |
| P1-3/4 | 保持旧 ErrorResponse 结构；定义 command_id/update_id/session_id 的关联方式、reply_to、不可解析与异步超时规则。 |
| P1-5 | 新增 `catalog?details=1` 和 CatalogDetailed；默认 Catalog 保持旧结构，不破坏严格客户端。 |
| P1-6 | 总表补 Health、ExperienceResult 及对应端点；Schema 覆盖真实经验响应。 |
| P1-7 | 会话/命令读取先鉴权，未认证统一 401，已认证再判断 404/403。 |
| P1-8/10 | agent.md 明确名字不可改、注册响应恢复、授权头硬要求、升级前 401。 |
| P1-9 | 新增 agent_token 专属 `/v1/agents/me`，返回自身状态、当前连接是否完成握手及握手时间；不会返回 token。 |
| P2-1 | 更正“服务尚未实现”，说明当前 SIMULATED。 |
| P2-2 | agent.md 官方契约链接固定到已提交 commit；旧 clone 用独立检出恢复，保留用户工作区。 |
| P2-3 | 明确 16384 UTF-8 字节与 2000 Unicode 字符。 |
| P2-4 | 公开本修复记录与可复现测试。真实外部接入尚缺 invite/外部 Agent/设备联调，不能伪造 summons。SIMULATED 继续保持 LIVE 计数为 0。 |
| P2-5 | 补 `$defs` 校验方式，根 Schema 不用于 HTTP 请求。 |

D-01～D-08 分别由版本说明、模式读取、接口表、旧 clone 恢复、名字与 token 指南、Authorization 要求、单位说明覆盖。D-04 是接入者本地历史状态，不能通过修改线上文件自动修好，也不强制覆盖其仓库。

A-01～A-07 为接入者注明“已修”的适配器自身问题，不重新改写其代码。接入参考要求：握手超时、短连接不重置退避、网络错误与协议错误分类、取消不补发、验收使用新的输出目录或先清理本次产物、凭证文件权限按平台验证、子进程显式编码并处理解码异常。

## 网络证据与边界

[本机公网采样](network-probe-20260922.json)：2026-09-22 09:56 UTC，20 次独立 curl 冷请求，20 成功、0 失败。TLS 完成累计时间 p50 306ms / p95 579ms / max 799ms；healthz 总耗时 p50 951ms / p95 1450ms / max 1480ms。TLS 指标包含 DNS/TCP 时间，不是纯 TLS 运算耗时。

复测命令：`python tools/probe_public_network.py --samples 20 --output <新报告路径>`。此探针不带凭证、不驱动设备，不等于 WSS 热连接或动作延迟。当前样本不能证明接入方此前的 TLS 故障已根治，也不能代表比赛现场网络。原报告中 cfOrigin 时长不必然等于纯应用处理时间，不能凭一个头部确定全部根因。

公开闭环验证使用 tests/test_hub.py 的 test_complete_simulated_handoff：注册→握手→激活→输入→模拟完成→经验入库/检索→交接→权限隔离。它是自动化模拟证据，不是外部 Agent 或真机认证。

部署后复验：11 项单元/集成测试通过；契约 10 个场景、94 正例、8 负例通过；公网 curl 检查文档 MIME、API 错误及新增接口 Schema 通过。Chrome 实测登录、召唤、模拟输出、经验显示、保存偏好、换壳、释放通过，手机无横向溢出。

额外发现并处理：相同 Python urllib 客户端携带默认 `Python-urllib/3.8` 时，Cloudflare 返回 403/1010；显式标注真实产品 `SUMMON-Adapter/0.1` 并发送 Accept 后返回 200。agent.md 与设备 Skill 已补客户端要求及非 JSON 边缘错误处理。本次未调整 Cloudflare 全站安全规则；边缘策略可能变化，仍需现场验证。
