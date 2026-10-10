# 邮件推送测试报告

测试日期：2026-10-10（Asia/Shanghai）。

## 结果

```powershell
docker compose run --rm -e RUN_HEADED_TEST=1 monitor python -m pytest tests -q
```

结果：**82 passed, 1 skipped, 1 warning in 52.40s**。跳过的是旧 changedetection 集成测试；警告为 Starlette/AnyIO 弃用提示。

新增邮件测试 17 项，覆盖：

- 首次基线无通知、商品邮件中文内容、事件去重及多收件人。
- SSL / STARTTLS、证书验证、认证调用和明确收件人信封。
- 认证失败、暂时拒绝、永久拒绝、超时结果不确定。
- 部分收件人接受时不自动重发；发送成功后 QUIT 失败不重发。
- SMTP 配置缺失/通知关闭时不发送，非法地址及换行注入校验。
- v1 数据库增量迁移、迁移前备份、历史商品和原通知设置保留。
- 管理 API 保存邮件设置、密码不返回。

浏览器验证：通知方式选项可切换到“邮件（SMTP）”，收件邮箱字段显示，企业微信字段隐藏；未保存测试配置。截图见 `email-settings.jpg`。

本次仅使用 SMTP 模拟对象测试，没有连接真实邮箱服务器、没有发送真实邮件。因此真实邮箱认证、额度、垃圾邮件过滤及实际收件尚未验证。
