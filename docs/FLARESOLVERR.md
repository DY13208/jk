# FlareSolverr 采集模式（Cloudflare 站点）

`niponino_arcteryx` 被 Cloudflare 保护：普通 HTTP / 自动化 Chrome 得到 403 或 “Just a moment…” 验证页，
日常 Chrome 扩展也会偶尔遇到人机验证。自 2026-10-20 起该站点使用 `execution_mode: "flaresolverr"`。

## 组成

- `compose.yaml` 中的 `flaresolverr` 服务（`ghcr.io/flaresolverr/flaresolverr:latest`，`restart: unless-stopped`）。
  **不发布端口**，只在 compose 内部网络可达；monitor 通过环境变量 `FLARESOLVERR_URL`
  （默认 `http://flaresolverr:8191/v1`）访问。
- `adapters/__init__.py`：`flaresolverr_fetch` / `collect_flaresolverr`。它复用 `collect_css_http` 的全部逻辑
  （站点 `schema`、`next_page_link` 分页、`clean()` 商品 ID/价格规则、重复 ID 与空页检查），
  只把“取页面”换成 FlareSolverr：
  - 每次运行 `sessions.create` 一个会话，所有分页复用，`finally` 中 `sessions.destroy`；
  - 每页 `request.get`，`maxTimeout` 120000 ms，失败/仍是验证页时重试 2 次（退避 15 s、45 s）；
  - 页与页之间按站点 `page_delay` 等待；调度间隔由站点 `interval_minutes` 决定（niponino 为 240 分钟）。
- 站点仍是 `css_catalog` 适配器，`execution_mode` 不属于基线字段，因此切换模式不会新建基线。

## 失败语义（不误报）

- 任何一页仍是 Cloudflare 验证页、FlareSolverr 超时/不可用、某页没有商品 → 整次采集失败，**不提交快照**，
  因而不会产生 “下架” 事件；站点**不会被暂停**，下一个间隔自动重试。
- 站点真实返回 HTTP 403/429（无验证页）时沿用原有暂停逻辑。
- FlareSolverr 模式的单次运行超时为 1500 秒（其他模式 300 秒）。

## 可选配置（adapter_config）

| 键 | 默认 | 说明 |
|---|---|---|
| `flaresolverr_timeout` | 120000 | 每页 maxTimeout（毫秒） |
| `flaresolverr_backoff` | `[15, 45]` | 重试前等待秒数（最多 2 次） |
| `flaresolverr_url` | 环境变量 | 覆盖 `FLARESOLVERR_URL` |

## 资源

FlareSolverr 内含无头 Chrome；空闲约 100–200 MB，解题期间约 300–500 MB 内存。
