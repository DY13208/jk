# 个人微信通知（pushplus）

## 配置

1. 在 https://www.pushplus.plus/ 用微信扫码登录，关注其公众号，按服务要求完成实名，获取 Token。不要将 Token 发到聊天、提交到 Git 或放在 URL 中。
2. 在部署目录的 `.env` 文件新增（值替换为自己的 Token）：

```dotenv
PUSHPLUS_LECOMTE_TOKEN=填写你的Token
PUSHPLUS_SITE2_TOKEN=
```

3. 执行 `docker compose up -d --build monitor`。后台进入“通知设置 → 设置通知”，选择“个人微信（pushplus）”，环境变量名填 `PUSHPLUS_LECOMTE_TOKEN`，选择商品事件及推送开关后保存。第二个网站可以填 `PUSHPLUS_SITE2_TOKEN`，也可共享第一个变量。
4. 环境状态“已就绪”仅验证本地 Token 配置，不访问推送接口、不证明授权有效或微信已送达。本轮不发送测试消息，不自动更改已有网站渠道或启用状态。

## 行为与限制

复用 SQLite 事件队列、首次基线静默和事件唯一性。NEW、REMOVED、RETURNED、PRICE_DROP、PRICE_RISE 按网站规则推送；403/429 等采集异常只在后台记录，不推送。

当前每个商品事件一条，未实现同轮合并。全局至少 15 秒间隔，重启后仍根据队列记录限速；队列可能延迟发送。免费额度及实名要求以 https://www.pushplus.plus/doc/guide/use.html 为准。

固定使用 HTTPS POST https://www.pushplus.plus/send，不携带 Cookie、不跟随重定向。仅向 Token 所属微信推送，不设置好友或群组。名称、价格、原价、折扣和商品链接复用已有纯文本模板。

HTTP 200 且业务 code=200 仅表示 pushplus 接受请求，队列标记“已发送”表示提交成功，不代表微信送达。请在 pushplus 消息记录核对最终状态。明确拒绝暂停该条，HTTP 429 或连接建立失败退避重试；超时、异常响应等结果不确定时不自动重发，核对后人工重试，避免重复通知。服务返回文本和 Token 不写执行日志。

配置 Token 前不能做真实收件验收。自动化使用模拟 HTTP 响应，不触达微信。
