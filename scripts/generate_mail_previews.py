"""Generate previews from the same templates used by both mail channels, without sending."""
import sys
from pathlib import Path
from email.message import EmailMessage
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from monitor.mail_templates import render_html, message, LABELS


def main():
    directory=ROOT/'docs/mail-previews';directory.mkdir(exist_ok=True)
    site={'name':"Lecomte · Arc'teryx"}
    # Clearly marked invented data, never attributed to a live collection.
    product={'name':'示例商品 · 山地防水外套（演示数据）','sale_price':'143.96','original_price':'179.95',
             'currency':'EUR','discount':'-20%','url':'https://www.lecomte-alpirando.be/shop?attribute_value=729-2672'}
    cards=[]
    for i,kind in enumerate([*LABELS,'ACCESS_DENIED'],1):
        event={'id':i,'kind':kind,'payload':{'product':dict(product)}}
        if kind=='PRICE_DROP': event['payload']['old_price']='159.95'
        if kind=='PRICE_RISE': event['payload']['old_price']='129.95'
        if kind=='ACCESS_DENIED': event['payload']={'message':'示例告警：站点拒绝访问 HTTP 403；监控已暂停，保留上一份成功快照。'}
        body=render_html(site,event)
        (directory/f'{kind}.html').write_text(body,encoding='utf-8')
        mail=EmailMessage();mail['From']='preview@example.test';mail['To']='recipient@example.test'
        mail['Subject']=message(site,event).splitlines()[0]
        mail.set_content(message(site,event));mail.add_alternative(body,subtype='html')
        (directory/f'{kind}.eml').write_bytes(mail.as_bytes())
        cards.append(f'<section><h2>{LABELS.get(kind,"访问拒绝告警")}</h2><iframe title="{kind}" srcdoc="{__import__("html").escape(body,quote=True)}" sandbox="" style="width:100%;height:510px;border:0"></iframe></section>')
    preview='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品监控邮件样式预览</title><style>body{font-family:Arial,sans-serif;background:#eef1f5;margin:24px;color:#233140}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}section{background:white;border-radius:8px;overflow:hidden}h2{font-size:18px;padding:0 20px}</style><h1>商品监控邮件样式预览</h1><p>以下全部为演示数据，不是真实商品变化。SMTP 与 Agent Mail 使用相同 HTML 模板，同时提供纯文本版本。</p><main>'+''.join(cards)+'</main></html>'
    (ROOT/'admin/mail-previews.html').write_text(preview,encoding='utf-8')
    print('已生成 6 类 HTML / EML 邮件预览；没有发送邮件。')


if __name__=='__main__':main()
