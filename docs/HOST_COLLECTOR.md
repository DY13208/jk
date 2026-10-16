# 本机 Chrome 采集与重新部署

## 运行方式

FastAPI、APScheduler、SQLite、差异识别、通知与管理界面继续运行在 Docker。
新增的 Windows 采集进程通过仅限本机的 HTTP 接口领取任务，使用 Crawl4AI 0.9.4 连接项目独立启动的 Google Chrome，完成后把快照交回后台。

采集进程不打开共享 SQLite，不读取日常 Chrome 的登录资料，不接触邮件或企业微信凭据。独立 Chrome 的资料目录在 `data/chrome-profiles/`，CDP 仅绑定本机随机端口。该目录及采集令牌不会进入 Git 或源码包。

## 安装和启动（PowerShell）

需要 Docker Desktop、Google Chrome、uv。首次安装下载 Python 3.11 和采集依赖：

```powershell
cd D:\jk
uv venv --python 3.11 .crawler-venv
uv pip install --python .crawler-venv/Scripts/python.exe -r requirements-collector.txt
docker compose up -d --build
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Start-Collector.ps1
```

启动脚本注册当前用户登录时运行的计划任务 `ProductMonitor-Collector`，不要求退出日常浏览器。Chrome 是有界面进程，因此无人值守使用需要 Windows 用户保持登录；电脑睡眠/关机时不能采集。

```powershell
# 检查连接
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Start-Collector.ps1 -Status
# 前台诊断（后台已有进程时会因防重入锁退出）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Start-Collector.ps1 -Foreground
# 停止本次运行
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Start-Collector.ps1 -Stop
```

启动脚本写有 UTF-8 BOM，兼容 Windows PowerShell 5.1。

## 后台配置

访问 http://127.0.0.1:8080/ 。总览显示本机采集进程是否已连接。
“网站设置”新增“采集运行位置”和“分页方式”。Lecomte 使用：

```json
{"execution_mode":"host","pagination_mode":"load_more","page_delay":3}
```

其他提取规则及 schema 保持不变。切换运行位置不清空基线。第二个网站可以选择独立的适配器、运行位置、分页方式和间隔。

## 保护机制

- 随机独立令牌认证采集接口；工作进程拒绝远程服务地址、环境代理及 HTTP 重定向。
- 后台原有网站租约防重入，加上工作进程单实例文件锁。
- 每个领取任务有独立随机凭据，过期、重复、凭据不符、网站暂停后的结果会被拒绝。
- 后台重启后取消旧任务；采集超时不会提交结果。
- 快照先在后台做完整性校验，再由现有比对事务写入。任务传输本身不创建快照或事件。
- 网站返回 403/429 仍暂停该网站；失败不会覆盖历史商品或误报下架。

## 本次验收结果（2026-10-15）

- Docker 后台重新构建部署，本机工作进程已连接，登录自动启动任务已注册。
- Windows 原生 Chrome/CDP 对本地自有动态页面完整采集 90 件、3 批，每批 30 件；90 个 ID 唯一，完整性校验通过（独立测试 1 passed，17.31 秒）。
- 后台完整回归测试覆盖 90 件同时移除/新增各 2 件、通知去重、接口认证、任务领取防重入、重复结果拒绝、暂停及过期保护。
- 完整回归：142 passed、1 skipped、1 warning，72.44 秒；最终镜像的接口与加载更多专项检查：13 passed、1 skipped、1 warning。跳过 Windows 原生测试时，该测试已另外在宿主机实际运行通过。
- 真实端到端任务日志 7：后台下发 → Windows Chrome 执行 → 返回第 2 页 HTTP 403 → 后台记录失败并暂停。原 119 件成功快照保留。
- 最新独立诊断中入口页也返回 HTTP 403。这说明本机接入已完成，但 Lecomte 的完整自动采集仍未恢复；内置浏览器一次取得 113 件不能作为本机工作进程采集成功的证据。
- 根据此前“暂时不要推送”的要求，Lecomte 的通知开关已关闭；没有发送真实邮件或企业微信消息。

## 已知未完成条件

Lecomte 当前的真实访问拒绝尚未解决。不能宣称每 15 分钟上新监控已可用，也不能把历史 119 件显示成当前实时数据。收到站方允许的商品接口/固定 IP 放行等可用访问条件后，可继续使用本机或 Docker 适配器，无需重写比对与通知。
