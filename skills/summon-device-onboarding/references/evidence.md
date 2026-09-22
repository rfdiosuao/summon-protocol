# 验收证据格式 v1

每次验收建立新目录 `evidence/<run-id>/`，禁止混用上一轮成功产物。每类检查一个 UTF-8 JSONL 文件，例如 `cold_start.jsonl`；一行是一次实际测量，不省略失败尝试。八个文件名称与 admission-report.acceptance 的八个键一致。

每行共同必填：schema_version=1、run_id、sample_id（该轮全局唯一）、check、started_at/ended_at（UTC RFC3339）、evidence_level（physical/simulated）、device_profile（型号/固件/适配器版本）、success（布尔）、raw_logs（本目录内原始日志相对路径数组）、metrics（下表）。raw_logs 不放 token；原始日志需能关联 command_id/session_id、设备版本、模式和回执来源。时间差用单调时钟测量，UTC 仅用于追踪。

| check | 每行 metrics 必填字段 | 聚合验收 |
|---|---|---|
| cold_start | ready_ms | 至少 3 次，全部成功且每次 ≤60000ms |
| soak | duration_ms, unrecovered_disconnects, crashes | 至少一次连续 ≥1800000ms，所有记录无不可恢复断线/崩溃 |
| interactions | command_id, session_id | 至少 20 次，全量成功率 ≥95%，保留失败 |
| latency | gateway_to_device_received_ms, command_id | 至少 20 次，全量 nearest-rank p95 ≤1000ms；从网关发送到设备接收，不能拿 completed 耗时替代 |
| handoff | double_occupancy, stale_replays | 每行完整 A→B→A；至少 10 次，均成功且两个计数都为 0 |
| faults | kind=network/cable/restart, recovered, state_correct | 每类至少 3 次，均正确反映状态并恢复 |
| rejection | kind=duplicate/expired/stale_lease, correctly_handled | 每类至少 3 次；重复返回已有结果且无重复执行，其余拒绝；全部正确处理 |
| experience | command_id, traceable, deduplicated, mode_separated, outcome=success/failure/unknown | 三类 outcome 均有记录；均可追溯、去重且模式隔离 |

示例（仅格式示意，不是实测，不能直接用来通过验收）：

```json
{"schema_version":1,"run_id":"example-only","sample_id":"cold-1","check":"cold_start","started_at":"2026-09-22T00:00:00Z","ended_at":"2026-09-22T00:00:12Z","evidence_level":"simulated","device_profile":"example/firmware-v1/adapter-v1","success":true,"raw_logs":["raw/cold-1.log"],"metrics":{"ready_ms":12000}}
```

由设备适配器或测试员在真实测试时产出上述文件。通用 Skill 不具备任意厂商的断电、拔线或急停驱动，不会自动执行这些操作。量化阈值、原始日志、设备型号和动作完成的真实性必须共同审查；JSONL 格式正确不代表硬件合格。

admission-report 的 evidence 填 `evidence/<run-id>/<check>.jsonl:sample_id` 及聚合结果。运动设备另提交 motion_stop 日志，包含本地停止/看门狗/固定/限位/校准/恢复、负载姿态和停止时间，不能用远程请求成功替代物理停止。

先用 `python scripts/check_admission.py report.json` 检查结构与阶段一致性，再独立核对上述原始证据与阈值；该检查器不是设备验收采集器，不会自动签发 demo_passed。缺少对应适配器的采集能力时明确列为尚未完成。
