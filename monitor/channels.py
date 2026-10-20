"""One place that knows every notification channel: what it needs, whether it is configured, and how to test it.

Secrets live only in deployment environment variables (.env) or the existing encrypted Gmail file; this module
reports *presence* and variable names only, never values."""
import os
import re

from . import push, pushplus
from .email_delivery import configuration as email_configuration
from .notifications import webhook

ENV_NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,79}$')
APPS = {
    'bark': [['App Store（iPhone）', 'https://apps.apple.com/app/bark-customed-notifications/id1403753865']],
    'ntfy': [['App Store（iPhone）', 'https://apps.apple.com/app/ntfy/id1625396347'],
             ['Google Play', 'https://play.google.com/store/apps/details?id=io.heckel.ntfy'],
             ['F-Droid', 'https://f-droid.org/packages/io.heckel.ntfy/']],
    'pushplus': [['pushplus 官网（微信扫码）', 'https://www.pushplus.plus/']],
    'agentmail': [['agently-cli（npm）', 'https://www.npmjs.com/package/@tencent-qqmail/agently-cli']],
}
# (name, required, description). Defaults mirror compose.yaml; site-specific names are added at runtime.
CHANNELS = [
    {'id': 'email', 'name': '邮件（SMTP / Gmail）', 'platform': '任意邮箱', 'doc': 'docs/EMAIL.md', 'env': [
        ('SMTP_HOST', True, 'SMTP 服务器'), ('SMTP_FROM', True, '发件邮箱'), ('SMTP_PORT', False, '端口（默认 465）'),
        ('SMTP_SECURITY', False, 'ssl 或 starttls'), ('SMTP_USERNAME', False, '登录名'), ('SMTP_PASSWORD', False, '密码 / 授权码')],
     'note': '也可在“Gmail 发件箱”配置 Gmail 应用专用密码（加密保存在本机 data/，优先于 SMTP）。收件邮箱在网站设置中填写。'},
    {'id': 'bark', 'name': 'Bark', 'platform': 'iPhone', 'doc': 'docs/PUSH.md', 'env': [
        ('BARK_KEY', True, 'App 中的设备 Key'), ('BARK_SERVER', False, '自建服务器（默认 https://api.day.app）'),
        ('BARK_ALLOW_HTTP', False, '自建 HTTP 服务器设为 1')],
     'note': '网站设置中“环境变量名”默认填 BARK_KEY。'},
    {'id': 'ntfy', 'name': 'ntfy', 'platform': 'Android / iPhone', 'doc': 'docs/PUSH.md', 'env': [
        ('NTFY_TOPIC', True, '随机长主题（≥20 位，知道主题的人都能收到）'), ('NTFY_SERVER', False, '自建服务器（默认 https://ntfy.sh）'),
        ('NTFY_TOKEN', False, '受保护主题的访问令牌'), ('NTFY_ALLOW_HTTP', False, '自建 HTTP 服务器设为 1')],
     'note': 'App 中订阅同一主题。网站设置中“环境变量名”默认填 NTFY_TOPIC。'},
    {'id': 'wecom', 'name': '企业微信机器人', 'platform': '企业微信群', 'doc': 'README.md', 'env': [
        ('WECOM_LECOMTE_WEBHOOK', False, '群机器人 Webhook 地址'), ('WECOM_SITE2_WEBHOOK', False, '第二个群机器人 Webhook')],
     'note': '在群聊“添加机器人”获取 Webhook；网站设置中填写对应的变量名。'},
    {'id': 'weixin', 'name': '微信扫码直连（腾讯 ClawBot）', 'platform': '个人微信', 'doc': 'docs/WEIXIN.md', 'env': [],
     'note': '无需环境变量：在下方“微信扫码直连”面板扫码绑定，凭据只保存在独立的 weixin-bridge 容器中。'},
    {'id': 'pushplus', 'name': '个人微信（pushplus）', 'platform': '微信公众号', 'doc': 'docs/PUSHPLUS.md', 'env': [
        ('PUSHPLUS_LECOMTE_TOKEN', False, 'pushplus Token'), ('PUSHPLUS_SITE2_TOKEN', False, '第二个 Token')],
     'note': '微信扫码登录 pushplus 并实名认证后复制 Token；网站设置中填写对应变量名。'},
    {'id': 'agentmail', 'name': 'Agent Mail CLI（QQ 邮箱 · 本机）', 'platform': '任意邮箱', 'doc': 'docs/AGENTMAIL.md', 'env': [],
     'note': '无需环境变量：发件账号是本机 agently-cli 授权的邮箱，需本机工作进程运行（见下方 Agent Mail 面板）。'},
]
NAMES = {c['id']: c['name'] for c in CHANNELS}
DEFAULT_ENV = {'bark': 'BARK_KEY', 'ntfy': 'NTFY_TOPIC'}
# Server/options variables, not a destination: never selectable as the "key" variable of a site or test.
OPTIONS = {'BARK_SERVER', 'BARK_ALLOW_HTTP', 'NTFY_SERVER', 'NTFY_TOKEN', 'NTFY_ALLOW_HTTP'}


def _set(name): return bool(ENV_NAME.match(name or '') and os.getenv(name, '').strip())


def site_issue(db, site, weixin=None, agent=None):
    """Why the site's chosen channel cannot deliver right now (None = configured). Never includes secret values.
    weixin/agent are status dicts; when omitted those runtime checks are skipped."""
    channel = site.get('notification_channel') or 'wecom'
    try:
        if channel == 'email': email_configuration(site, db)
        elif channel in push.CHANNELS: push.config(site)
        elif channel == 'pushplus': pushplus.token(site)
        elif channel == 'weixin':
            if weixin is not None and not weixin.get('ready'): return '微信扫码直连未就绪（请在通知设置扫码并主动发一句话）'
        elif channel == 'agentmail':
            from .email_delivery import recipients
            recipients(site.get('email_to') or '')
            if agent is not None and not agent.get('ready'): return 'Agent Mail 本机工作进程未运行或未授权'
        else: webhook(site)
    except ValueError as exc:
        return str(exc)
    return None


def env_names(db, channel, destinations=True):
    """Variable names for a channel: its documented ones plus those referenced by sites using it.
    destinations=True drops server/option variables (the names a site or a test may point at)."""
    spec = next(c for c in CHANNELS if c['id'] == channel)
    names = [e[0] for e in spec['env'] if not (destinations and e[0] in OPTIONS)]
    for site in db.sites():
        if site.get('notification_channel') == channel and ENV_NAME.match(site.get('webhook_env') or '') and site['webhook_env'] not in names:
            names.append(site['webhook_env'])
    return names


def overview(db, weixin=None, agent=None):
    sites = db.sites()
    gmail = False
    try:
        from .gmail import load
        gmail = bool(load(db))
    except Exception: pass
    result = []
    for spec in CHANNELS:
        cid = spec['id']
        known = {e[0]: e for e in spec['env']}
        env = [{'name': n, 'required': known.get(n, (n, True, ''))[1], 'description': known.get(n, (n, True, '网站设置中引用的变量'))[2],
                'set': _set(n)} for n in env_names(db, cid, destinations=False)]
        targets = [e for e in env if e['name'] not in OPTIONS]
        if cid == 'email': configured = gmail or all(e['set'] for e in env if e['required'])
        elif cid in ('bark', 'ntfy', 'wecom', 'pushplus'): configured = any(e['set'] for e in targets)
        elif cid == 'weixin': configured = bool((weixin or {}).get('ready'))
        else: configured = bool((agent or {}).get('ready'))
        users = [{'id': s['id'], 'name': s['name'], 'notify_enabled': s['notify_enabled'],
                  'issue': site_issue(db, s, weixin, agent) if s['notify_enabled'] else None}
                 for s in sites if (s.get('notification_channel') or 'wecom') == cid]
        result.append({**{k: spec[k] for k in ('id', 'name', 'platform', 'doc', 'note')}, 'env': env, 'configured': configured,
                       'gmail': gmail if cid == 'email' else None, 'apps': APPS.get(cid, []), 'sites': users,
                       'test': {'needs_to': cid in ('email', 'agentmail'), 'env_choices': [e['name'] for e in targets if e['set']]
                                if cid in ('bark', 'ntfy', 'wecom', 'pushplus') else []}})
    return result
