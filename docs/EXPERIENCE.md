# SUMMON 设备经验库

2026-09-22 · HTTP 扩展 v1；不修改现有 WebSocket 消息和 Snapshot Schema。

## 已实现

新动作进入 Hub 时持久化设备上下文；网关回传完成/失败，或 Hub 判定超时未知时，按 command_id 保存一条经验凭据。重复回执不重复计数。进程恢复会补齐已有上下文的终态凭据，重启前未结束的命令记为 UNKNOWN。

经验保存在 Hub SQLite 中，与最近 100 条命令快照分离。统计覆盖当前账号全部已采集凭据，列表最多返回最近 100 条。旧命令没有模式与版本快照，不自动回填。

隔离维度：operator_id、shell_id、capability、mode、profile_id。同一账号的 Agent 可在获得该设备活动会话后检索，其他账号不可见。SIMULATED 与 LIVE 不混用。设备版本变化后匹配新指纹，不复用旧统计。

目前提供可解释的统计建议：完成/失败/未知数量、平均回执耗时、有未知结果时禁止自动重放的提示。它不是训练模型或自动生成运动策略；检索次数只代表 API 被 Agent 读取，不是采纳或成功复用。

## Agent 接入

收到 session.activate 后，在规划动作前调用：

```http
GET /v1/sessions/{session_id}/experiences?capability=display.text
Authorization: Bearer <agent_token>
```

只允许所属 Agent 在未过期 ACTIVE 会话中调用；会话结束后拒绝。服务端限制为同账号、同设备、同模式、同配置指纹。返回 groups 中的统计和 guidance，以及 items 中的证据。

返回对象字段：v、scope、total、returned、items、groups、retrievals、note。items 包含 experience_id/command_id/session_id、operator_id/agent_id/shell_id、capability、profile/profile_id、mode、status、evidence、source、duration_ms、created_at。groups 按设备、能力、模式与指纹分组。

示例 Agent 已在每次输入执行前实际调用该接口，并在模拟输出中显示找到多少条历史。真实 Agent 适配器需要显式接入该工具；本次未接入真实模型。

检索结果只能作为参考，不得覆盖动作白名单、当前租约、期限、设备限位和停止要求；禁止从 UNKNOWN 推断成功并继续依赖它的动作。

## 控制台接口

```http
GET /v1/experiences?shell_id=shell_a&capability=display.text&mode=SIMULATED
Cookie: summon_session=...
```

三个筛选项均可省略。必须登录，仅返回当前账号数据；不在公开 catalog 发布。控制台读取不增加 Agent 检索次数。

## 版本、证据与隐私

部署配置可增加 device_profiles，每个 shell_id 对应 model、firmware、adapter_version 三个字符串。例如：

```json
{"device_profiles":{"shell_a":{"model":"AI Passport","firmware":"summon-0.1","adapter_version":"bridge-0.1"}}}
```

缺省值为 unspecified，页面如实显示；接真实硬件前应填写并随固件更新。指纹还包含声明能力与动作白名单。版本为部署者配置，不是硬件远程证明。

duration_ms 是 Hub 从接收动作到观察终态的壁钟耗时，包含排队与传输；不是电机运动时间或网络单程延迟。恢复记录的耗时还可能包含停机时间。

COMPLETED 代表网关声明完成，evidence 保留回执证据类型；不代表抓取成功等物理任务已独立验证。source 区分 gateway、timeout、recovery。mode 在动作提交时固定，不会因部署切换把模拟记录改成实物证据。

专用经验表不保存原始动作参数、对话、输出、自由文本错误、音频或视频。原有命令表仍保留其协议要求的请求与回执，两者不可混为一谈。当前共享范围为当前 operator，不自动上传 EvoMap，不开放跨用户共享。

## 后续边界

当前为单机 SQLite、启动时载入内存，适合比赛规模。规模扩大前需要分页查询、保留策略和索引。若要提炼真正的控制技能，还需要设备遥测、任务成功判据、参数白名单、重复验证，以及人工审核后的共享与版本撤销。不能从显示命令成功直接泛化到机械臂控制。
