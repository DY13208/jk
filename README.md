# 多网站电商商品监控

正式版本使用 **Crawl4AI 0.9.4 + 原有 diff_sqlite.py + APScheduler + SQLite + FastAPI**，提供中文 Web 管理后台。无需 EasySpider，也不再启动 changedetection.io。此前的研究、代码和回归测试保留供回溯，不参与正式 Compose 调度。

## 当前状态

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

后台：**http://127.0.0.1:8080/**。健康检查：`/health`；API 文档：`/docs`。

容器自带系统 Google Chrome、Xvfb、xauth 和 tini，`xvfb-run` 为有界面 Chrome 提供显示环境，`shm_size: 1gb` 避免共享内存不足。只运行一个 Uvicorn worker、一个服务副本；多个网站在同一服务内独立调度，浏览器采集串行执行。每站默认 15 分钟；启用或重启后的首轮在约 5 秒后运行，此后按各站间隔采集。错过的任务合并，不补跑大量历史轮次。

查看和停止：

```powershell
docker compose ps monitor
docker compose logs --tail 100 monitor
docker compose stop monitor
```

旧 `jk-catalog-1`、`jk-changedetection-1` 若仍存在，会显示 orphan 提示，它们与新服务无关。可停止这两个旧容器，保留旧卷；不要用 `down -v` 删除历史卷。

Linux 上首次部署到绑定目录时，确保 UID 1000 可写入 `data/`，例如对本项目的 `data` 目录设置所有者；容器使用非 root 用户 `pwuser`。Windows Docker Desktop 通常不需要此操作。

## 中文后台使用

左侧包含监控总览、网站管理、商品列表、变化历史、通知设置、执行日志。右上角可以立即创建 SQLite 一致性备份。

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

