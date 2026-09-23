# 2026-09-23 官网前后端对齐

来源：设计师交付的 `唤名-summon-20260923.zip`。页面结构、文案和 CSS 保留在 `web/frontend/`；构建后用 `python tools/build_frontend.py` 安装到 Hub 的 `web/` 静态目录。构建脚本不覆盖 Hub 权威的 `agent.md`、`skill.md` 等协议文档。旧控制台保留在 `/assets/console.html`。

## 当前真实流程

- 首页状态栏从 `/v1/catalog` 读取实时 Agent/设备状态。它是全网摘要，不用于判断当前用户的接入是否完成。
- 「接入 Agent」先创建 30 分钟的一次性接入会话。复制的提示词附带会话 token；Agent 按原协议注册并完成在线握手，再以自己的 Agent token 调用 `POST /v1/onboarding/claim`。等待页只接受该会话认领的 Agent；`POST /v1/onboarding/status` 返回 ONLINE 后才展示服务端的真实铭牌。仅有其他 Agent/电脑在线不会触发成功。
- 「写入名牌」从已登录团队的 `/v1/nameplates` 里按完整铭牌号或唯一 Agent 名匹配。无 operator cookie 时，用原有团队访问码接口登录。只在浏览器提供 Web NFC 时调用 `NDEFReader.write`，将 HTTPS 铭牌 URL 与铭牌号写到实体标签；写入 Promise 成功后才进入成功页。返回按钮中止等待。扫码访问 `/?plate=...` 时以只读公开接口校验铭牌，不靠页面猜测。
- 旧页面的计时假成功和固定 `001` 铭牌已去除。前端不会接触注册 invite、Agent token、设备授权 token。

## 尚缺的产品入口和硬件能力

1. 设计稿「给 Agent 取名字」目前没有选择/创建 Agent 的身份输入。后端的名字在注册时确定，且不可改名；现在该输入框只接受**已有 Agent 的完整铭牌号或唯一名字**。若要支持真正取新名字，设计师需增加 Agent 身份选择/注册入口，并决定改名权限与冲突规则。
2. 设计稿没有团队码输入框。当前在进入写卡页前使用浏览器原生输入框登录；应补一个正式的团队登录入口，避免用户不知道访问码从何而来。团队访问码和 Agent 注册邀请不同。
3. Web NFC 写卡仅在支持该 API 且具备 NFC 的手机浏览器运行；桌面和不支持的手机会明确报错。网页写入的是**铭牌路由信息**；让硬件读卡后切换 Agent，还需要该硬件官方 SDK/固件上的 NFC 读取与设备授权适配器。网页写卡本身不授予控制权限。
4. 当前官网没有独立的设备选择/授权按钮；电脑客户端仍使用 `/assets/device.html` 的团队授权与 Gateway TUI。Agent 在线和铭牌写入都不等于设备已获控制权。若设计稿要在官网完成全链路，需要新增配对码、能力审核、设备选择及执行状态界面。

## 部署与验证

`cd web/frontend && npm ci && npm run build`，回仓库根目录运行 `python tools/build_frontend.py`。在本地跑 `python -m unittest discover -s tests -q` 和 `python tools/validate_contract.py`。服务器部署时保留 `/etc/summon` 凭证及 `/var/lib/summon` 数据库，备份旧 `web/` 与源码，更新应用文件并重启 Hub。上线后核对首页、静态媒体、Skill、catalog、一次性会话与旧控制台。
