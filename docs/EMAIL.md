# 邮件推送使用说明

系统复用 Python 标准库 `smtplib` / `email`，通过现有通知队列发送邮件；无需增加邮件服务器或付费推送平台。邮箱服务商的 SMTP 权限、额度和收费以其规定为准。

## 配置发件账号

在 `D:\jk\.env` 中填写以下变量（不要提交该文件）：

```dotenv
SMTP_HOST=smtp.your-provider.example
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_USERNAME=sender@your-provider.example
SMTP_PASSWORD=邮箱服务商的SMTP授权码
SMTP_FROM=sender@your-provider.example
```

使用 STARTTLS 的邮箱：设置 `SMTP_SECURITY=starttls`，并按服务商要求设置端口（通常为 587）。仅支持加密连接，验证服务器证书；不降级为明文。用户名和密码可同时留空，用于支持匿名连接的 TLS 邮件中继。变量为所有网站共用的发件账号；各网站分别设置收件人。

重建/更新服务：

```powershell
cd D:\jk
docker compose up -d --build monitor
```

## 后台操作

打开 http://127.0.0.1:8080/#notifications，点击网站的“设置通知”，选择“邮件（SMTP）”，填写收件邮箱。多个地址用逗号分隔（最多 20 个），选择事件规则，最后勾选“启用消息推送”并保存。采集启用开关与推送开关独立。

邮件包含事件类型、网站名称、商品名称、售价、原价、折扣、购买链接和事件编号。首次完整采集仍只建立基线；仅以后符合规则的变化进入通知队列。关闭时产生的事件不会自动补发。

## 失败处理与去重

- 复用事件唯一键、数据库队列和防重入锁，同一个事件不会重复入队。
- SMTP 接受后记录“已提交”，仅说明服务器接受，不保证已进入收件箱（也可能被退信或归入垃圾邮件）。
- 临时拒绝使用原有指数退避，最多 5 次；配置错误/永久拒绝暂停投递。
- 超时或断线无法确定是否已接受邮件，标记“需核对”，不自动重发。
- 部分收件人被拒绝时，其他人可能已收到；整封邮件不自动重发。
- 人工重试前核对邮箱。稳定 Message-ID 便于查找，但 SMTP 不提供严格的恰好一次保证。

密码/授权码不会保存到 SQLite，也不会返回管理 API 或出现在发送异常日志中。测试仅使用 SMTP 模拟对象，没有向真实邮箱发送消息。

参考：[Python smtplib 官方文档](https://docs.python.org/3.12/library/smtplib.html)。
