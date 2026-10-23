# 多网站电商商品监控

2026-10-20 更新：新增 Gmail 发件箱、变化历史组合筛选与排序、独立通知投递队列（内容预览、渠道与接收目标）。给其他电脑安装或升级时，可直接转发[安装 / 更新提示词](docs/INSTALL_UPDATE_PROMPT.md)。

日常 Chrome 扩展与 Windows 本机采集的配置见[扩展接入](docs/CHROME_EXTENSION.md)和[本机采集说明](docs/HOST_COLLECTOR.md)。站点访问结果受网络、登录态和站点政策影响，以各自电脑的完整采集验收为准；历史测试不代表新安装一定可用。

正式版本使用 **Crawl4AI 0.9.4 + 原有 diff_sqlite.py + APScheduler + SQLite + FastAPI**，提供中文 Web 管理后台。无需 EasySpider，也不再启动 changedetection.io。此前的研究、代码和回归测试保留供回溯，不参与正式 Compose 调度。


## 克隆与完整复现（含已配置站点）

仓库包含 5 个已配置站点的**配置种子** `config/sites.json`：`lecomte`、`bergzeit_arcteryx`、`boardersla_arcteryx`、`niponino_arcteryx`、`thinkempire_arcteryx`（适配器、URL、提取 schema、分页/执行模式、间隔、事件规则、合规说明）。种子**不含**任何快照、历史、令牌或密码；为避免克隆者给原作者发邮件，种子中所有站点 `notify_enabled=false`、`email_to` 为空，请在后台填写你自己的收件人后再开启通知。

**前置条件**：Docker Desktop（Linux 容器）、Python 3.11+（仅宿主机脚本/扩展令牌需要）、Google Chrome 120+、Git。

```powershell
git clone https://github.com/DY13208/jk.git
cd jk
copy .env.monitor.example .env        # 按需填写 ADMIN_PASSWORD、SMTP/Webhook 等；不要提交 .env
docker compose up -d --build monitor  # 首次启动的空数据库会自动导入 config/sites.json（SITES_SEED）
```

打开 http://127.0.0.1:8080/ 即可看到 5 个站点。已有数据库不会被覆盖；如需手动导入或补齐：

```powershell
docker compose exec monitor python scripts/import_sites.py            # 只新增缺失站点
docker compose exec monitor python scripts/import_sites.py --overwrite # 用种子覆盖同 id 站点配置
docker compose restart monitor                                       # 让调度器加载新站点
```

**Chrome 扩展（`lecomte` 使用“日常 Chrome 扩展”模式）**：

1. 启动后台后生成扩展令牌配置：`python scripts/prepare_extension.py`（读取 `data/extension-worker.token`，写入被忽略的 `chrome-extension/config.local.json`）。
2. Chrome 打开 `chrome://extensions` → 开启“开发者模式” → “加载已解压的扩展程序” → 选择 `chrome-extension/` 目录。
3. 点击扩展图标 → **授权已配置网站**，允许访问扩展模式站点的域名。之后扩展每 30 秒向后台领取任务；浏览器关闭或电脑睡眠时不会运行。

**通知 / Gmail / Agent Mail**：Gmail 可直接在“通知设置 → Gmail 发件箱”中配置，通知详情见独立“通知投递队列”。在后台站点设置中选择通知方式并填写收件人后开启“通知”。Agent Mail CLI 的安装与登录见 [Agent Mail 说明](docs/AGENTMAIL.md)（宿主机运行 `scripts/Start-AgentMail.ps1`）；SMTP、企业微信、pushplus 等令牌只放在 `.env`。

**说明**：首次成功采集只建立基线，不会发送“新品”通知；`niponino.fr` 对普通自动化访问返回 403/Cloudflare 验证，现使用 **FlareSolverr 模式**（`execution_mode: "flaresolverr"`，compose 自带 `flaresolverr` 服务，仅内部网络），见 [FlareSolverr 说明](docs/FLARESOLVERR.md)；扩展模式仍可用于其他站点。

## 初次交付记录（历史，当前状态以本机后台为准）

- 用户提供的成果已从 `C:\Users\YiRan\crawl-test` 复制到本项目，源目录未删除。
- `data/snapshots.db` 保留原数据库全部记录，包括三轮 Crawl4AI（每轮 119 件）和三轮 EasySpider 的历史测试记录。只有通过原始 JSON 分页证据核验的 Crawl4AI 快照作为正式基线。
- `data/crawl4ai_products.json` 保留原商品 JSON；`legacy/runs/` 保留每轮原始数据；`legacy/snapshots.original.db` 是未升级的原数据库，用于迁移回归验证。
- **网站采集默认暂停，企业微信通知默认关闭。** 本次开发没有向真实企业微信发送消息，也没有重新采集目标站点。
- 成功测试不等于自动化访问授权。启用前须核对站点政策及访问许可，后台核对依据选填。

## Docker 一键启动

需要 Docker Desktop 的 Linux 容器模式（或 Linux Docker Engine），当前镜像面向 **linux/amd64**。Chrome 安装来自 Google 官方源，首次构建需要网络。

```powershell
cd D:\jk
docker compose up -d --build monitor
```

后台：**http://127.0.0.1:8080/**。进程健康检查：`/health`；业务状态：`/api/health`（沿用管理员认证）；API 文档：`/docs`。

监控总览显示采集端离线、尚无成功记录、数据过期、最近采集失败和通知待核对状态，并展示最近成功时间及当前任务排队时长。超过两倍采集间隔且至少 30 分钟未成功更新时，数据标记为过期；主动暂停的网站不因数据陈旧报警。业务异常不会令 Docker 存活检查失败。

容器自带系统 Google Chrome、Xvfb、xauth 和 tini，`xvfb-run` 为有界面 Chrome 提供显示环境，`shm_size: 1gb` 避免共享内存不足。只运行一个 Uvicorn worker、一个服务副本；多个网站在同一服务内独立调度，采集按 Docker Chrome、本机 Chrome、Chrome 扩展、FlareSolverr 分组；同组串行，不同组可并行，慢站点不会占住其他模式的采集槽位。每站默认 15 分钟；启用或重启后的首轮在约 5 秒后运行，此后按各站间隔采集。错过的任务合并，不补跑大量历史轮次。

查看和停止：

```powershell
docker compose ps monitor
docker compose logs --tail 100 monitor
docker compose stop monitor
```

旧 `jk-catalog-1`、`jk-changedetection-1` 若仍存在，会显示 orphan 提示，它们与新服务无关。可停止这两个旧容器，保留旧卷；不要用 `down -v` 删除历史卷。

Linux 上首次部署到绑定目录时，确保 UID 1000 可写入 `data/`，例如对本项目的 `data` 目录设置所有者；容器使用非 root 用户 `pwuser`。Windows Docker Desktop 通常不需要此操作。

## 中文后台使用

左侧包含监控总览、网站管理、商品列表、变化历史、通知投递队列、通知设置、执行日志。右上角可以立即创建 SQLite 一致性备份。

### 网站 1：Lecomte

1. 在“网站管理”打开 `lecomte` 的设置，入口已填为 `https://www.lecomte-alpirando.be/shop?attribute_value=729-2672`。
2. 适配器使用 `odoo_lecomte`，保留经过测试的 CSS 提取规则。商品稳定标识为 `data-product-template-id`，同时核对商品 URL slug 尾部数字；默认变体 ID 单独保存。
3. 完成站点使用政策及访问许可核对，再勾选确认和启用。核对依据、日期和许可范围可选填；勾选确认不会自动获得站方授权。
4. 首次正式轮次将与旧的最近完整快照比较；**旧快照导入不会推送历史商品**。全新网站没有旧基线时，第一轮只建立基线。
5. 按当前要求，保持“启用消息推送”关闭。

### 网站 2 及更多网站

`site2` 已预建，URL 留空、暂停、15 分钟、无通知。获得 URL 后可在后台填写并选择适配器；“添加网站”可以创建任意数量独立网站。

- 相同 Odoo / Theme Prime 结构：可复用 `odoo_lecomte`，但应先对新站验证 DOM 和分页参数。
- 有完整分页声明的其他 CSS 列表：使用 `css_catalog`，在“适配器配置”粘贴 `config/css_adapter.example.json` 并调整选择器。这个文件只是结构示例，不是已验证的新站配置。
- 站点具有不同动态加载、API 或分页机制：在 `adapters/` 新增独立 async 适配器，并注册到 `REGISTRY`；不要改动数据库、通知或调度模块。接口见 [开发说明](docs/DEVELOPMENT.md)。

网站 ID 是目录的身份；已有历史的网站更换 URL、目录筛选或适配器配置时，必须创建新 ID，防止把两个不同目录误当成商品上下架。名称、间隔、开关、通知规则可独立修改。历史记录不会随暂停删除。

当前 Lecomte 列表可靠提供商品 ID、默认变体 ID、名称、价格、原价、折扣、购买链接等；**没有可靠的商家 SKU 或尺码库存证据**。后台显示“未知”，不会把变体 ID 当 SKU、把在列表中出现当有库存。未来适配器可补充这些字段。

## 快照与变化规则

商品身份为 `(site_id, product_id)`；使用原 `diff_sqlite.load/diff/ingest`，比较 ID 集合。即使总数相同，新增与消失也分别记录。

|事件|含义|
|---|---|
|NEW|此前完整历史中未出现的 ID|
|REMOVED|上一完整快照有、本轮完整快照没有|
|RETURNED|此前出现过、上一完整快照没有、本轮又出现|
|PRICE_DROP / PRICE_RISE|两个相邻完整快照中同一商品的售价下降 / 上升|

原价、名称、URL 的差异仍写入原 `changes` 表；五类商品事件和访问告警写入 `monitor_events`。通知规则只控制推送，不删除变化历史。

每轮要求：所有分页存在、每页有效成功、分页声明一致、ID 唯一、商品名称/价格/链接有效、ID 总数与站点声明总数一致。失败轮次只写执行日志，**不入快照、不产生上下架或价格事件**。403/429 或访问控制页面会立即结束并暂停该站；后台显示告警，有通知配置时可进入告警队列。当前通知关闭，所以只在后台显示。

已移除 Crawl4AI 0.9.4 默认启动参数中的 `AutomationControlled` 隐藏和证书检查绕过；禁用 stealth、magic、navigator 覆盖，不加代理、IP 轮换或验证码处理。使用原生 Chrome UA，保留 `navigator.webdriver=true`。因此不能把旧可行性结果当成当前安全参数下已获准且可长期采集的证明；被拒绝时必须停止，取得许可或允许的接口后再恢复。

## 企业微信通知（目前关闭）

以后需要推送时：

1. 将 `.env.monitor.example` 复制为 `.env`，配置 `WECOM_LECOMTE_WEBHOOK` / `WECOM_SITE2_WEBHOOK` 和可选后台密码。不要提交 `.env`，不要把 Webhook 值粘贴进后台。
2. `docker compose up -d --force-recreate monitor` 使环境变量生效。
3. 在网站设置中填写对应的**环境变量名**，勾选需要的事件和消息推送。没有配置有效 Webhook 的消息保持待处理状态，不泄露密钥。

其他网站可在 Compose `environment` 中增加独立变量，再在后台引用。仅接受企业微信官方 HTTPS Webhook 地址，禁止跟随重定向。

消息包含商品名称、售价、原价、折扣、购买链接、事件编号。快照、事件和通知队列在同一 SQLite 事务提交；每事件只创建一条投递记录。明确限流退避重试，最多 5 次；连接失败可重试。超时或发送进程中断可能已经送达，标记“需核对”并停止自动重发。群 Webhook 没有业务幂等键，不能承诺网络异常下严格 exactly-once；请核对群消息后再点击“核对后重试”。

## 数据保护与恢复

- 数据仅挂载在 `data/`，重建容器保留数据。
- 首次升级前自动备份到 `data/backups/before-migration-*.db`；仅新增 `monitor_*` 表，不覆盖旧表记录。
- 每天 UTC 02:00（北京时间 10:00）用 SQLite Backup API 创建一致性备份，保留最近 30 份每日备份。升级前和手动备份不自动删除。
- 手动备份可在后台点击，或调用 `POST /api/backups`。
- 恢复时先停止服务，将所需备份复制为 `data/snapshots.db`，在服务停止后清理该数据库残留的 `-wal` / `-shm` 文件，再启动。先保留当前库的备份，不在运行时覆盖数据库。
- 快照和历史持续保留，没有自动清空；长期运行需监控磁盘空间，并将备份复制到另一存储设备。Docker 日志每文件 10 MB，保留 3 份。
- 总览的“存储与备份”及 `GET /api/storage` 展示数据库/WAL、备份数量与大小、最近备份时间和文件系统可用空间；低于 2 GiB 或 10% 可用空间、数据库超过 1 GiB、备份超过两天未更新时提示。容器显示其可见文件系统容量，不代表 Windows 所有磁盘。不会自动删除历史或手动备份。
- 商品搜索与分页在数据库中执行，保留 Unicode 大小写匹配和字面量搜索；网站及总览商品计数不再解析全部商品 JSON。

后台默认只绑定 `127.0.0.1`。设置 `ADMIN_PASSWORD` 后使用浏览器 Basic 登录（默认用户名 `admin`）。若后续提供远程访问，应置于有 TLS 和访问控制的反向代理之后，不直接暴露无密码后台。API 拒绝跨站修改请求。

## 自动化测试

```powershell
docker compose run --rm -e RUN_HEADED_TEST=1 monitor python -m pytest tests -q
```

有界面集成测试只访问容器中的本地测试站和模拟 Webhook，不访问 Lecomte 或真实企业微信。它验证完整分页、90→90 四个变化、403 保留基线和浏览器自动化标识。旧 changedetection 集成测试默认跳过。

本机数据库/服务测试（不安装 Crawl4AI 也可运行这些模拟测试）：

```powershell
.venv\Scripts\python -m pytest tests/test_monitor.py -q
```

完整结果见 [测试报告](docs/MONITOR_TEST_REPORT.md)，原测试报告见 [可行性报告](docs/FEASIBILITY_REPORT.md)。

## 开源组件与归属

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).

- [Crawl4AI 0.9.4](https://github.com/unclecode/crawl4ai/tree/v0.9.4)：复用浏览器生命周期和 JsonCssExtractionStrategy；上游许可证为 Apache-2.0，并附归属声明要求，见 [官方许可证](https://github.com/unclecode/crawl4ai/blob/v0.9.4/LICENSE)。
- [APScheduler](https://github.com/agronholm/apscheduler)：复用间隔任务、最大并发、错过任务合并和调度生命周期，MIT。
- [FastAPI](https://github.com/fastapi/fastapi)：复用 API、请求校验、文档与静态文件服务；SQLite 保留已有数据库和事务。
- Playwright、BeautifulSoup、HTTPX、Xvfb、Google Chrome 提供浏览器、解析、消息 HTTP 和显示环境。Crawl4AI 的间接依赖可能含 stealth/patchright 包，但本系统不启用这些机制。

新增代码仅覆盖缺失的多站配置、迁移关联、事件分类、持久通知队列、运行协调、备份和中文业务后台。
## 邮件推送

已支持按网站选择邮件（SMTP）或企业微信推送，收件邮箱可独立设置。发件账号通过部署环境配置，推送默认关闭。详见 [邮件推送说明](docs/EMAIL.md)。


## 新版管理界面

管理后台复用 Tabler 1.6.1 和 Tabler Icons，资源本地部署。详见 [界面更新及验证](docs/UI_REDESIGN.md)。Agent Mail CLI 接入与用户配置见 [Agent Mail 说明](docs/AGENTMAIL.md)。

个人微信通知已支持 pushplus，配置步骤见 [docs/PUSHPLUS.md](docs/PUSHPLUS.md)。

微信扫码直连（腾讯 ClawBot），无需公众号或 pushplus Token，见 [docs/WEIXIN.md](docs/WEIXIN.md)。

Bark（iPhone）与 ntfy（Android/iPhone）手机推送：无需每日激活，配置步骤见 [docs/PUSH.md](docs/PUSH.md)。

### Gmail 发件箱

管理台「通知设置 → Gmail 发件箱」可保存个人 Gmail 地址与应用专用密码、测试 SMTP 登录、发送带附件的邮件及查看最近 50 条手动发送记录。需先开启 Google 两步验证并创建应用专用密码，不使用 Google 登录密码。连接测试不会发送邮件，发送按钮会展示确认窗口。

应用密码加密存于 `data/gmail.enc`，本机密钥为 `data/gmail.key`，均不进入 Git，也不进入 SQLite 备份；拥有这两个文件的人仍可解密，需保护宿主机目录权限。迁移时需同时保留这两个文件。不要将凭据贴到聊天中。

保存后，网站的「邮件（SMTP）」通道优先使用此 Gmail 账号；未配置 Gmail 时继续使用原 SMTP 环境变量。网站通知仍需单独配置收件人并启用。SMTP 接受不等于收件箱送达；超时标为需核对且手动邮件不会自动重发。

### 变化历史筛选

“变化历史”支持按上架、下架、重新上架、降价、涨价和访问异常分类，并组合网站、商品名称或 ID、日期及币种筛选。支持时间、网站、商品名、价格、涨跌金额的正反序；所有筛选和排序在服务端分页前执行。日期使用浏览器本机时区并包含结束当天，价格按原币种排序，不进行汇率换算。当前筛选条件保存在本浏览器标签页。

### 通知投递队列

左侧“通知投递队列”独立展示网站通知的状态、方式、接收目标、尝试次数和结果，支持按网站、方式和状态筛选。“预览 / 详情”只读，不发送消息。后续投递在发送前保存最近一次尝试的渠道、接收目标和正文快照（邮件含 HTML）；历史缺失记录明确标注，预览按事件数据重建，当前网站设置不作为历史投递证据。邮件 HTML 在隔离预览中显示，禁用脚本和外部图片。接口接受不代表最终送达。群机器人或 pushplus 未提供名称时显示配置标识；微信显示绑定用户 ID。


### 多通知方式（每个网站可多选）

网站设置的“通知方式”是多选：勾选的每种方式各填自己的环境变量**名**（企业微信 / pushplus / Bark / ntfy）或收件邮箱（邮件 / Agent Mail）；密钥仍只写在 `.env`。

- 数据：`monitor_sites.notification_channels` 为 JSON 列表 `[{"channel","env","to"}]`，第一项为主方式，并同步到旧字段 `notification_channel` / `webhook_env` / `email_to`（旧字段继续作为回退）。升级时自动把原来的单一方式转成一项列表，行为不变。
- 种子 `config/sites.json` / `SITES_SEED` 两种写法都接受：旧的单字段，或 `"notification_channels": [...]`。
- 投递：每个事件、每种方式一行队列（`UNIQUE(event_id, channel)`），状态、重试、暂缓 / 待核实、去重、邮件合并与推送合并都按方式独立；一种方式失败不会阻塞或重复其他方式。取消勾选的方式，其未发记录转为暂缓，不会改发到别处。
- API 向后兼容：仍可只传旧的 `notification_channel` 等字段；只改旧字段时替换主方式，保留其他已选方式。
- 投递队列可按“发送方式”筛选；单站点“测试推送”按所选 Bark / ntfy 分别发送。

## 运行维护补充

- 总览趋势通过 `/api/overview/trends` 按浏览器当前时区偏移聚合最多 90 天，不再分页下载事件和日志明细；当前网站状态独立取各站最新记录。
- `/api/notifications/diagnostics` 分类显示旧渠道限额、历史配置缺失和送达不确定。仅解释原因，不自动重试或更改送达状态；切换通知渠道不会把历史失败变成成功。
- 每小时记录一次存储采样，保留最多 90 天容量统计；达到 24 小时跨度后估计净增长及剩余天数。预测不包含其他程序磁盘占用，不能替代磁盘监控。
- 数据库恢复演练：`python scripts/verify_restore.py --db data/snapshots.db`。验证现有备份：`python scripts/verify_restore.py --backup data/backups/你的备份.db`。工具在临时目录恢复，不启动任何后台任务，不覆盖运行库。
- 创建保留全部历史的只读归档副本：`python scripts/verify_restore.py --db data/snapshots.db --archive-to data/backups/archive-日期.db`。仅在演练通过后写入新文件；已有目标拒绝覆盖，源历史不删除。归档可复制到另一设备；SQLite 归档不包含 Gmail 密钥、账号配置及桥接卷，整机恢复需另行备份这些配置。


### 不投递的访问告警

历史投递队列里的 `ACCESS_DENIED` 待发送或暂停记录在升级时转为 `suppressed`（不投递），保留事件、尝试次数及原始原因。不计入待处理通知，不能重试。已提交和送达不确定的历史记录不自动改写。正常商品通知仍沿用原有人工核对与重试流程。
