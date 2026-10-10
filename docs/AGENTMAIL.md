# Agent Mail CLI 配置与运行

官方安装文档：https://agent.qq.com/doc/cli-setup.md

本次在 Windows 安装 `@tencent-qqmail/agently-cli` 1.0.18 和 `agently-mail` 技能，完成用户浏览器 OAuth 授权，并用 `+me` 验证成功。当前账户返回 daily_send_quota=50；额度以账户实时返回为准。没有发送真实邮件。

## 用户自行配置

在使用监控系统的 Windows 用户账号下执行：

```powershell
npm install -g @tencent-qqmail/agently-cli
npx --yes skills add https://agent.qq.com --skill -g -y
agently-cli auth login
agently-cli +me
```

CLI 输出授权链接后，在浏览器完成授权。凭据由官方 CLI 保存到系统钥匙串，不放入项目、SQLite 或 Docker 镜像。CLI 账号独立于浏览器当前登录账号，以 `+me` 返回为准；授权失败时不要反复重试登录，应先核对错误。

Docker 的 Linux 用户不能直接读取 Windows 钥匙串，因此采用本机邮件工作进程，复用 Docker 采集服务的通知队列。只发送后台选择 Agent Mail 且启用推送的事件；不需要开放额外网络端口。发件账号为 CLI 的当前主邮箱，各网站可使用不同收件邮箱。

```powershell
cd D:\jk
docker compose up -d --build monitor
powershell -ExecutionPolicy Bypass -File .\scripts\Start-AgentMail.ps1
```

`Start-AgentMail.ps1` 在隐藏窗口启动本机工作进程。需要 Python 3.10+；此脚本仅用标准库，无需在 Windows 安装 Crawl4AI。前台诊断使用 `-Foreground`。本机重启后重新运行脚本，或由用户自己的任务调度器启动（此版本不自动注册系统启动任务）。支持一次处理检查：`python scripts/agentmail_worker.py --once`。**只要推送已开启，该命令可能发送实际排队邮件；不要把它当作无副作用测试。**

默认自动定位 npm 安装的原生 exe。自定义安装时，可在运行脚本前设置 `AGENTMAIL_CLI_PATH` 为官方 CLI 原生 exe 的绝对路径。`--db` 可指定共享数据库；默认 `D:\jk\data\snapshots.db`。不要复制其他机器的 OAuth 凭据。

## 后台设置

打开 http://127.0.0.1:8080/#notifications，查看本机工作进程状态及授权邮箱。点击“设置通知”，选择“Agent Mail CLI（QQ 邮箱）”，填写收件人和事件规则。勾选启用推送即授权系统自动向所填邮箱发送所选事件，工作进程调用 CLI 的 `--confirmed`；关闭时不发送。

查看 http://127.0.0.1:8080/assets/mail-previews.html 可预览 NEW、REMOVED、RETURNED、PRICE_DROP、PRICE_RISE、ACCESS_DENIED 六类邮件。预览使用明确标记的演示数据，没有发送邮件；实际模板与预览共用同一生成函数。SMTP 发送 HTML 和纯文本双版本；Agent Mail 发送 HTML 版本。

## 可靠性与限制

- 并发工作进程通过 SQLite 事务抢占同一事件，重复启动不重复发送已提交邮件。
- CLI 限频时使用退避，最多 5 次；授权失效和业务明确拒绝暂停投递。
- 网络故障、超时、进程中断可能无法确认是否送达，标记需核对，不盲目自动重试。
- SMTP/CLI 已提交仅表示服务器接受，不保证最终进入收件箱。
- 每个商品事件单独一封邮件，大量变化可能达到账号额度；可只选择上新/降价规则。
- CLI stdout/stderr 不写入队列日志，避免泄露登录信息或邮件元数据。工作进程本地日志在 `data/agentmail-worker*.log`。

## 本次采集失败原因修复

2026-10-10 16:00 的“页面访问失败”来自 Crawl4AI 0.9.4 robots parser 对空 User-Agent 调用 `.split()`，并非该记录证明的 HTTP 403。修复在浏览器启动后，从空白 Chrome 页读取原生 `navigator.userAgent`，用于 robots 检查；仍启用 robots.txt、证书验证和访问拒绝暂停机制。后续结果不能从这条旧失败记录推断，需要查看新日志。
