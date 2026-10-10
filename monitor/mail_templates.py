"""Shared notification content; no network calls."""
import html
from urllib.parse import urlsplit

LABELS = {'NEW':'新品上架','REMOVED':'商品下架','RETURNED':'重新上架','PRICE_DROP':'价格下降','PRICE_RISE':'价格上涨'}


def message(site, event):
    payload = event['payload']
    if event['kind'] == 'ACCESS_DENIED':
        return f"商品监控告警 · {site['name']}\n{payload['message']}\n请核对站点政策和访问许可后人工恢复。"
    p = payload['product']
    # Plain text format prevents scraped names from injecting markdown or mentions.
    return '\n'.join([f"{LABELS[event['kind']]} · {site['name']}", p['name'],
        f"价格：{p['sale_price']} {p.get('currency') or ''}",
        f"原价：{p.get('original_price') or '未知'}；折扣：{p.get('discount') or '未知'}",
        *([f"此前价格：{payload['old_price']}"] if payload.get('old_price') is not None else []),
        f"购买链接：{p['url']}", f"事件编号：{event['id']}"])


def render_html(site, event):
    color = {'NEW':'#167d55','REMOVED':'#a33e36','RETURNED':'#2163ad',
             'PRICE_DROP':'#167d55','PRICE_RISE':'#a56c11','ACCESS_DENIED':'#a33e36'}[event['kind']]
    lines = message(site,event).splitlines()
    body = ''.join(f'<p style="margin:12px 0;line-height:1.7">{html.escape(line)}</p>' for line in lines[1:])
    product = event['payload'].get('product',{})
    url = product.get('url','')
    if urlsplit(url).scheme in ('http','https') and event['kind'] != 'REMOVED':
        body += f'<p><a href="{html.escape(url,quote=True)}" style="display:inline-block;background:{color};color:white;padding:12px 20px;text-decoration:none;border-radius:6px">查看商品</a></p>'
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body style="margin:0;background:#f4f6f8;font-family:Arial,sans-serif;color:#233140">'
            f'<div style="max-width:600px;margin:24px auto;background:white;border:1px solid #e0e5eb;border-radius:10px;padding:28px">'
            f'<h1 style="font-size:22px;color:{color};margin-top:0">{html.escape(lines[0])}</h1>{body}</div></body></html>')

