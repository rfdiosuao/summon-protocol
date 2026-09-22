# 云端 Agent 打开本机浏览器

新增 `browser.open` 桌面能力，参数为 `{"url":"https://..."}`。Hub 和 Agent 使用更新后的 Schema；旧客户端未升级不能声明该能力。USB BridgeMessage 与 MCU 固件不因此自动支持浏览器。

本机 adapter.kind=desktop，需要配置非空 allowed_urls，每个完整 URL 必须为 HTTPS，且动作 URL 与白名单精确相等。不支持脚本、命令行参数或任意浏览器自动化。适配器也支持 display.text。配置示例：

```json
{"kind":"desktop","allowed_urls":["https://summon.entermodetwo.com/#summon-browser-cloud-test"]}
```

browser.open 的 COMPLETED/device_ack 表示操作系统接受了默认浏览器打开请求，**不证明页面已渲染**。页面标题/地址需要另外观察。停止表示停止接收和执行新动作，不关闭用户已打开的浏览器或标签页。

hub/browser_agent.py 是明确标注的固定规则测试 Agent，只在 SIMULATED Hub 工作；它在服务器运行，接到任务后通过正常会话发送一次 browser.open，不是大模型自主决策。测试使用独立 shell 与本地数据库，不改变原有终端设备的能力。

工具 tools/smoke_gateway_cloud.py 增加显式 `--nameplates --open-browser` 开关，完成配对审批、铭牌连接、任务输入、浏览器打开回执、经验入库、释放与撤销。需要部署者发放的独立设备凭证及 operator 访问码；它确实会在运行电脑打开浏览器，不能当成无副作用的静态检查。
