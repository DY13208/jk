# 本轮验收记录（2026-10-16）

- 已部署 monitor 和 weixin-bridge，monitor /health 为 ok。
- 已读取真实本机 /api/weixin/status：running=true、bound=true、ready=true。通知页经浏览器刷新后显示“会话已就绪”。重启部署后绑定仍保留。
- 当前 Niponino 网站已使用 weixin 通知渠道；其他网站渠道保持现有设置。本轮未手动发送测试微信消息，也未宣称真实新通知已送达。
- Docker 相关测试：121 passed、1 skipped、1 warning，26.50 秒。警告为 Starlette 测试依赖弃用提示。
- Node 会话测试通过：绑定本人隔离、会话激活、重复消息不恢复额度、保守次数/期限。
- Node 扩展回归通过：Odoo 与通用采集成功/失败自动关闭、页面脚本不返回时超时、busy 状态独立到期清理、用户标签页保留、关闭失败重试、后台断线清理、中断任务、投递重交。
- 扩展版本 1.2.1，需要用户在 chrome://extensions 手动刷新。本次浏览器安全策略禁止代开该地址，未绕过限制；不能将源码/测试通过表述为已验证用户 Chrome 加载了新版。
- 打包完整性及排除检查通过：包含 weixin-bridge 源码，排除本机令牌、config.local.json、node_modules 和 .bak 文件。

运行文档：WEIXIN.md、CHROME_EXTENSION.md。源码包 dist/product-monitor-source.zip。
