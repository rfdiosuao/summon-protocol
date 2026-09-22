# 平台部署

Hub 使用 aiohttp 与 SQLite，运行单进程、单 worker。安装依赖：`python -m pip install -r hub/requirements.txt`。配置路径由 SUMMON_CONFIG 环境变量指定，启动命令 `python -m hub.app`。

Ubuntu 部署脚本见 deploy/install.sh；安装位置为 /opt/summon/app，配置 /etc/summon/config.json，数据库 /var/lib/summon/hub.db。脚本生成随机凭证；配置不能进入 Git 或静态网站。Nginx 配置模板位于 deploy，证书和 DNS 按自己的域名设置。

配置要求 origin、mode、database、invite、operator_codes、gateway_tokens；可选 shell_labels、device_profiles、port、secure_cookie。生产 origin 必须与实际 HTTPS 入口一致。SIMULATED 才允许启动 `python -m hub.simulator`，不得让模拟客户端接入 LIVE 系统。

验证：`python -m unittest discover -s tests -v`、`python tools/validate_contract.py`。服务器配置好 SUMMON_CONFIG 后，可运行 tools/smoke_platform.py 验证模拟闭环；该脚本会产生模拟任务数据，并拒绝 LIVE 模式。

升级前备份源码和 SQLite；一致性备份应使用 SQLite backup API 或停止相关服务后复制数据库及 WAL/SHM。重启不会恢复旧动作，仍未终止的会话可能进入故障隔离，需要确认设备已停。备份不对公网开放。

设备经验接口与配置见 [EXPERIENCE](EXPERIENCE.md)。站点不会自动导入旧命令作为新经验，因为旧命令没有模式和版本证据。
