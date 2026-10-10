# 管理后台界面更新

日期：2026-10-10。

## 成熟组件复用

- Tabler Core 1.6.1（基于 Bootstrap，MIT）：使用 card、card-header、table/table-vcenter、btn/btn-primary、form-control、form-select、form-check-input。
- Tabler Icons 3.49.0（MIT）：只打包导航和操作需要的 SVG 图标。
- 页面保留现有路由、管理 API、原生 dialog 和业务事件绑定；不引入第二套前端框架。

组件来自官方 npm 包，CSS 和图标本地部署，不使用 CDN，不加载外部字体、脚本或样式。完整许可证在 `admin/vendor/tabler/`；UI 自定义层在 `admin/style.css`。

官方文档：https://docs.tabler.io/ui/getting-started/installation

## 界面变化

- 浅灰工作区、白色侧栏、克制的绿色选中态，统一线性图标。
- 桌面 280px 侧栏，折叠为 80px，手机 72px 导航栏；指标卡随窗口排列。
- 表格内部滚动、统一按钮与表单，适配小屏。
- Agent Mail 配置命令默认折叠，授权、账号、额度、预览和投递状态集中展示。
- 移除主界面“开源组件与致谢”链接，许可证和原有 NOTICE 留在源码分发中。

## 验证

- Docker 全套测试：116 passed, 1 skipped, 1 warning（61.04 秒）。跳过旧 changedetection 集成；警告为 Starlette/AnyIO 弃用提示。
- `node --check` 检查 app.js 和 icons.js 均通过。
- 1440px 桌面：侧栏 280px、导航 44px、4 列指标，无页面横向溢出。
- 折叠侧栏实测 80px。
- 390px 手机：侧栏 72px、指标单列，无页面横向溢出；宽表格在表格容器内滚动。
- 六个路由均可切换，无界面错误；设置表单可打开关闭，测试期间没有保存修改或发送邮件。
- Tabler CSS 实际服务 HTTP 200；CSS 无外部 import 或 HTTP url。

截图：`ui-redesign-desktop.jpg`、`ui-redesign-mobile.jpg`。

## 采集及邮件状态

旧 User-Agent / robots parser 兼容错误已修复，并保留 robots.txt 检查。现有最新执行记录为站点 HTTP 403，监控保持暂停，原有 119 件基线不被覆盖；没有尝试绕过访问控制。Agent Mail CLI 已安装并授权，通知页可查看本机助手状态，账号配置说明见 AGENTMAIL.md。所有邮件模板见后台“全部样式”；没有在此界面验证中发送真实邮件。
