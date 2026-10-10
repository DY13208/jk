# 研究记录（2026-10-10）

实际克隆上游 master，commit `75a508fecc37483e936795f1e3f9b076e375cf0f`；版本文件和 GitHub 最新 release API 均为 0.60.8。Compose 固定此版本，避免 latest 漂移。上游源码位于本地 `vendor/changedetection`（不复制到自有代码中）。

## changedetection.io

- Apache-2.0，允许商业使用和修改；若分发上游代码须保留许可证及适用的 NOTICE。本项目不修改上游。
- `docs/api-spec.yaml`：`/api/v1/watch` GET/POST、`/watch/{uuid}` GET/PUT、history/snapshot API，认证 `x-api-key`；运行实例 `/api/v1/full-spec` 是合并处理器扩展后的完整规范。
- 可配置 HTML/CSS/XPath/JSON 过滤、Browser Steps、每 watch 调度、通知和历史；通知通过 Apprise（上游 requirements 锁定 2.0.1）。
- `changedetectionio/processors/restock_diff/processor.py` 标明 SINGLE product；多价格报错。它不承担完整目录分页及跨快照 SKU 生命周期。
- 处理器插件可带 `api.yaml`；官方 Compose 也支持 EXTRA_PACKAGES。定制处理器可实现需求，但依赖内部 worker/watch 存储接口，维护成本高于 HTTP 适配服务。
- `changedetectionio/worker.py`：第二份及以后的历史快照才发变更通知；首次成功读取建立基线。
- `model/App.py`：请求超时环境变量 `DEFAULT_SETTINGS_REQUESTS_TIMEOUT`。本项目设 300 秒，采集服务默认运行预算 240 秒。
- 配置简单过滤器只能比较页面内容，不保证全目录、稳定身份、重新上架和完整性。故配置不足，增加小型目录适配服务，保留原调度/UI/通知/历史。

## 目标网站

普通请求：`curl.exe -L -s -o target.html -w '%{http_code}' 'https://www.lecomte-alpirando.be/shop?attribute_value=729-2672'` 返回 **403**；响应正文 `Your request was blocked.`。网页读取工具也未成功。没有采取代理、伪装、验证码处理或绕过控制措施。

**不能确认网站技术栈、页数、总数、产品 ID/SKU/库存结构。没有真实网站采集成功证据。** 网站 1 的选择器未填写，默认暂停。需要网站方允许的公共访问、官方目录 API/导出或授权环境中确认结构后再完成适配。网站 2 未提供 URL，同样暂停。示例选择器只属于本项目的测试页面。

## 选择理由与备选

changedetection.io + Playwright + BeautifulSoup + SQLite。前两者复用成熟监控和浏览器引擎，后两者用于缺失的字段映射和事务快照。仅开发业务适配，不创建调度器、通知框架或管理后台。当前规模每站一项目录任务；未引入 Scrapy，因为再运行一个爬虫任务系统会增加部署与重复调度成本。将来大型目录需要限速、队列和多域抓取时可替换 collector 为 Scrapy，HTTP/feed 契约不变。

## 来源

- https://github.com/dgtlmoon/changedetection.io
- https://github.com/dgtlmoon/changedetection.io/blob/master/docs/api-spec.yaml
- https://github.com/dgtlmoon/changedetection.io/blob/master/LICENSE
- https://github.com/dgtlmoon/changedetection.io/blob/master/changedetectionio/processors/restock_diff/processor.py
- https://github.com/caronc/apprise/blob/master/apprise/plugins/wecombot.py
- https://github.com/caronc/apprise/blob/master/apprise/plugins/telegram.py

Apprise 源码也实际克隆核查：`f746ec5659da5d59cfd75db49ad57bd75c4acdd2`。企业微信机器人是 `wecombot://KEY`；`wxteams://` 是 Cisco Webex，不能混用。部署镜像内的实际插件另用 smoke 测试验证。

运行镜像核验：changedetection 0.60.8，Apprise **1.13.1**，与当前 master requirements 的 2.0.1 不同；企业微信和 Telegram 插件均在实际镜像内通过 mock HTTP 请求测试。镜像的 SSRF 防护默认拒绝私网地址，需要 `ALLOW_IANA_RESTRICTED_ADDRESSES=true` 才能访问本系统的内部 catalog 服务；因此管理后台限定 localhost，不应直接向不可信用户开放创建任意 watch 的权限。这是访问自有内部适配服务的部署配置，未用于访问或绕过目标网站的 403。
