"""Bark (iPhone) and ntfy (Android/iPhone) push channels.

Secrets (Bark device key, ntfy topic/token) live only in deployment environment variables; the site row stores
just the variable name (webhook_env), like pushplus. Several pending events of one site are batched into one push.
"""
import json
import os
import re
from urllib.parse import urlsplit, quote

from .mail_templates import LABELS, message

CHANNELS = ('bark', 'ntfy')
GROUP = '商品监控'
MAX_LINES = 8
TAGS = {'NEW': 'new', 'REMOVED': 'x', 'RETURNED': 'recycle', 'PRICE_DROP': 'chart_with_downwards_trend',
        'PRICE_RISE': 'chart_with_upwards_trend', 'TEST': 'white_check_mark'}


def _server(name, default, allow_http_env):
    url = (os.getenv(name) or default).strip().rstrip('/')
    p = urlsplit(url)
    allowed = ('https',) + (('http',) if os.getenv(allow_http_env, '').strip() in ('1', 'true', 'yes') else ())
    if p.scheme not in allowed or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError(f'{name} 必须是 HTTPS 地址（自建 HTTP 服务需设置 {allow_http_env}=1）')
    return url


def _secret(site, what, pattern):
    name = site.get('webhook_env') or ''
    value = os.getenv(name, '').strip() if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', name) else ''
    if not value or not re.fullmatch(pattern, value):
        raise ValueError(f'请在部署环境变量中设置{what}，并在站点中填写该变量名')
    return value


def config(site):
    """Validate and return (url, headers, extra) for the site's push channel. Never returns secrets in errors."""
    if site.get('notification_channel') == 'bark':
        key = _secret(site, ' Bark 设备 Key', r'[A-Za-z0-9_-]{8,128}')
        return _server('BARK_SERVER', 'https://api.day.app', 'BARK_ALLOW_HTTP') + '/push', None, {'device_key': key}
    if site.get('notification_channel') == 'ntfy':
        topic = _secret(site, ' ntfy 主题（topic）', r'[A-Za-z0-9_-]{1,64}')
        token = os.getenv(site.get('ntfy_token_env') or 'NTFY_TOKEN', '').strip()
        if token and (len(token) > 512 or any(c.isspace() for c in token)): raise ValueError('NTFY_TOKEN 格式无效')
        return (_server('NTFY_SERVER', 'https://ntfy.sh', 'NTFY_ALLOW_HTTP'),
                {'Authorization': f'Bearer {token}'} if token else None, {'topic': topic})
    raise ValueError('不是 Bark/ntfy 通道')


def _line(site, event):
    if event['kind'] not in LABELS: return message(site, event).splitlines()[0]
    p = event['payload']['product']
    price = f"{p.get('sale_price')} {p.get('currency') or ''}".strip()
    old = event['payload'].get('old_price')
    return f"{LABELS[event['kind']]}：{p['name']} · {price}" + (f"（原 {old}）" if old is not None else '')


def _http(u):
    u = str(u or '').strip()
    p = urlsplit(u)
    return u if p.scheme in ('http', 'https') and p.hostname else ''


def tap_url(site, events):
    """URL opened when the push is tapped: the product page when exactly one product is involved, else the site URL."""
    urls = {_http(((e.get('payload') or {}).get('product') or {}).get('url')) for e in events if e.get('kind') in LABELS}
    urls.discard('')
    return urls.pop() if len(urls) == 1 else _http(site.get('url'))


def build(site, events):
    """Return a provider-neutral notification dict for one or more events of a site."""
    kinds = [e['kind'] for e in events]
    if len(events) == 1:
        title = (message(site, events[0]).splitlines()[0])[:120]
        body = '\n'.join(message(site, events[0]).splitlines()[1:])
    else:
        title = f"{site['name']}：{len(events)} 项商品变化"[:120]
        lines = [_line(site, e) for e in events[:MAX_LINES]]
        if len(events) > MAX_LINES: lines.append(f'…另有 {len(events) - MAX_LINES} 项，请在后台查看')
        body = '\n'.join(lines)
    url = tap_url(site, events)
    image = ''
    if len(events) == 1 and events[0]['kind'] in LABELS:
        i = str(events[0]['payload']['product'].get('image') or '')
        image = i if urlsplit(i).scheme == 'https' else ''
    urgent = 'PRICE_DROP' in kinds
    return {'title': title, 'body': body[:3500], 'url': url, 'image': image, 'urgent': urgent,
            'tags': sorted({TAGS.get(k, 'bell') for k in kinds})[:5]}


def payload(channel, note, extra, site_name=''):
    if channel == 'bark':
        data = {'device_key': extra['device_key'], 'title': note['title'], 'body': note['body'], 'group': (site_name or GROUP)[:64],
                'level': 'timeSensitive' if note['urgent'] else 'active'}
        if note['url']: data['url'] = note['url']
        if note['image']: data['icon'] = note['image']
        return data
    data = {'topic': extra['topic'], 'title': note['title'], 'message': note['body'], 'tags': note['tags'],
            'priority': 4 if note['urgent'] else 3}
    if note['url']: data['click'] = note['url']
    return data


def outcome(channel, response):
    """Map an HTTP response to (status, reason); provider text is never stored (it may echo secrets)."""
    name = 'Bark' if channel == 'bark' else 'ntfy'
    code = response.status_code
    if code == 429 or code >= 500: return 'retry', f'{name} HTTP {code}，稍后重试'
    if code in (401, 403): return 'held', f'{name} 拒绝授权（HTTP {code}），请核对 Key/Token'
    if code != 200: return 'held', f'{name} 拒绝请求（HTTP {code}），请核对配置'
    try: body = response.json()
    except ValueError: return 'uncertain', f'{name} 返回内容无法确认，请在手机上核对'
    if channel == 'bark' and body.get('code') != 200: return 'held', 'Bark 拒绝推送，请核对设备 Key'
    if channel == 'ntfy' and not body.get('id'): return 'uncertain', 'ntfy 返回内容无法确认，请在手机上核对'
    return 'sent', f'{name} 服务器已接收，送达请在手机上核对'


def encode(payload, headers=None):
    """Serialize a JSON payload as explicit UTF-8 bytes with a charset header (Chinese titles/bodies stay intact)."""
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return body, {'Content-Type': 'application/json; charset=utf-8', **(headers or {})}
