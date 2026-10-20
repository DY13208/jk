# Bark / ntfy 手机推送

两种通道都不需要每天给机器人发消息，配置一次即可长期推送。密钥只放在部署目录 `.env`，站点里只保存环境变量名。

## Bark（iPhone）
1. App Store 安装 **Bark**，打开后首页的示例地址形如 `https://api.day.app/XXXXXXXX/...`，其中 `XXXXXXXX` 就是设备 Key。
2. 在 `D:\jk\.env` 写入 `BARK_KEY=XXXXXXXX`（自建 bark-server 时另写 `BARK_SERVER=https://你的域名`；纯 http 自建需再加 `BARK_ALLOW_HTTP=1`）。
3. `docker compose up -d` 让容器读取新变量。
4. 后台“通知设置” → 站点“配置通知” → 通知方式选 **Bark（iPhone）**，变量名填 `BARK_KEY`，勾选“启用消息推送”，保存。
5. 在通知设置列表点 **发送测试推送**，手机应收到“【测试】商品监控推送”。降价推送使用 timeSensitive（时效性）级别。

## ntfy（Android / iPhone）
1. 安装 **ntfy** App（Google Play / F-Droid / App Store）。
2. 生成随机长主题，例如 PowerShell：`-join ((48..57)+(97..122) | Get-Random -Count 24 | % {[char]$_})`。**任何知道主题名的人都能收到/发送该主题的消息，切勿使用简单名字。**
3. App 中“订阅主题”，服务器默认 `ntfy.sh`，填入该主题。
4. `.env` 写入 `NTFY_TOPIC=刚才的主题`；自建或受保护服务器再写 `NTFY_SERVER=https://...`、`NTFY_TOKEN=tk_...`。
5. `docker compose up -d`，后台站点通知方式选 **ntfy**，变量名填 `NTFY_TOPIC`，启用并保存，点 **发送测试推送**。

## 行为
- 同一站点一次采集的多个变化合并为一条推送（最多列 8 行，其余显示“另有 N 项”）；单个商品推送可点击直达商品页。
- 每个通道两次推送至少间隔 5 秒；HTTP 429/5xx/连接失败自动退避重试，授权错误转为“待处理”，结果记录在“通知投递队列”。
- 只允许 HTTPS，自建 HTTP 服务需显式设置 `*_ALLOW_HTTP=1`。
