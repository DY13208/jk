# 浏览器会话兼容模式

日期：2026-10-15。

复用 Crawl4AI 0.9.4 的 `BrowserConfig.storage_state` 和 Playwright 的 `storage_state()`，按目录 URL 隔离保存 Cookie 和 localStorage。不是自动破解访问控制或验证码的功能，不保证 Cloudflare 放行。

网站设置中的“适配器配置”可加入：

```json
{"persistent_session": true, "page_delay": 10}
```

其他已有适配器参数应保留。此类运行参数允许在有历史的网站上修改，仍禁止更换提取 schema 或目录后复用原基线。

默认关闭。会话文件存放在数据库同级 `browser-sessions` 目录，使用 URL 哈希作为文件名，不能通过网站配置指定本地任意文件。写入采用临时文件和原子替换，只有全部分页与总数一致后保存；失败不覆盖原会话。会话可能包含登录信息，不应上传 GitHub或共享。当前 Docker 的 data 挂载保留它，源码打包器不包含它。

## 实测结果

使用本项目 Docker 镜像、原生 Chrome、会话保存选项和 10 秒分页间隔测试 Lecomte：第一页成功，第 2 页 `/en/shop/page/2?attribute_value=729-2672` 仍收到 HTTP 403（响应提示 anti-bot protection）。

因此，本功能没有解决该网站当前的完整采集限制。没有重新启用网站，没有提交第一页作为快照，没有发送消息。未安装代理、验证码服务或指纹伪装模块。

官方参考：https://docs.crawl4ai.com/api/parameters/ （storage_state / BrowserConfig）。
