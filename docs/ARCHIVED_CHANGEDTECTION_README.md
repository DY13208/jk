# 多网站电商目录监控

复用 changedetection.io 0.60.8 的后台、15 分钟调度、历史和 Apprise 消息推送。新增目录适配服务提供完整采集、成功快照和商品身份差异。

**当前真实站点未启用：Lecomte 返回 HTTP 403，第二站 URL 未提供。以下代码在合成目录上可运行和测试，不代表两个实际网站已接入。** 研究证据见 [docs/RESEARCH.md](docs/RESEARCH.md)。

## 启动

```powershell
Copy-Item .env.example .env
docker compose up -d --build changedetection catalog
```

打开 http://localhost:5000，使用原后台设置登录密码。在 Settings > API 复制 API key 到 `.env` 的 `CD_API_KEY`。不要把 `.env` 加入版本控制。管理界面仅绑定本机，适配服务不公开宿主端口。

```powershell
docker compose --profile setup run --rm configure
```

此命令通过官方 API 幂等创建/更新两个监控任务，当前均暂停。配置读取目录为 `config/sites.yaml`。修改后再执行 configure；服务每次请求重新读配置，无需重建镜像。后台可查看任务、错误、变更历史、暂停及手动检查，也可测试通知。

本机 Docker Desktop 也可使用 `python scripts/setup_local.py`，它直接在进程内读取本机容器 API key，不打印或落盘。若采用此方式，通知环境变量须提前设置到该进程；否则通过 `.env` + configure 服务读取。

## 配置两个网站

网站 1 保留真实 URL；得到允许访问的结构证据后，把 `config/example.yaml` 的采集配置块复制到 `lecomte`，填入**实际验证的选择器**再启用。网站 2 填 URL，按同样方式填写；新增站点只需增加一个唯一配置键并运行 configure。

每站 `interval_minutes` 默认 15；`events` 可选择 added、removed、relisted、price_changed、stock_changed；`notification_env` 指定该站要用的环境变量名。

字段映射规则：`selector` 是商品卡片内 CSS 选择器，省略表示卡片自身；`attribute` 读取属性，省略则读文字；`regex` 使用第一个捕获组。`id` 必须是站内稳定商品/变体 ID，不能是页面位置或商品名称；SKU 有变体时以变体粒度定义目录和总数。固定 ID 提取规则后不要改变，否则会建立错误身份变化。SKU、名称、价格、链接、库存必须有值；缺失拒绝整次采集。原价未展示可为 null，折扣按当前价/原价计算百分比，不能推测库存。现有通用适配器只读取目录呈现字段；如字段仅在详情/API 中存在，需要基于授权结构补充适配，不能宣称已支持该网站。

`total` 必须提取当前筛选条件下的**完整目录商品总数**，regex 可提取数字；它不是每页数量。`next_selector` 指向下一页有效链接，`terminal_selector` 必须只在末页/加载完成时出现。不能用每页共有的 footer 当终止证据。总数校验针对唯一 ID，与提取粒度一致。

动态加载支持 `dynamic: load_more` + `load_more_selector`，或 `dynamic: scroll`。通过 Playwright 真浏览器执行，只有终止标记和总数吻合才成功；时间稳定、按钮消失、步骤耗尽均不能单独当作完整证据。每次任务执行两次完整遍历，商品身份和字段必须一致，否则拒绝提交，降低分页期间商品重排导致的误判。没有服务端一致性版本时仍无法数学保证远程目录的原子快照。虚拟列表会移除先前 DOM 节点的站点目前应使用专用 API 适配；通用配置会因总数不足而拒绝提交。

分页支持 max_pages、循环和跨域链接检查；run_timeout 默认 240 秒。当前串行采集，一次超过预算的站点需要官方导出/API 或适配优化。changedetection 调度为其原有运行语义，不保证墙钟严格每 :00/:15 触发。

## 通知

`.env` 配置：

```dotenv
WECOM_URL=wecombot://YOUR_GROUP_BOT_KEY
TELEGRAM_URL=tgram://YOUR_BOT_TOKEN/YOUR_CHAT_ID
```

需要两种通知的站点填 `notification_env: [WECOM_URL, TELEGRAM_URL]`；只需要一种则只填一种。configure 缺少所选变量会报错，不静默跳过。消息使用上游 `{{ diff_added }}` 模板，仅发送新的商品变更事件。Telegram token 示例真实形状含冒号，群 chat ID 通常为负值。企业微信使用群机器人，不是第三方 PushPlus。

没有选择通知变量的站点由 configure 设为静音，避免意外继承后台全局通知；填写渠道后再次配置会取消静音。配置脚本会覆盖该任务的调度与通知字段；直接在 UI 改动后不要运行旧配置覆盖它。

首次采集不产生商品事件；changedetection 首次读取 feed 不推送。后续每个事件带唯一序号、时间、类型、商品 ID/SKU、名称、价格、原价、折扣、URL 和库存，价格/库存变化带 previous。同样总数也照常比较 ID 集合。重新上架表示以前成功快照出现、上一成功快照没有、当前重新出现；库存补货另以 stock_changed 表示。

推送队列和失败记录复用上游。没有添加自建可靠投递队列；不要把上游消息发送等同于收件人已读或 exactly-once 投递。凭据未提供时只验证插件构造及 mock HTTP 请求，真实投递需在原后台发送测试通知确认。

## 快照、失败与备份

`data/catalog.sqlite` 保存每次成功采集的全部商品（包括内容未变的运行）、完整性证据、事件和失败类型。事务提交，失败不改变基线、不会产生下架事件；feed 返回 503，让原后台显示错误。网络/403、字段缺失、重复 ID、总数不符、跨域、分页循环、页数上限均失败关闭。零商品只有明确总数 0 和终止证据时才成功。

Feed 是追加的事件日志，避免把事件消失当作新变化；规则只控制通知事件，完整快照始终保存。后台已有文本历史用于通知审阅；完整 JSON 商品快照在 SQLite。内部只读 `/sites/{site}/latest` 可查看最近成功目录。无独立商品管理 UI。备份时停止 catalog 再复制整个 data 目录，并备份 changedetection-data Docker volume；两者需一起恢复。事件日志与快照目前不自动清理，长期运行需要磁盘监控与离线归档；不要单独截断 feed，否则会影响上游比较基线。

不要重建 watch 来恢复通知历史；新 watch 以当前 feed 作为基线。如需改变身份粒度，应使用新站点键主动建立新基线。

## 测试

```powershell
docker compose build catalog
docker compose run --rm catalog python -m pytest tests -q
docker compose exec -T changedetection python -c "import runpy; runpy.run_path('/tmp/notification_smoke.py', run_name='__main__')"
```

最后一条先执行 `docker compose cp scripts/notification_smoke.py changedetection:/tmp/notification_smoke.py`。它使用镜像内真实 Apprise、模拟 HTTP；不向企业微信或 Telegram 发送真实消息。测试结果见 [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md)。

本机 Python 环境安装 requirements 并执行 `python -m playwright install chromium` 后，可运行 `python scripts/run_e2e.py`：真实后台连接本机临时测试目录和 Webhook，验证首次静默、90→90 替换四个身份、403 保留快照，最后删除临时 watch。测试会临时占用 8091/8092 端口。普通 `pytest` 默认跳过此集成测试。

## 复用与新增边界

| 功能 | 实现 |
|---|---|
| 管理后台、任务调度、暂停/手动检查 | changedetection.io 原有 |
| 文本变更历史、diff、通知模板、消息渠道 | changedetection.io + 原生 Apprise |
| Docker 管理后台部署 | 上游镜像，新增 Compose 编排 |
| 页面 JS 执行 | 成熟 Playwright 浏览器 |
| 完整分页/动态列表字段映射和完整性门槛 | 新增 collector |
| ID 集合差异、下架/重新上架、价格/库存变化 | 新增业务模块 |
| 每次成功 JSON 快照、事务事件日志 | 新增 SQLite 存储 |
| 配置转官方 API watch | 新增幂等配置脚本 |

自有代码按 Apache-2.0 发布；上游及依赖保留各自许可证。无提交、推送或外部生产发布。
