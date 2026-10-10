"""Generate HTML/EML previews of every notification email from the real templates. Sends nothing."""
import html
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from monitor.mail_templates import render_html, message, subject, label  # noqa: E402

SITE = {'name': "Lecomte · Arc'teryx"}
BASE = 'https://www.lecomte-alpirando.be/en/shop/'


def product(pid, slug, name, sale, original, discount, label_):
    return {'product_id': pid, 'product_variant_id': None, 'name': name, 'brand': "Arc'teryx", 'sale_price': sale,
            'original_price': original, 'currency': 'EUR', 'discount': discount, 'label': label_,
            'url': f'{BASE}{slug}-{pid}', 'image': f'https://www.lecomte-alpirando.be/web/image/product.template/{pid}/image_256'}


# Real Lecomte catalogue entries (names, prices, links, Odoo product images) from a saved crawl;
# the changes themselves (old prices, removals) are illustrative.
SAMPLES = [
    ('NEW', 1201, product('44892', 'kyanite-jacket-cloud', 'Kyanite Jacket Cloud', '143.96', '179.95', '-20%', 'Promotion'), None),
    ('REMOVED', 1202, product('44880', 'atom-vest-black-mongoose', 'Atom Vest Black Mongoose', '159.96', '199.95', '-20%', 'Promotion'), None),
    ('RETURNED', 1203, product('44866', 'beta-ar-jacket-women-black', 'Beta AR Jacket Women Black', '519.96', '649.95', '-20%', 'Promotion'), None),
    ('PRICE_DROP', 1204, product('42700', 'beta-sv-jacket-fluidity', 'Beta SV Jacket Fluidity', '559.97', '799.95', '-30%', 'Best deal'), '639.96'),
    ('PRICE_RISE', 1205, product('44901', 'beta-sl-jacket-nucleus', 'Beta SL Jacket Nucleus', '449.96', '499.95', '-10%', 'Promotion'), '399.96'),
]
SUMMARY = {'total': 119, 'changes': 5}


def events():
    for kind, eid, p, old in SAMPLES:
        yield {'id': eid, 'kind': kind, 'at': '2026-10-10T08:15:12+00:00', 'payload': {'product': p, 'old_price': old}}, SUMMARY
    yield {'id': 1206, 'kind': 'ACCESS_DENIED', 'at': '2026-10-10T08:48:12+00:00',
           'payload': {'message': '站点拒绝访问 HTTP 403；监控已暂停'}}, None
    yield {'id': 'T3', 'kind': 'TEST', 'at': '2026-10-10T09:05:00+00:00', 'payload': {}}, None


def main():
    directory = ROOT / 'docs' / 'mail-previews'
    directory.mkdir(parents=True, exist_ok=True)
    cards = []
    for event, summary in events():
        body = render_html(SITE, event, summary)
        (directory / f"{event['kind']}.html").write_text(body, encoding='utf-8')
        mail = EmailMessage()
        mail['From'] = 'jetbra@agent.qq.com'
        mail['To'] = 'recipient@example.com'
        mail['Subject'] = subject(SITE, event)
        mail.set_content(message(SITE, event))
        mail.add_alternative(body, subtype='html')
        (directory / f"{event['kind']}.eml").write_bytes(mail.as_bytes())
        cards.append(f'<section><h2>{html.escape(label(event["kind"]))} <small>{event["kind"]}</small></h2>'
                     f'<iframe title="{event["kind"]}" srcdoc="{html.escape(body, quote=True)}" sandbox="" '
                     'style="width:100%;height:760px;border:0"></iframe></section>')
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>商品监控邮件样式预览</title><style>body{font-family:Arial,sans-serif;background:#eef1f5;margin:24px;color:#233140}'
            'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}section{background:white;border-radius:8px;overflow:hidden}'
            'h2{font-size:18px;padding:0 20px}h2 small{color:#8a96a3;font-weight:normal;font-size:12px}</style>'
            '<h1>商品监控邮件样式预览</h1><p>以下为示例数据（商品名和价格参照已保存的 Lecomte 采集结果），不是真实变化，也没有发送任何邮件。'
            'SMTP 与 Agent Mail 使用同一 HTML 模板；SMTP 另附纯文本版本。</p><main>' + ''.join(cards) + '</main></html>')
    (ROOT / 'admin' / 'mail-previews.html').write_text(page, encoding='utf-8')
    print(f'已生成 {len(cards)} 类 HTML / EML 邮件预览于 {directory}；没有发送邮件。')


if __name__ == '__main__':
    main()
