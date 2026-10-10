# 正式监控系统测试报告

测试日期：2026-10-10，Windows / Docker Desktop Linux 容器。

## 最终结果

```text
docker compose run --rm -e RUN_HEADED_TEST=1 monitor python -m pytest tests -q
65 passed, 1 skipped, 1 warning in 31.47s
```

包含正式系统 43 项模拟/数据库/API/调度测试、1 项实际 Chrome 完整链路测试，以及此前保留的 21 项回归测试。跳过的是旧 changedetection 远端集成测试，本次正式运行不使用它。警告来自 Starlette TestClient 对 anyio 的弃用接口，不影响通过结果。`pip check` 返回 `No broken requirements found`。

## 必测场景：90 件，数量不变

第一轮本地目录为 ID 0–89，完整采集 3 页，共 90 件；建立基线，0 条通知。

第二轮本地目录为 ID 2–91，同样 3 页、90 件：

|事件|ID|条数|
|---|---|---|
|REMOVED|0、1|2|
|NEW|90、91|2|

实际 Crawl4AI + 有界面 Chrome 提取、原 diff_sqlite 差异、SQLite 提交和本地 HTTP 模拟 Webhook 全部贯通。通知队列处理两次，模拟端点仅收到 4 条消息，没有重复。第三轮的第二页返回 403，停止采集，最近完整快照仍为 90 件、事件仍为 4 条，没有新增下架。

该测试使用本地自有 HTTP 站点，**不是真实 Lecomte 的本轮验证**。原 Lecomte 三轮真实采集证据另见 FEASIBILITY_REPORT.md；本次没有重复采集该站。

## 主要覆盖

|测试|验证结果|
|---|---|
|首次基线|保存完整快照，不创建商品事件或通知|
|RETURNED|与同网站完整历史比较，正确识别重新上架|
|PRICE_DROP / PRICE_RISE|售价下降、上升分别分类；格式变化不误报|
|多网站 ID 隔离|相同商品 ID 在两个网站拥有独立价格和历史|
|无效快照|403、429、数量不符、重复 ID、缺页、不完整标志、缺失价格、NaN 均不覆盖基线|
|真实分页|Chrome 实际访问本地 3 页，提取 90 件；不会只抓第一页|
|分页拒绝|403/429 立即失败；服务暂停该网站并创建访问告警，禁止下架判定|
|robots.txt|访问被拒绝时按 403 暂停，不继续遍历|
|重定向|新采集仅接受已确认最终 200；未知 303 不能入库|
|目录变化|分页中总数变化或重复 ID 失败；已有历史的配置不能悄悄切换目录|
|价格格式|英文/法文的小数与千位分隔正确解析|
|任务防重入|async 排队防重入、SQLite owner 锁与到期恢复|
|独立调度|15 / 30 分钟分别设置，max_instances=1、coalesce=True，调度实际执行；保存设置不重置未变化任务|
|采集中暂停|放弃已抓取结果，不提交快照|
|通知规则|保留所有事件，仅为选中的事件创建通知|
|消息内容|名称、售价、原价、折扣、URL、事件编号|
|通知去重|同采集重放、重复处理已发送队列不再次发送|
|通知异常|超时待核对；限流退避；未配置/关闭/明确拒绝保持暂停；不盲目重试|
|备份与迁移|原 runs、snapshots、changes 逐行不变；升级前备份、手动备份完整性 ok|
|Windows 挂载|拒绝 POSIX chmod 时仍可迁移、写快照和一致性备份；数据库写权限失败本身不会被忽略|
|后台 API|六个业务页面、配置校验、跨站修改阻止、可选 Basic 登录|

## 真实旧数据保留

- 原数据库副本 SHA-256 与用户目录源数据库一致。
- 升级后的原表：runs 6 条、snapshots 426 条、changes 0 条，全部保留。
- 正式完整历史关联：Crawl4AI 3 轮 × 119 件；旧 EasySpider 3 轮 × 23 件仍在原表，但不进入正式基线。
- 历史导入没有生成新品事件，实际通知队列 0 条。
- `snapshots_LOGICTEST_copy.db` 未复制到本项目，也未参与迁移。
- 正式数据库及生成的备份 `PRAGMA integrity_check` 均为 `ok`。

## Docker 与浏览器

- `docker compose build monitor` 成功，正式容器 `jk-monitor-1` 启动。
- 后台、`/health`、`/api/overview`、`POST /api/backups` 实际返回 HTTP 200。
- 概览实际返回 lecomte 119 件、site2 0 件、3 次完整快照、0 条待处理通知。
- 使用 Crawl4AI 0.9.4、APScheduler 3.11.0、FastAPI 0.115.12、Playwright 1.51.0、Python 3.12、Google Chrome 155.0.8059.39。
- `xvfb-run` 为 Uvicorn 及其浏览器子进程提供 DISPLAY。普通 `docker compose exec` 不继承 xvfb-run 子进程的环境；headed 测试通过 `compose run` 经同一 ENTRYPOINT 启动。
- 实际 Chrome 页面钩子读得 `navigator.webdriver=true`；安全扩展没有隐藏自动化标识，不启用 stealth / magic / navigator override。
- 具体构建依赖版本记录在 `validated-requirements.txt`；Chrome 稳定源版本可能随后续构建变化。

## 中文后台浏览器验收

在应用内浏览器实际访问后台，完成六个导航页面、暂停网站设置保存、商品分页和手动备份。

- 商品第一页 100 条，第二页 19 条，共 119 条。
- 企业微信设置显示两个网站均关闭、Webhook 未配置。
- 桌面 1440px：侧栏 304px，导航行高 42px，KPI 4 列，无页面横向溢出。
- 折叠侧栏 80px，无页面横向溢出。
- 手机 390px：导航 72px，KPI 1 列，无页面横向溢出。
- wide table 仅在表格容器内滚动。
- 后台预览保存于 `admin-preview.jpg`，手机截图 `admin-mobile.png`；桌面截图因应用内截图范围限制只包含局部，桌面尺寸另有 DOM 测量证据。

## 未宣称验证的范围

本次没有取得站方自动化访问授权，没有在移除上游反检测默认参数后重新抓取 Lecomte，没有实际向企业微信发送消息，也没有进行连续多日运行验证。真实站点和通知均默认关闭。已验证的是正式系统的软件行为、Docker 显示环境和本地完整链路，不将其当成生产访问许可或长期可用性的保证。

Lecomte SKU 与尺码库存在现有数据中没有可靠字段，显示未知；动态加载不同于当前分页结构的新网站，需要独立适配器和相应测试。
