# 设备 Skill 第二轮接入修复 · 2026-09-22

## 复验残留 R1–R3

- R1：静态文件先验证存在且位于 web 根目录，再创建 FileResponse，避免延迟文件检查绕过错误中间件。缺失 .md/.txt/.js 和目录路径均验证为 JSON 404，不附带 inline 头。
- R2：Windows 串口按设备身份/名称区分 USB、蓝牙、其他；保留 serial_candidates 全集兼容旧使用方。任何分类都不是刷写资格，禁止自动选第一个口。
- R3：删除不再有截断行为的 truncated 常量，同时保留完整 stderr，避免隐式丢失诊断信息。


对应 WorkBuddy 的《SUMMON-接入问题清单-20260922》。不改写外部报告原始证据。

| 项目 | 修复 |
|---|---|
| P0-1 | 将模式读取明确为 GET /healthz 后读取 JSON.mode；不添加错误 URL 别名。Hub 所有路由 404 均返回 ErrorResponse。 |
| P0-2/3 | 按 JSON 结构与 Schema 判别边缘/Hub 错误，明确 JSON MIME 不足以判别；非空真实产品 UA，空 UA 和默认值可能被拦，任何 UA 都不保证放行。 |
| P0-4 | 增加 references/evidence.md：八类 JSONL、共同字段、原始日志关联、计量口径、样本数与阈值。按清单提出的“明确证据格式”方案处理；未声称已有任意硬件的自动采集器。 |
| P0-5 | 在线流程开头提供 clone、版本记录、cd、解释器检测与探针命令，区分阅读与执行。 |
| P1-1 | PowerShell 输出及 Python 解码明确 UTF-8，保留转义异常字节；删除破坏 JSON 的 stdout 截断。Windows 本机探测 87 条设备、6 个 COM 候选，0 个 U+FFFD，JSON 解析成功；未上传私人设备标识。 |
| P1-2 | 已存在输出给出可操作提示，--force 才覆盖；父目录不存在明确报错，不裸抛 traceback。 |
| P1-3/4 | 退出码 0=完整、3=未达门槛、2=输入或结构错误；新增 stage 枚举、stage/证据等级一致性与 evidence 类型校验，禁止不完整报告自称 demo_passed。 |
| P1-5 | 明确交付评估与适配代码，公网连接需凭证；提供 GitHub Issues 申请入口，维护者指定私密发放渠道，禁止公开凭证。已确认仓库启用 Issues。 |
| P1-6 | 保留 Origin 防护与 wire 错误码，403 文案指出 Origin，文档说明其优先级。不存在/错误方法不被 Origin 抢先遮盖，POST /v1/connect 返回 405。 |
| P1-7 | details 仅接受单个 0/1，其余或重复值返回 400 INVALID_MESSAGE。 |
| P1-8 | 明确 websockets 13.1 的 extra_headers、InvalidStatusCode 的限制；必要时同凭证/UA HTTP GET 辅助诊断，但不把独立请求结果当成上一次根因。 |
| P2-1 | Windows 结构化 devices、serial_candidates；所有平台均提供该键，增加 --diff。 |
| P2-2 | 安装目标必须显式指定 --agent 或 --skills-dir，不默认装到 Codex。未知宿主可直接读流程，不捏造 WorkBuddy 技能目录。 |
| P2-3 | 参考端入口使用 asyncio.run；协程内部 create_future 用 get_running_loop。 |
| P2-4 | 区分技能标准库脚本和仓库级依赖，补 pip install 与完整验证命令。 |
| P2-5 | 增加 device-sources.md 模板；先发现已有信息、确认目标和能力，再按路线补资料。 |
| P2-6/7 | 增加 python/py -3/python3 选择与版本检查；明确 /v1/connect 是 WSS GET Upgrade，不是 POST。 |

本地验证：15 项单元/集成测试，契约校验和参考端六项自检通过，Skill 格式验证通过。模拟与资料均不能替代真实硬件验收；本次没有刷写或操作任何执行器。
