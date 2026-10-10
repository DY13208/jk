"""Shared notification content for every channel (WeCom text, SMTP, Agent Mail); no network calls.

The HTML is email-client safe: table layout, inline styles only, no external CSS/JS/fonts,
600px max width, bgcolor attributes for Outlook, and graceful fallbacks (no image -> initial tile).
"""
import html
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

LABELS = {'NEW':'新品上架','REMOVED':'商品下架','RETURNED':'重新上架','PRICE_DROP':'价格下降','PRICE_RISE':'价格上涨'}
ALERT_LABELS = {'ACCESS_DENIED':'访问拒绝告警','TEST':'测试邮件'}
# Restrained accents: (pill text, pill background)
ACCENTS = {'NEW':('#067647','#ecfdf3'),'REMOVED':('#b42318','#fef3f2'),'RETURNED':('#175cd3','#eff8ff'),
           'PRICE_DROP':('#067647','#ecfdf3'),'PRICE_RISE':('#b54708','#fffaeb'),'ACCESS_DENIED':('#b42318','#fef3f2'),
           'TEST':('#5925dc','#f4f3ff')}
HEADLINES = {'NEW':'上架了一件新商品','REMOVED':'一件商品已下架','RETURNED':'一件商品重新上架',
             'PRICE_DROP':'一件商品降价了','PRICE_RISE':'一件商品涨价了'}
INTRO = {'NEW':'最新一次完整采集中出现了此前从未记录过的商品。','REMOVED':'这件商品已不在最新一次完整采集的目录中。',
         'RETURNED':'这件商品曾经下架，现在重新出现在目录中。','PRICE_DROP':'与上一次完整采集相比，售价下降。',
         'PRICE_RISE':'与上一次完整采集相比，售价上涨。'}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',Helvetica,Arial,sans-serif"
INK, MUTED, FAINT, LINE, BG = '#101828', '#475467', '#98a2b3', '#eaecf0', '#f2f4f7'


def label(kind): return LABELS.get(kind) or ALERT_LABELS.get(kind, kind)


def admin_url(): return os.getenv('MONITOR_ADMIN_URL', 'http://127.0.0.1:8080').rstrip('/')


def message(site, event):
    payload = event['payload']
    if event['kind'] == 'ACCESS_DENIED':
        return f"商品监控告警 · {site['name']}\n{payload['message']}\n请核对站点政策和访问许可后人工恢复。"
    if event['kind'] == 'TEST':
        return (f"【测试】商品监控邮件通道 · {site['name']}\n这是一封由管理员在后台手动触发的测试邮件，用于确认 Agent Mail 通道可用。\n"
                f"收到本邮件说明发件账号、收件邮箱和本机工作进程配置正确。\n事件编号：{event['id']}")
    p = payload['product']
    # Plain text format prevents scraped names from injecting markdown or mentions.
    return '\n'.join([f"{LABELS[event['kind']]} · {site['name']}", p['name'],
        f"价格：{p['sale_price']} {p.get('currency') or ''}",
        f"原价：{p.get('original_price') or '未知'}；折扣：{p.get('discount') or '未知'}",
        *([f"此前价格：{payload['old_price']}"] if payload.get('old_price') is not None else []),
        f"购买链接：{p['url']}", f"事件编号：{event['id']}"])


def subject(site, event):
    return message(site, event).splitlines()[0][:180]


def _dec(value):
    try: return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError): return None


def _pct(old, new):
    old, new = _dec(old), _dec(new)
    if old is None or new is None or old == 0: return ''
    pct = ((new - old) / old * 100).quantize(Decimal('0.1'))
    return f"{'+' if pct > 0 else ''}{pct}%"


def _money(value, currency):
    number = _dec(value)
    text = f'{number:,.2f}' if number is not None else str(value or '—')
    symbol = {'EUR':'€','USD':'$','GBP':'£','CNY':'¥','JPY':'¥'}.get((currency or '').upper())
    return f'{symbol}{text}' if symbol else f'{text} {currency or ""}'.strip()


def _when(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if moment.tzinfo is None: moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return ''


def _safe_url(url):
    return url if url and urlsplit(str(url)).scheme in ('http', 'https') else ''


e = html.escape


def _pill(kind, text=None):
    fg, bg = ACCENTS[kind]
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{bg}" style="background:{bg};color:{fg};font-size:12px;line-height:18px;font-weight:600;'
            f'padding:3px 10px;border-radius:999px;font-family:{FONT}">{e(text or label(kind))}</td></tr></table>')


def _button(url, text, dark=True):
    bg, fg, border = ('#101828', '#ffffff', '#101828') if dark else ('#ffffff', INK, '#d0d5dd')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{bg}" style="border-radius:8px;background:{bg};border:1px solid {border}">'
            f'<a href="{e(url, quote=True)}" target="_blank" style="display:inline-block;padding:11px 20px;font-family:{FONT};'
            f'font-size:14px;line-height:20px;font-weight:600;color:{fg};text-decoration:none;border-radius:8px">{e(text)}</a>'
            '</td></tr></table>')


def _thumb(p):
    image = _safe_url(p.get('image'))
    if image:
        return (f'<img src="{e(image, quote=True)}" width="88" height="88" alt="{e(p.get("name") or "")}" '
                f'style="display:block;width:88px;height:88px;object-fit:cover;border-radius:10px;border:1px solid {LINE};background:{BG}">')
    initial = e(((p.get('brand') or p.get('name') or '?').strip() or '?')[0].upper())
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="88"><tr>'
            f'<td width="88" height="88" align="center" valign="middle" bgcolor="{BG}" style="width:88px;height:88px;background:{BG};'
            f'border-radius:10px;border:1px solid {LINE};font-family:{FONT};font-size:30px;font-weight:700;color:{FAINT}">{initial}</td></tr></table>')


def _product(kind, p, old_price):
    currency = p.get('currency') or ''
    new = _money(p.get('sale_price'), currency)
    price = f'<span style="font-size:20px;line-height:28px;font-weight:700;color:{INK}">{e(new)}</span>'
    if old_price is not None:
        fg, bg = ACCENTS[kind]
        pct = _pct(old_price, p.get('sale_price'))
        price = (f'<span style="font-size:14px;color:{FAINT};text-decoration:line-through">{e(_money(old_price, currency))}</span>'
                 f'&nbsp;&nbsp;{price}' + (f'&nbsp;&nbsp;<span style="display:inline-block;background:{bg};color:{fg};font-size:12px;'
                 f'font-weight:600;line-height:18px;padding:2px 8px;border-radius:999px;vertical-align:3px">{e(pct)}</span>' if pct else ''))
    elif p.get('original_price') and _dec(p.get('original_price')) != _dec(p.get('sale_price')):
        price += f'&nbsp;&nbsp;<span style="font-size:14px;color:{FAINT};text-decoration:line-through">{e(_money(p["original_price"], currency))}</span>'
    if kind == 'REMOVED':
        price = f'<span style="font-size:13px;color:{FAINT}">下架前售价</span>&nbsp;&nbsp;' + price
    meta = ' · '.join(x for x in [
        f"原价 {_money(p['original_price'], currency)}" if p.get('original_price') else '',
        f"折扣 {p['discount']}" if p.get('discount') else '', p.get('label') or '',
        f"ID {p['product_id']}" if p.get('product_id') else ''] if x)
    name = e(p.get('name') or '')
    url = _safe_url(p.get('url'))
    if url and kind != 'REMOVED':
        name = f'<a href="{e(url, quote=True)}" target="_blank" style="color:{INK};text-decoration:none">{name}</a>'
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border:1px solid {LINE};border-radius:12px"><tr>'
            f'<td width="88" valign="top" style="padding:16px 0 16px 16px;width:88px">{_thumb(p)}</td>'
            f'<td valign="top" style="padding:14px 16px 14px 16px;font-family:{FONT}">'
            + (f'<div style="font-size:12px;line-height:18px;color:{FAINT};letter-spacing:.4px;text-transform:uppercase">{e(p["brand"])}</div>' if p.get('brand') else '')
            + f'<div style="font-size:16px;line-height:24px;font-weight:600;color:{INK};margin:2px 0 6px">{name}</div>'
            f'<div style="margin:0 0 6px">{price}</div>'
            + (f'<div style="font-size:12px;line-height:18px;color:{MUTED}">{e(meta)}</div>' if meta else '')
            + '</td></tr></table>')


def _note(text, kind='TEST'):
    fg, bg = ACCENTS[kind]
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{bg}" style="background:{bg};border-radius:10px;padding:14px 16px;font-family:{FONT};font-size:14px;'
            f'line-height:22px;color:{INK}">{text}</td></tr></table>')


def _rows(pairs):
    cells = ''.join(f'<tr><td valign="top" style="padding:9px 0;font-size:13px;line-height:20px;color:{MUTED};width:96px;border-bottom:1px solid {LINE}">{e(k)}</td>'
                    f'<td valign="top" style="padding:9px 0;font-size:13px;line-height:20px;color:{INK};border-bottom:1px solid {LINE};word-break:break-all">{e(str(v))}</td></tr>'
                    for k, v in pairs if v)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-family:{FONT}">{cells}</table>'


def render_html(site, event, summary=None):
    """summary (optional): {'total': products in the run, 'changes': events in the run}."""
    kind, payload = event['kind'], event['payload']
    when = _when(event.get('at'))
    admin = admin_url()
    facts = [when and f'{when} 北京时间']
    if summary and summary.get('total') is not None: facts.append(f"本次完整采集 {summary['total']} 件")
    if summary and summary.get('changes'): facts.append(f"共 {summary['changes']} 条变化")
    meta_line = ' · '.join(x for x in facts if x)
    action = ''
    if kind in LABELS:
        p = payload.get('product', {})
        headline = HEADLINES[kind]
        old, new = _dec(payload.get('old_price')), _dec(p.get('sale_price'))
        if kind in ('PRICE_DROP', 'PRICE_RISE') and old is not None and new is not None and old != new:
            headline = f"{'降价' if new < old else '涨价'} {_money(abs(new - old), p.get('currency'))}（{_pct(old, new)}）"
        preheader = f"{label(kind)}：{p.get('name') or ''} {_money(p.get('sale_price'), p.get('currency'))}"
        body = (f'<p style="margin:0 0 16px;font-size:14px;line-height:22px;color:{MUTED}">{e(INTRO[kind])}</p>'
                + _product(kind, p, payload.get('old_price')))
        url = _safe_url(p.get('url'))
        if url and kind != 'REMOVED': action = _button(url, '查看商品 →')
        elif kind == 'REMOVED': action = _button(f'{admin}/#events', '查看变化历史', dark=False)
    elif kind == 'ACCESS_DENIED':
        headline, preheader = '网站拒绝了自动采集', payload['message']
        body = (_note(f'<strong style="color:{ACCENTS[kind][0]}">{e(payload["message"])}</strong><br>'
                      '该网站的自动采集已暂停，上一份成功快照保持不变，不会因此产生下架通知。', kind)
                + f'<p style="margin:16px 0 0;font-size:14px;line-height:22px;color:{MUTED}">恢复前请核对站点使用政策与自动化访问许可，'
                  '再在后台“网站管理”中重新启用该网站。</p>')
        action = _button(f'{admin}/#sites', '打开网站管理')
    else:  # TEST
        headline, preheader = '邮件通道测试成功', '收到这封邮件，说明 Agent Mail 通道已可用。'
        body = (_note('收到这封邮件，说明 <strong>发件账号、收件邮箱和本机 Agent Mail 助手</strong> 均已配置正确。'
                      '此邮件由管理员在后台手动触发，不代表商品发生了变化。', kind)
                + '<div style="height:16px;line-height:16px">&nbsp;</div>'
                + _rows([('网站', site['name']), ('发送通道', 'Agent Mail CLI'), ('触发时间', when and f'{when} 北京时间'),
                         ('事件编号', event['id'])]))
        action = _button(f'{admin}/#notifications', '打开通知设置')
    reason = (f'你收到此邮件，是因为在商品监控后台为「{e(site["name"])}」启用了{e(label(kind))}通知。' if kind in LABELS
              else '你收到此邮件，是因为你是该网站通知的收件人。')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="x-apple-disable-message-reformatting">'
            f'<title>{e(subject(site, event))}</title></head>'
            f'<body style="margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%">'
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{BG}">{e(preheader)}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{BG}" style="background:{BG}">'
            '<tr><td align="center" style="padding:32px 12px">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px">'
            # brand bar
            f'<tr><td style="padding:0 4px 16px;font-family:{FONT}"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="font-family:{FONT}"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td width="28" height="28" align="center" bgcolor="#101828" style="width:28px;height:28px;background:#101828;border-radius:7px;'
            f'color:#ffffff;font-size:14px;font-weight:700;font-family:{FONT}">监</td>'
            f'<td style="padding-left:10px;font-size:15px;font-weight:600;color:{INK};font-family:{FONT}">商品监控</td></tr></table></td>'
            f'<td align="right" style="font-size:13px;color:{MUTED};font-family:{FONT}">{e(site["name"])}</td></tr></table></td></tr>'
            # card
            f'<tr><td bgcolor="#ffffff" style="background:#ffffff;border:1px solid {LINE};border-radius:16px;padding:28px 28px 24px;font-family:{FONT}">'
            f'{_pill(kind)}'
            f'<h1 style="margin:14px 0 6px;font-size:22px;line-height:30px;font-weight:700;color:{INK};font-family:{FONT}">{e(headline)}</h1>'
            f'<p style="margin:0 0 20px;font-size:13px;line-height:20px;color:{FAINT}">{e(meta_line)}</p>'
            f'{body}'
            + (f'<div style="height:22px;line-height:22px">&nbsp;</div>{action}' if action else '')
            + '</td></tr>'
            # footer
            f'<tr><td style="padding:20px 8px 0;font-family:{FONT};font-size:12px;line-height:19px;color:{FAINT}" align="center">'
            f'{reason}<br>'
            f'<a href="{e(admin, quote=True)}/#notifications" target="_blank" style="color:{MUTED};text-decoration:underline">管理通知设置 / 退订</a>'
            f'&nbsp;·&nbsp;事件编号 {e(str(event["id"]))}<br>商品监控 · 自动发送，请勿直接回复</td></tr>'
            '</table></td></tr></table></body></html>')
