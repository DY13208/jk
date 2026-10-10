# 验收记录（2026-10-10）

## 已实际执行

- 当前完整本地测试：`python -m pytest tests -q` → **21 passed, 1 skipped**，28.69 秒。skip 是默认关闭的真实 changedetection 集成测试；随后已单独执行成功。
- 真实 changedetection 集成：`python scripts/run_e2e.py` → **1 passed**，37.15 秒。使用实际 0.60.8 Docker 实例，采集适配服务及三页/90 商品目录在本机，浏览器真实抓取；通知投递到真实本地 HTTP Webhook。
- 首次三页完整 90 件仅建基线，没有 Webhook 消息。
- 第二次 ID 集合从 0..89 改为 2..91，依旧 90 件；通知文本断言 **2 added + 2 removed**，同时包含 SKU-90、SKU-91、SKU-0、SKU-1。
- 随后目录返回 403，changedetection 显示错误、历史仍为 2 个版本，SQLite 最近完整商品快照不变，Webhook 消息数不增加。
- Docker 内原生 Apprise smoke：企业微信 `wecombot` 和 Telegram `tgram` 均构造并发送了符合各自官方域名的 HTTP 请求；mock 返回成功。**没有真实外部渠道投递证据。**
- 实际镜像版本：changedetection **0.60.8**，Apprise **1.13.1**。
- 两个实际网站的任务通过官方 API 成功创建，默认暂停。重复运行使用 URL 匹配进行更新。
- `docker compose config --quiet` 通过；Python compileall 通过。

## 自动测试范围

| 场景 | 结果 |
|---|---|
| 90 件，2 下架 + 2 新增，总数相同 | 精确四个事件 |
| 首次建基线 | 零事件/零消息 |
| 重新上架 | relisted，区别 added |
| 价格变化及变回旧价 | 每次新事件 |
| 原价与折扣计算 | 正常 |
| 顺序及价格文字格式改变 | 无误报 |
| 无变化成功采集 | 仍保存新快照 |
| 库存变化 | stock_changed |
| 两站相同商品 ID | 独立处理 |
| 规则关闭下架通知 | 不进入通知 feed，快照照常更新 |
| 重复/缺失 ID、无效价格、总数不符 | 拒绝写入 |
| 数据无完整性证明 | 拒绝写入 |
| 完整分页两页 | 全部 4 件，执行两次一致遍历 |
| 点击加载更多 / 滚动动态加载 | 全部 4 件 |
| 第一页 403 / 第二页 403 / 残缺末页 | 拒绝整次采集 |
| 两次完整遍历身份不一致 | 拒绝写入 |
| HTTP 服务失败/停用/未知站点 | 503/503/404；原基线保留 |
| 每站间隔及通知配置 | 默认 15 分钟，可独立修改 |

## 修复与限制

首次浏览器测试因 Chromium 尚未安装失败；安装后发现隐藏总数节点默认可见性等待不合适，改为 attached。动态测试夹具的 onclick HTML 转义问题也已修复。集成发现上游私网 SSRF 防护拒绝内部适配 URL，已显式配置内部访问并保留 localhost 管理端口；修复后集成通过。这些失败不算通过证据。

Windows Python 3.14 下 FastAPI/Uvicorn 的第三方 asyncio/WebSocket 废弃警告，不影响断言；Docker 使用其镜像内 Python。

**未完成真实站点验收**：Lecomte 普通请求 403，未验证选择器、SKU 和库存来源；网站 2 无 URL。没有绕过限制、没有伪造完整商品采集。没有提供企业微信/Telegram 凭据，故外部投递尚待设置后测试。默认 15 分钟配置经过 API/字段验证，不声称已观察完整 15 分钟真实站点周期。

## 最终 Docker 部署验收

- `docker compose build catalog` 成功。
- `docker compose run --rm catalog python -m pytest tests -q` → **21 passed, 1 skipped**，17.40 秒；真实 Chromium、Python 3.12。唯一警告为第三方 Starlette/AnyIO 废弃别名。
- 当前源码最终端到端复测：**1 passed**，41.19 秒。
- `docker compose up -d catalog` 成功，管理后台和采集服务均运行。
- 后台主页 HTTP 200；官方 API 读回 Lecomte、Website 2 两个任务均为 `paused=true`、`time_between_check={minutes:15}`。
- 企业微信/Telegram 未填写时任务静音，避免意外继承全局通知；填写渠道并再次配置后取消静音。

完整源代码交付包由 `python scripts/package_source.py` 生成于 `dist/catalog-monitor-source.zip`，包含 Docker Compose、配置示例、测试、操作及研究文档，不包含 `.env`、数据库、浏览器缓存或 vendor 克隆。
