# 开发与适配器接口

## 目录

|位置|责任|
|---|---|
|crawl4ai_run/run_crawl4ai.py|原 CSS 提取规则、Odoo ID 规则，配置化分页、完整性检查、安全浏览器扩展|
|diff_sqlite.py|原 runs/snapshots/changes 与稳定 ID 差异算法；新增可控事务参数和本机默认路径|
|adapters/|独立网站采集函数与注册表|
|monitor/database.py|增量建表、旧证据导入、原 diff 整合、事件与 outbox 的原子提交|
|monitor/service.py|APScheduler 生命周期、任务防重入、串行浏览器、异常暂停、每日备份|
|monitor/notifications.py|企业微信消息、HTTPX、持久投递状态、退避与不确定状态处理|
|monitor/web.py|FastAPI 管理 API、配置校验、Basic 登录、跨站修改限制|
|admin/|中文业务后台；不含爬虫或调度实现|
|legacy/|用户提供的原始脚本、真实数据证据和旧数据库，仅用于回溯/测试|

`monitor/app.py`、`monitor/collector.py`、`monitor/store.py` 与旧配置脚本属于此前 changedetection 方案，保留兼容回归，生产入口为 `monitor.web:app`。旧 Bash 轮询脚本存为 `legacy/monitor_15min.original.sh`，保留原 Linux 路径作证据，不再执行它；APScheduler 替代 shell 轮询。

## 添加独立适配器

实现 `async def collect_site(site: dict) -> dict`，在 `adapters.REGISTRY` 注册唯一名称。`site` 包含 URL、adapter_config 等。适配器只能通过站点允许的公开访问方式采集；拒绝访问必须抛出 `CollectionError(message, status=403/429)`，由服务暂停并记录告警。

成功返回：

```json
{
  "meta": {
    "tool": "crawl4ai", "started": "2026-10-10T07:00:00+00:00",
    "ended": "2026-10-10T07:00:24+00:00", "duration_s": 24,
    "complete": true, "site_total": 1, "site_pages": 1, "extracted": 1,
    "pages": [{"page": 1, "status": 200, "items": 1}]
  },
  "products": [{
    "product_id": "123", "name": "商品名称", "sale_price": "100.00",
    "original_price": "125.00", "discount": "-20%", "currency": "EUR",
    "url": "https://example.test/products/123", "sku": null, "stock": "UNKNOWN"
  }]
}
```

`site_total` 必须来自权威目录总数或等价完整结束证据，不能填写 `len(products)` 假装核对完成。动态加载适配器需验证终止状态、请求是否成功、跨批次 ID 重复和目录是否改变，再构造每批 evidence。页面不足、重复 ID、关键字段缺失时抛异常，不返回伪成功。

`started` 是每轮唯一且带时区的时间。业务身份由 service 注入网站 ID，工具字段存为 `crawl4ai:<site_id>`，兼容原 runs 的 `(tool,started)` 唯一约束。原 diff 对指定前后 run_id 做 ID 集合差；业务层以同网站完整历史识别 RETURNED。

## 事务和重启

同一事务写 runs、snapshots、changes、monitor_run_meta、monitor_details、monitor_events、monitor_outbox。调用原 ingest/diff 时使用 `commit=False`。任何异常回滚整轮。重放同网站同 started 不创建重复事件或通知。

APScheduler `max_instances=1` + `coalesce=True`；单进程 async active 集合避免重复排队；SQLite 带到期时间的 owner 锁防止同时提交相同网站。采集 timeout 300 秒，锁租期 360 秒；异常和正常完成都释放 owner 锁。服务中断后锁到期自动恢复。部署仅支持一个 worker/副本，避免多实例调度争抢和任务日志生命周期冲突。

没有完整证明的旧轮次仍保存在原表中，但不用于正式基线。三轮 Crawl4AI 的第一页日志保存初始 303；仅在历史导入时结合原报告、成功字段、完整 5 页、119 个唯一 ID 和数据库 ID 集合接受这一旧证据。新轮次必须读取 `redirected_status_code` 并确认最终 200，不把未知重定向当成功。

## 浏览器扩展维护

上游 v0.9.4 的 `BrowserManager._build_browser_args` 默认含 `--disable-blink-features=AutomationControlled`。本项目使用小型子类过滤该参数及证书绕过，不修改上游源码、不启用 stealth。不支持未经重新核查的其他 Crawl4AI 版本，版本检查失败时停止采集。见 [官方 v0.9.4 源码](https://github.com/unclecode/crawl4ai/blob/v0.9.4/crawl4ai/browser_manager.py)。

Chrome 来自官方稳定渠道，构建版本可随时间变化；Playwright 基础镜像固定 digest，Crawl4AI 和主要依赖固定版本。升级后必须运行 headed 集成测试，确认 Chrome 生命周期、`navigator.webdriver=true`、分页和故障行为。测试固定环境清单会记录在测试报告中。
