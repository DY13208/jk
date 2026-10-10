# 抓取可行性测试报告：lecomte-alpirando.be Arc'teryx 列表

- 目标：`https://www.lecomte-alpirando.be/shop?attribute_value=729-2672`（Odoo 站点，`<meta name="generator" content="Odoo">`，Theme Prime 主题；筛选项 `729-2672` = **Brand: Arc'teryx**；303 跳转到 `/en/shop?...`）
- 测试时间：2026-10-10 14:42 – 15:17（Asia/Shanghai，下同）
- 环境：box（Debian 13，Python 3.13，Xvfb 显示 :17，系统 Google Chrome 154）
- 所有数据均为真实抓取，无 mock。

## 0. 结论速览

| 项目 | Crawl4AI 0.9.4 | EasySpider 0.6.5（官方 Linux 执行器） | 原生 Playwright（对照） | curl（对照） |
|---|---|---|---|---|
| 是否成功（全量） | **是** | **否**（只拿到第 1 页） | 否（只拿到第 1 页） | 否（403） |
| 产品数 | **119 / 119**（3 次运行均一致） | 23 / 119（3 次运行均一致） | 23 / 119 | 0 |
| 翻页 | 5/5 页全部 200 | 第 1 页成功，点 "Load more" 进入第 2 页即被 Cloudflare 403 | 第 2–5 页 403 | 第 1 页即 403 |
| 单次耗时 | 23.3 / 24.1 / 23.8 s | 23.3 / 25.0 / 23.0 s（但只采到 1 页） | 17.8 s | — |
| 字段完整度 | 119 条 × 全部字段 100% 非空 | 23 条 × 全部字段 100% 非空 | — | — |
| 与浏览器视图一致性 | 第 1 页 23 条与对照逐条（ID、顺序、名称、价格、URL）完全一致；总数=站点显示 119 | 23 条与对照、与 Crawl4AI 完全一致 | 基准 | — |

推荐：15 分钟监控用 **Crawl4AI（有头 Chrome + JsonCssExtractionStrategy）+ SQLite 差异比对**；但见第 5 节的合规提示——网站的 Cloudflare 明确在拦截"被识别为自动化"的浏览器，Crawl4AI 能通过很可能依赖其默认启动参数 `--disable-blink-features=AutomationControlled`，正式上线前建议征得站方许可（或请求 feed/API/白名单）。

## 1. robots.txt 与站点防护

- `robots.txt`（200）：`User-agent: *` 只禁止 `/my`、`/shop/wishlist`、`/web/*` 部分、`/odoo`、utm/gclid 等追踪参数、以及 **`/*?page=` 与 `/*&page=`**。注释明确说明：真正的列表分页是路径形式 `/shop/page/N`，**允许抓取**。`ClaudeBot`、`meta-externalagent` 被整站禁止（本测试未使用这些 UA）。
- 本测试只访问 `/shop?attribute_value=729-2672` 和 `/en/shop/page/{2..5}?attribute_value=729-2672`，均为 robots 允许路径；未使用 `?page=`。Crawl4AI 开启了 `check_robots_txt=True`。
- 防护：Cloudflare（`server: cloudflare`、`cf-ray`）。被拦截时响应为 `HTTP 403`，`content-type: text/plain`，正文 `Your request was blocked.`，`server-timing: cfEdge;dur=5,cfOrigin;dur=0`（边缘直接拦截，未到源站），`x-robots-tag: noindex, follow`。无验证码/JS challenge 页面，无 `Retry-After`，**未出现 429 限速**。
- 403 实测记录（`logs/curl_evidence.txt`，以及下文各工具日志）：
  - curl（Chrome UA + 普通 Accept 头）：`/shop?...` → 403（cf-ray a483a5a8a8710584-IAD、a483c1591cbc202a-IAD）；`robots.txt` → 200。
  - Playwright 自带 Chromium 无头（UA 含 `HeadlessChrome`）：第 1 页即 403。系统 Chrome 无头：403。
  - 系统 Chrome **有头**（原生 Playwright，默认 UA `Chrome/154`，navigator.webdriver=true）：第 1 页 200，**随后同一会话的第 2–5 页全部 403**；再测：新会话直接开第 2 页 200，8 s 后第 3 页 403、带 Referer 的第 2 页 403 → 判断为 Cloudflare 基于会话/浏览器指纹的 Bot 识别（首个请求放行，执行 JS 检测后后续请求被拦），与请求间隔无关。
  - Crawl4AI 无头（默认 UA `Chrome/116`）：第 1 页 200，第 2 页 403（"Blocked by anti-bot protection: HTTP 403"）。
  - Crawl4AI 有头：5 页全部 200（3 次运行，共 15 次请求）。
- 按要求：**遇到 403 时没有做任何绕过**（没有开启 Crawl4AI 的 `enable_stealth`/`magic`/undetected 适配器，没有更换代理/IP，没有清 cookie 换身份重试，没有用 EasySpider 的 Cloudflare 模式）。

## 2. Crawl4AI

- 安装：`python3 -m venv venv && pip install crawl4ai`（0.9.4）→ `crawl4ai-setup`（下载 Playwright chromium-headless-shell 等，完成 DB 初始化）。
- 脚本：`crawl4ai_run/run_crawl4ai.py`，只用公开 API：`AsyncWebCrawler` + `BrowserConfig(headless=False, chrome_channel="chrome")` + `CrawlerRunConfig(JsonCssExtractionStrategy(schema), check_robots_txt=True, cache_mode=BYPASS, session_id=..., delay_before_return_html=1.5)`；页间额外 sleep 1.5 s，串行（并发 1）。
- 翻页：从第 1 页读取站点的分页信息（`tp-product-pager data-page-count="5" data-total-count="119"`、"Showing 23 Of 119 Results"、隐藏的 SEO 分页 `/en/shop/page/2..5`），然后依次访问 `/en/shop/page/N?attribute_value=729-2672`。
- 结果：

| 运行 | 开始 | 结束 | 耗时 | 每页状态/条数 | 产品数 |
|---|---|---|---|---|---|
| run1 | 14:46:46 | 14:47:09 | 23.29 s | 303→200/23, 200/24, 200/24, 200/24, 200/24 | 119 |
| run2 | 14:59:35 | 14:59:59 | 24.12 s | 同上 | 119 |
| run3（15 分钟循环第 2 轮） | 15:14:35 | 15:14:59 | 23.79 s | 同上 | 119 |
| 无头探测 probe | 14:46:32 | 14:46:39 | 7.7 s | 第 1 页 23 条，第 2 页 403 | 23 |

- 字段（119/119 全部非空）：`product_id`（卡片 `data-product-template-id`，= Odoo product.template id，与 URL slug 末尾数字 119/119 一致）、`product_variant_id`（表单隐藏域 `product_id`，默认变体 product.product id）、`name`、`brand`、`sale_price`（`itemprop=price`）、`original_price`（`del.compare-price`）、`currency`（EUR）、`discount`、`label`（Promotion/Best deal/End of line）、`url`（去掉 query 的规范 URL）、`listing_page`。
- 说明：该品牌筛选下 119 个商品**全部处于打折状态**（-20% 94 个、-30% 16 个、-40% 6 个、-37%/-43%/-50% 各 1 个），所以原价字段 100% 有值；Odoo 列表按"颜色"拆成独立 template（如 Beta AR Jacket Women Black / Nightscape），尺码变体不在列表页。
- 判定：**成功**。

## 3. EasySpider

- 文档：读了 README、ExecuteStage/Readme.md、Release notes、Wiki 的命令行参数说明；执行阶段命令 `easyspider_executestage --ids [id] --read_type local --headless 0/1 --config_folder ./ --keyboard 0`。
- 安装：下载官方 `EasySpider_0.6.5_Linux_x64.tar.xz`（自带 Chrome 151 + chromedriver 151 + PyInstaller 打包的执行器 `easyspider_executestage`），并 `git clone` 源码供阅读。Debian 13 上无需额外依赖。
- GUI：Electron 主程序可以在 Xvfb 上启动（本地服务 `localhost:8074/8075` 正常），但设计任务需要人工在图形界面里点选元素，无法自动化 → 按要求**手写任务 JSON**：`easyspider_run/build_task.py`，完全按照 GUI 生成的 0.6.5 节点格式（模板为安装包自带示例 `tasks/327.json`"提取列表 + 循环点击下一页"）：
  `打开网页 → 循环(类型0 单元素：//a[contains(@class,'tp-load-more-btn')]) { 循环(类型1 不固定列表：//div[contains(@class,'tp-product-item')]) { 提取数据(9 个字段) }, 点击 Load more }` + 结束后一个诊断提取节点（当前 URL / 标题 / body 文本，用于记录拦截页面），`removeDuplicate=1`。
  任务写入 `tasks/1001.json`；执行实例通过 **EasySpider 官方服务 API** `POST http://localhost:8074/invokeTask (id=1001)` 生成（返回 eid 1002），与手写的 `execution_instances/1001.json` 逐字段比对**完全一致（除 id）**。
- 执行：官方二进制 `EasySpider/resources/app/chrome_linux64/execute_linux64.sh`，有头（Xvfb），`--read_type local`。包装脚本 `easyspider_run/run_easyspider.py`（计时 + 将 EasySpider 原生 CSV 转为统一 JSON/CSV）。
- **重要发现（合规）**：EasySpider 执行器的 Python 源码里**硬编码、无开关地**注入 `stealth.min.js`（puppeteer-extra-stealth 反检测脚本）并把 `navigator.webdriver` 改成 undefined，还默认加 `--disable-blink-features=AutomationControlled`。这属于"stealth 反检测手段"。为遵守"不使用 stealth"的要求，又不改核心源码：我把安装包**硬链接复制**为 `EasySpider_Linux_x64_nostealth`，只把复制目录里的资源文件 `chrome_linux64/stealth.min.js` 换成空注释（sha256 见 `easyspider_run/sha256_check.txt`；执行器二进制 sha256 与原版相同，Python/二进制均未修改）。正式运行 run1–run3 全部用的是这个无 stealth 的副本。（另：最早一次无头探测 `logs/easyspider_probe_headless.log` 是在发现之前用原版包跑的，含 stealth，结果 0 条——第 1 页就没有商品卡片。）
- 结果：

| 运行 | 开始 | 结束 | 耗时 | 结果 |
|---|---|---|---|---|
| probe（无头，原版） | 14:53:11 | 14:53:24 | 13.0 s | 0 条（第 1 页无商品） |
| run1（有头） | 14:54:15 | 14:54:38 | 23.28 s | 第 1 页 23 条；点 Load more 后页面变为空，循环退出 |
| run2（有头） | 15:00:20 | 15:00:45 | 25.04 s | 23 条；诊断：`/en/shop/page/2?attribute_value=729-2672`，body=`Your request was blocked.` |
| run3（有头，15 分钟循环第 2 轮） | 15:15:20 | 15:15:43 | 22.97 s | 23 条；同样在第 2 页被拦 |

- 翻页逻辑本身是工作的（"Load more" 在桌面宽度下是指向 `/en/shop/page/2` 的普通链接，EasySpider 成功点击并导航），但第 2 页被 Cloudflare 403；按要求未绕过。
- 字段：23 条 × 9 字段 100% 非空，和 Crawl4AI/对照逐条一致。
- 判定：**失败（不能全量）**。原因：Cloudflare 在第 1 次请求之后识别出 Selenium/chromedriver 会话并 403（`Your request was blocked.`）。官方的绕过手段（stealth.min.js、Windows 专用的 Cloudflare/undetected 模式）按要求未启用；Cloudflare 模式在 Linux 上本身也不支持（源码：`Cloudflare Mode only support on Windows x64 platform`）。

## 4. 与普通浏览器视图的对比

- 对照：原生 Playwright + 系统 Chrome 有头（`baseline_full.py`，BeautifulSoup 独立解析）→ `output/baseline_playwright_products.json`。第 1 页 23 条；第 2–5 页 403。
- 第 1 页：对照 23 条 vs Crawl4AI 第 1 页 23 条 vs EasySpider 23 条 → product_id 及顺序、名称、售价、原价、URL **零差异**。
- 总量：站点自己显示 `Showing 23 Of 119 Results`、`data-total-count="119"`、`data-page-count="5"`；Crawl4AI 去重后 119 = 23 + 24×4，无重复、无遗漏。第 1 页只有 23 张卡片是站点自身行为（`data-products-count="23"`），三种方式一致。
- 无法完成"人工真实浏览器"对第 2–5 页的逐条比对：box 上的 Browser-use 浏览器会话启动失败（`daemon default didn't come up`），原生 Playwright 第 2–5 页被 403。第 2–5 页只能用"站点声明的总数/页数"来核对 Crawl4AI。

## 5. 15 分钟重复采集可行性

- 循环脚本：`monitor_15min.sh`（每轮：Crawl4AI → 入库/差异 → 20 s → EasySpider → 入库/差异 → 睡到 900 s）。实际执行了 2 轮：15:00（run2）、15:15（run3），间隔 900 s；加上 14:47 的 run1，Crawl4AI 共 3 次全量。
- 每轮总耗时约 70 s（两工具串行），Crawl4AI 单次约 24 s ≈ 15 分钟预算的 2.7%。每轮只有 5 个页面请求（≈ 每小时 20 个），页间间隔约 4.5 s。
- 3 次 Crawl4AI 运行 15 个页面请求全部 200，没有 429/Retry-After，也没有从 200 变成 403，**未见限速迹象**。
- cron 写法（示例，未安装）：`*/15 * * * * cd /workspace/crawl-test && . venv/bin/activate && DISPLAY=:17 python crawl4ai_run/run_crawl4ai.py headful run_$(date +\%H\%M) && python3 diff_sqlite.py ingest output/runs/crawl4ai_run_$(date +\%H\%M).json && python3 diff_sqlite.py diff --tool crawl4ai`
- 合规提示：原生 Playwright 和不带 stealth 的 Selenium 都在第 2 页被拦，说明站方**有意**拦截自动化浏览器；Crawl4AI 能通过，最可能的原因是它默认带 `--disable-blink-features=AutomationControlled`（隐藏 navigator.webdriver）。这是 Crawl4AI 的默认行为，本测试没有额外开启 stealth，但长期高频监控前建议联系站方取得许可/白名单或询问是否有商品 feed（站点有 Google Merchant feed 的迹象），以免被视为规避防护。

## 6. SQLite 快照与差异

- 脚本：`diff_sqlite.py`（表 `runs` / `snapshots(run_id, product_id 主键)` / `changes`）。命令：`ingest <json>`、`diff --tool X [--a --b]`、`runs`。检测：新增、消失、售价/原价变化、名称/URL 变化，并写入 `changes` 表。
- 真实数据（`output/snapshots.db`）：
  - crawl4ai run1→run2、run2→run3、run1→run3：新增 0，消失 0，价格变化 0，其他 0。
  - easyspider run1→run2、run2→run3：全 0（各 23 条）。
- **逻辑测试（不是真实数据）**：`diff_logic_test.py` 把数据库复制为 `output/snapshots_LOGICTEST_copy.db`，在副本最新的 crawl4ai 快照里删除 16270、把 31304 的售价减 10、插入一个假商品 `LOGICTEST-999999`；diff 正确报出 new=1（LOGICTEST-999999）、disappeared=1（16270）、price_changes=1（31304 Rho LT Bottom Black 69.97→59.97）。

## 7. 推荐的 15 分钟监控方案

1. 工具：Crawl4AI（有头系统 Chrome + Xvfb，JsonCssExtractionStrategy，按 `data-product-template-id` 作主键），串行、页间 ≥1.5 s，`check_robots_txt=True`。不推荐 EasySpider（在该站第 2 页被拦；Linux 上没有可用的无界面任务设计；不注入 stealth 就无法工作，而注入 stealth 违反本次规则）。
2. 翻页：读取 `tp-product-pager` 的 `data-page-count/data-total-count`，访问 `/en/shop/page/N`（robots 允许），不要用 `?page=`（robots 禁止）。
3. 完整性检查：每次比较抓到的数量和 `data-total-count`；不一致或任一页非 200 → 本次标为失败，**不**用它做"消失"判断，避免误报下架。
4. 403 处理：任何 403/429 → 停止本轮、记录 cf-ray/状态码、指数退避，不绕过。
5. 存储/告警：SQLite 快照 + `diff_sqlite.py`，基于 `changes` 表做告警（新品/下架/价格变化）。
6. 先取得站方同意（见第 5 节）。

## 8. 文件清单

- 报告：`/workspace/crawl-test/REPORT.md`
- 输出：`output/crawl4ai_products.{json,csv}`（119 条，最新 run3）、`output/easyspider_products.{json,csv}`（23 条，最新 run3）、`output/runs/*_run{1,2,3}.{json,csv}`、`output/runs/crawl4ai_probe_headless.*`、`output/baseline_playwright_products.json`、`output/snapshots.db`、`output/snapshots_LOGICTEST_copy.db`（逻辑测试副本）
- 脚本：`crawl4ai_run/run_crawl4ai.py`、`easyspider_run/build_task.py`、`easyspider_run/run_easyspider.py`、`baseline_playwright.py`、`baseline_full.py`、`probe_page2.py`、`probe_page2b.py`、`diff_sqlite.py`、`diff_logic_test.py`、`monitor_15min.sh`
- EasySpider：任务 `easyspider/EasySpider_Linux_x64/tasks/1001.json`，执行实例 `execution_instances/1001.json`（及服务 API 生成的 `1002.json`），原生输出 `easyspider/EasySpider_Linux_x64_nostealth/Data/Task_1001/run*.csv|.log`
- 日志：`logs/`（crawl4ai_run*.log、easyspider_*.log、diff_*.json、monitor_15min.log、curl_evidence.txt、easyspider_gui_*.log）、`robots.txt`
