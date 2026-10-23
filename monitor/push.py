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
# ntfy emoji shortcodes (shown as an emoji before the title); Bark ignores tags.
TAGS = {'NEW': 'new', 'REMOVED': 'x', 'RETURNED': 'recycle', 'PRICE_DROP': 'chart_with_downwards_trend',
        'PRICE_RISE': 'chart_with_upwards_trend', 'TEST': 'white_check_mark', 'ACCESS_DENIED': 'warning'}


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


TITLE_MAX = 28
KIND_SHORT = {'NEW': '上新', 'REMOVED': '下架', 'RETURNED': '补货', 'PRICE_DROP': '降价', 'PRICE_RISE': '涨价'}
# Brand prefixes that are redundant on a single-brand shop feed (case-insensitive, any apostrophe style).
BRANDS = (r"arc['\u2019\u2018`]?\s?teryx", '始祖鸟')
_BRAND_RE = re.compile(r'^\s*(?:' + '|'.join(BRANDS) + r')(?:\s*[-–—:·|/]\s*|\s+|(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff]))', re.I)


def clamp(text, limit=TITLE_MAX):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip(' ·-') + '…'


def short_name(name):
    """Product name without a leading redundant brand; falls back to the full name if nothing would remain."""
    full = ' '.join(str(name or '').split())
    short = _BRAND_RE.sub('', full, count=1).strip()
    return short or full


_SITE_BRAND_RE = re.compile(r'\s*[-–—:·|/]\s*(?:' + '|'.join(BRANDS) + r')\s*$', re.I)


def short_site(name):
    """Site name for titles: 'Bergzeit FR - Arc'teryx' -> 'Bergzeit FR' (the brand is implied by the feed)."""
    full = ' '.join(str(name or '').split())
    return _SITE_BRAND_RE.sub('', full).strip() or full


def _price(p, value=None):
    from .mail_templates import _money
    v = p.get('sale_price') if value is None else value
    return _money(v, p.get('currency')) if v not in (None, '') else ''


def _key(event):
    """Key number for the title: percentage change for price events."""
    from .mail_templates import _pct
    p = event['payload']['product']
    old = event['payload'].get('old_price')
    if event['kind'] in ('PRICE_DROP', 'PRICE_RISE') and old is not None:
        pct = _pct(old, p.get('sale_price'))
        return pct.replace('.0%', '%') if pct else ''
    return ''


def _expand(events):
    out = []
    for e in events:
        if e.get('kind') == 'DIGEST': out.extend(_expand((e.get('payload') or {}).get('events') or []))
        else: out.append(e)
    return out


def _title(event):
    kind = KIND_SHORT[event['kind']]
    key = _key(event)
    head = f'{kind} {key}' if key else kind
    return clamp(f"{head} · {short_name(event['payload']['product'].get('name'))}")


def _line(site, event):
    if event['kind'] not in LABELS: return message(site, event).splitlines()[0]
    p = event['payload']['product']
    old = event['payload'].get('old_price')
    key = _key(event)
    price = (f"{_price(p, old)} → {_price(p)}" if old is not None else _price(p))
    head = KIND_SHORT[event['kind']] + (f' {key}' if key else '')
    return f"{head} · {clamp(short_name(p.get('name')), 40)}" + (f' · {price}' if price else '')


def _detail(site, event):
    p = event['payload']['product']
    old = event['payload'].get('old_price')
    if old is not None: price = f"{_price(p, old)} → {_price(p)}"
    else:
        price = _price(p)
        orig = p.get('original_price')
        if price and orig not in (None, '') and str(orig) != str(p.get('sale_price')): price += f"（原价 {_price(p, orig)}）"
    full = ' '.join(str(p.get('name') or '').split())
    lines = [price] if price else []
    lines.append(site.get('name') or '')
    if '…' in _title(event) or short_name(full) != full: lines.append(full)
    return '\n'.join(x for x in lines if x)


def _http(u):
    u = str(u or '').strip()
    p = urlsplit(u)
    return u if p.scheme in ('http', 'https') and p.hostname else ''


def tap_url(site, events):
    """URL opened when the push is tapped: the product page when exactly one product is involved, else the site URL."""
    urls = {_http(((e.get('payload') or {}).get('product') or {}).get('url')) for e in _expand(events) if e.get('kind') in LABELS}
    urls.discard('')
    return urls.pop() if len(urls) == 1 else _http(site.get('url'))


def build(site, events):
    """Return a provider-neutral notification dict. Title is phone-sized: change type, key number, short product name."""
    flat = _expand(events)
    product = [e for e in flat if e.get('kind') in LABELS]
    kinds = [e['kind'] for e in flat]
    if len(flat) == 1 and product:
        title, body = _title(flat[0]), _detail(site, flat[0])
    elif len(flat) == 1:
        lines = message(site, flat[0]).splitlines()
        title, body = clamp(lines[0]), '\n'.join(lines[1:])
    else:
        title = clamp(f"{len(flat)} 个变化 · {short_site(site.get('name'))}")
        lines = [_line(site, e) for e in flat[:MAX_LINES]]
        if len(flat) > MAX_LINES: lines.append(f'…另有 {len(flat) - MAX_LINES} 项，请在后台查看')
        body = '\n'.join(lines)
    url = tap_url(site, events)
    image = ''
    if len(flat) == 1 and product:
        i = str(flat[0]['payload']['product'].get('image') or '')
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
