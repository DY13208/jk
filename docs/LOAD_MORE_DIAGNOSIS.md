# Lecomte 分页诊断（2026-10-15）

## 已实现

复用 Crawl4AI 官方的 session_id、js_only、js_code_before_wait 和 wait_for，在同一页面点击站点自带的“加载更多”。配置示例：

```json
{"pagination_mode":"load_more","page_delay":3}
```

默认仍为原来的 links 模式，未自动重新启用 Lecomte。

每次加载提取累计商品集合，要求数量增长、不丢失已有 ID、无重复 ID、已有商品的价格及名称不在采集过程中变化、站点总数和页数不变。每页快照证据记录新增部分的数量，最终商品总数必须一致。监听同源 document/xhr/fetch 的 403/429，避免 AJAX 出错却把主文档的 HTTP 200 误认为采集成功。

## 实测与边界

内置正常浏览器：站点按钮成功加载 23 → 47 → 71 → 95 → 113 件。最终 DOM 中 113 个商品卡片、113 个唯一稳定商品 ID，页面声明 113/113。这证明网站确有正常动态分页通路，不等于 Docker 自动采集也已成功。

Docker 原生 Chrome/Crawl4AI：点击按钮后仍发生 document 导航并返回 HTTP 403。分别验证了法语请求头、等待 load 和延长脚本初始化等待，以及 Crawl4AI 的 fr-BE locale；均未恢复完整采集。locale 改变后拒绝路径从 `/en/shop/page/2` 变为 `/shop/page/2`，所以不能把语言路径本身认定为根因。

没有提交残缺快照、重启站点监控或发送通知；不能宣称已修复 Lecomte 的全天候采集。

## 自动化验证

`docker run --rm -e RUN_HEADED_TEST=1 jk-monitor python -m pytest -q`

137 passed，1 skipped，1 warning（Starlette 依赖弃用提示），41.35 秒。
新增真实 Chrome 本地自有页面测试：点击加载三批共 90 件，直接分页路径刻意返回 403；程序成功使用页面按钮完成全部采集，没有直接导航分页 URL。
另有累计集合重复、缺失、无增长、价格中途变化、不完整以及 HTTP 403 的回归测试。

参考官方文档：https://docs.crawl4ai.com/core/page-interaction/
