# Agent Mail CLI 通知：用户自行配置指南

官方安装文档：https://agent.qq.com/doc/cli-setup.md （CLI：`@tencent-qqmail/agently-cli`，管理端：https://agent.qq.com）

## 工作原理（为什么需要本机工作进程）

```
Docker 容器 monitor（采集 + 管理后台 + SQLite）
   │  事件写入通知队列 monitor_outbox（只有容器进程写数据库）
   │
   │  http://127.0.0.1:8080/api/agentmail/worker/*   ← 令牌：data/agentmail-worker.token
   ▼
Windows 本机工作进程 scripts/agentmail_worker.py（仅标准库）
   │  调用官方 agently-cli message +send --confirmed
   ▼
发件账号 = 本机 agently-cli 授权的邮箱（例如 jetbra@agent.qq.com）
```

- OAuth 凭据由官方 CLI 保存在**运行它的 Windows 用户**的系统钥匙串，不复制到项目、数据库或 Docker 镜像。
- 工作进程**不直接打开 SQLite**：Windows 与 Docker Desktop 共享同一 SQLite WAL 文件会导致 `disk I/O error`（旧版本工作进程就曾引发此问题）。它通过本机管理接口领取任务、回报结果，只回报退出码与 `ok/queued` 标志，CLI 输出不写入日志或数据库。
- 令牌文件 `data/agentmail-worker.token` 由监控服务首次启动时随机生成，只允许访问 `/api/agentmail/worker/*` 三个接口。不要分享该文件；删除后重启容器会生成新令牌。

## 一次性配置步骤

```powershell
# 1. 安装 / 更新 CLI（需要 Node.js）
npm install -g @tencent-qqmail/agently-cli

# 2. 确保监控服务在运行，然后安装并启动本机助手（只需一次，之后登录 Windows 自动启动）
cd D:\jk
docker compose up -d monitor
powershell -ExecutionPolicy Bypass -File .\scripts\Start-AgentMail.ps1
```

3. 打开 http://127.0.0.1:8080 → **通知设置**，在 Agent Mail 面板的“授权状态”旁点 **「重新授权 / 登录」**。

`Start-AgentMail.ps1` 默认会注册**当前用户**的计划任务 `ProductMonitor-AgentMail`（触发器“用户登录时”、登录类型 Interactive、最低权限，不需要管理员），并立即启动它。助手用 `.venv\Scripts\pythonw.exe` 隐藏运行在你自己的 Windows 会话里，因此使用的就是你本人钥匙串里的 agently-cli 登录；**未授权时它也保持运行**，这样才能从后台发起登录。日志：`data\agentmail-worker.log`。若任务计划程序拒绝注册，脚本会改为在“启动”文件夹放一个快捷方式。

| 命令 | 作用 |
|---|---|
| `Start-AgentMail.ps1` | 注册登录自启动 + 立即后台启动（重复运行会先停旧进程；会处理已排队邮件） |
| `Start-AgentMail.ps1 -Status` | 只把 CLI 安装/授权状态上报到后台，**不发送任何邮件** |
| `Start-AgentMail.ps1 -Stop` | 停止助手（自启动保留，下次登录仍会启动） |
| `Start-AgentMail.ps1 -Uninstall` | 停止并移除自启动 |
| `Start-AgentMail.ps1 -Login` | 不用后台，直接在当前终端完成 `agently-cli auth login` |
| `Start-AgentMail.ps1 -Foreground` | 前台运行，便于诊断，Ctrl+C 停止 |
| `Start-AgentMail.ps1 -NoAutoStart` | 只启动本次，不注册自启动 |
| `python scripts\agentmail_worker.py --once` | 处理一次队列后退出（**会发送已排队邮件**） |

助手是单实例的（`data\agentmail-worker.lock`）；监控服务还没启动（例如刚登录、Docker 尚在启动）时，助手每 15 秒重试连接，不会退出。

## 从管理后台授权 / 切换账号

1. 点 **「重新授权 / 登录」**：后台写入一个登录请求；本机助手约 2 秒内领取，执行官方 `agently-cli auth login`（OAuth 设备授权）。
2. 面板显示“等待浏览器授权”和授权链接（可点击，新窗口打开；若 CLI 给出验证码也会一并显示）。agently-cli 通常还会自动在本机默认浏览器打开同一页面。
3. 在页面上确认授权后，CLI 自动退出，助手重新读取 `+me`，面板约 3 秒内自动刷新为“已授权：<邮箱>”，发件账号和每日额度随之更新。
4. 链接 15 分钟内有效；过期或想换一个链接，点“取消”后重新点按钮。
5. **退出登录 / 切换账号**：已授权时出现该按钮，确认后执行 `agently-cli auth logout`；之后 Agent Mail 通知暂停，再点「重新授权 / 登录」用另一个 QQ 邮箱账号授权即可切换发件账号。

安全：浏览器只通过管理后台发起请求；助手凭 `data\agentmail-worker.token` 领取请求，只回报授权链接（仅接受 https）、验证码和结果，不回报 CLI 的其他输出或任何凭据。

CLI 位置自动从 `npm root -g` 定位原生 `agently-cli.exe`；自定义安装可在启动前设置环境变量 `AGENTMAIL_CLI_PATH` 为该 exe 的绝对路径。监控服务地址默认 `http://127.0.0.1:8080`，可用 `--server` 或 `MONITOR_URL` 修改。

## 在管理后台设置（http://127.0.0.1:8080/#notifications）

“通知设置”页顶部的 **Agent Mail CLI（QQ 邮箱 · 本机）** 面板显示：

- 本机工作进程：运行中 / 未运行、最近心跳时间
- CLI 安装：版本与路径；授权状态：已授权 / 未授权或失效
- 发件账号（CLI 当前主邮箱）与每日发送额度；Agent Mail 待发送数量
- 配置步骤、各类邮件的**预览链接**（只在浏览器里渲染，不发送）、测试邮件记录

为某个网站启用 Agent Mail：

1. 在“网站推送设置”表中点该网站的 **设置通知**。
2. 通知方式选 **Agent Mail CLI（QQ 邮箱）**，填写收件邮箱（多个用逗号分隔，最多 20 个），勾选需要通知的事件（新品上架 / 商品下架 / 重新上架 / 价格下降 / 价格上涨；访问拒绝告警总是发送）。
3. 勾选 **启用消息推送** 并保存。勾选即授权系统向这些邮箱自动发送所选事件；不勾选则绝不发送。
4. 点 **预览邮件** 查看效果（不发送）。
5. 需要验证通道时点 **发送测试邮件**：会弹窗显示发件账号与收件人，确认后才排队，工作进程约 10 秒内真实发送 1 封。测试邮件不会自动重试。

设置保存在 SQLite（`monitor_sites.notification_channel / email_to / notify_enabled / event_rules`），容器重建后保留。发件账号无法在后台修改——它就是本机 CLI 授权的账号；要换发件账号，在面板点「退出登录 / 切换账号」后再「重新授权 / 登录」。

## 投递状态与可靠性

- `已提交(sent)`：CLI 返回 `ok=true, queued=true`，表示 Agent Mail 已接受并排队，不保证已进入收件箱。
- `待发送(pending)`：等待工作进程；CLI 退出码 7（限频/额度用尽）会指数退避，最多 5 次后转为“已暂停”。
- `已暂停(held)`：退出码 2（参数不合规）、3（授权失效，需重新 `auth login`）、6（业务拒绝），或网站关闭推送/收件邮箱无效。
- `需核对(uncertain)`：超时、进程中断、未知退出码、或 CLI 要求二次确认——可能已经发出，**不会自动重发**；核对收件箱后在队列里点“核对后重试”。
- 每个商品事件单独一封邮件，大量变化可能耗尽每日额度（当前账号为 50 封/天，以 `+me` 实时返回为准）；可只勾选上新/降价。
- Docker 内的通知调度器跳过 Agent Mail 网站，只有本机工作进程发送，避免重复。

## 邮件样式

`python scripts\generate_mail_previews.py` 用与实际发送相同的模板（表格布局、内联样式、最大宽度 600px，无外部 CSS/JS；兼容 QQ 邮箱 / Gmail / Outlook）生成 `docs/mail-previews/*.html|*.eml` 和 `admin/mail-previews.html`（示例数据，不发送）。类型：NEW、REMOVED、RETURNED、PRICE_DROP、PRICE_RISE、ACCESS_DENIED、TEST。SMTP 发送 HTML + 纯文本两个版本；Agent Mail 发送 HTML 版本。

## 常见问题

**后台显示“未授权”，但我之前登录过。** agently-cli 会按调用它的环境分别保存登录（识别 Codex、Cursor、Claude Code、WorkBuddy 等 AI 助手终端的环境变量，配置在 %USERPROFILE%\.agently-cli\agents\ 下）。在 AI 助手终端里完成的登录不会被助手使用。助手调用 CLI 时会去掉这些环境变量，固定使用普通用户（workspace `default`）的登录；直接在后台点「重新授权 / 登录」即可，结果一定是助手所用的那份。

**后台显示“工作进程未运行”。** 运行 `Start-AgentMail.ps1`；查看 `data\agentmail-worker.log`；也可在“任务计划程序库”中查看 `ProductMonitor-AgentMail`。如提示令牌无效，确认 `data\agentmail-worker.token` 来自当前运行的容器（`docker compose up -d monitor` 后重新启动工作进程）。

**授权失败或超时。** 按官方文档，不要反复重试登录，先核对错误信息或在 https://agent.qq.com 管理端检查账号。

## 2026-10-10 Lecomte 采集失败的原因与修复

1. 16:00 的“页面访问失败；未提交快照”并非 HTTP 403：Crawl4AI 0.9.4 的 robots.txt 检查把 `browser_config.user_agent`（为保留 Chrome 原生 UA 设为 `None`）直接交给 `urllib.robotparser`，后者对 `None` 调用 `.split()` 抛出异常，被当成页面访问失败。修复：浏览器启动后读取 Chrome 真实 `navigator.userAgent`，通过包装的 RobotsParser 用于 robots 检查（读取失败时退回 `*`）；robots.txt 检查、证书校验和 403/429 暂停机制均保持开启。
2. 修复后首次重跑暴露第二个问题：Lecomte 正常商品页内嵌 Cloudflare 被动检测脚本 `/cdn-cgi/challenge-platform/scripts/jsd/main.js`，旧的“访问控制页面”判断仅匹配 `challenge-platform` 子串，把正常页面误判为拦截页并暂停了网站。现改为只识别真实拦截/验证页（`cf-chl-`、`_cf_chl_opt`、`/challenge-platform/h/`、“Just a moment...” 标题、“Your request was blocked”）。
3. 错误信息改为显示真实原因（异常类型 + 简短信息，去除 URL），不再统一显示为“页面访问失败”。
4. 修复后在 Docker 中按系统同样方式重跑（定时任务触发，2026-10-10 16:47 北京时间）：robots 检查正常，第 1 页 HTTP 200、提取 23 件、站点声明共 119 件；第 2 页（`/en/shop/page/2?...`）被站点的 Cloudflare 反爬保护**真实返回 HTTP 403**（176 字节拦截页）。系统按设计暂停该网站并记录访问拒绝事件，未提交快照，上一份成功快照保持不变。本项目不做任何绕过；是否、如何恢复（例如取得站点许可、加大翻页间隔 `page_delay` 后人工重新启用）需要你决定。
