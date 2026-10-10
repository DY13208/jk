"""Real headed Chrome/Crawl4AI test against an owned local fixture, never the target site."""
import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from crawl4ai_run.run_crawl4ai import collect, crawler_factory, CollectionError
from monitor.database import Database
from monitor.notifications import Notifier

pytestmark=pytest.mark.skipif(os.getenv('RUN_HEADED_TEST')!='1',reason='requires Docker Chrome + Xvfb')


def test_headed_full_pipeline(tmp_path,monkeypatch):
    state={'phase':0,'webhooks':[],'webdriver':[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/robots.txt':
                self.send_response(200);self.end_headers();self.wfile.write(b'User-agent: *\nDisallow: /private\nAllow: /\n');return
            page=int(path.rsplit('/',1)[-1]) if '/page/' in path else 1
            if state['phase']==2 and page==2:
                self.send_response(403);self.end_headers();self.wfile.write(b'Your request was blocked.');return
            ids=list(range(90)) if state['phase']==0 else list(range(2,92))
            cards=[]
            for pid in ids[(page-1)*30:page*30]:
                cards.append(f'''<div class="tp-product-item" data-product-template-id="{pid}">
                    <input name="product_id" value="{pid+1000}"><h6 class="tp-product-title"><a href="/p-{pid}" title="商品 {pid}">商品 {pid}</a></h6>
                    <span itemprop="price">10.00</span><span itemprop="priceCurrency">EUR</span>
                    <del class="compare-price"><span class="oe_currency_value">20.00</span></del><span class="tp-discount-percentage">-50%</span></div>''')
            body='<html><body><div class="tp-product-pager" data-page-count="3" data-total-count="90"></div>'
            body+=''.join(f'<a href="/shop/page/{n}?brand=x">{n}</a>' for n in (2,3))+''.join(cards)+'</body></html>'
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(body.encode())
        def do_POST(self):
            state['webhooks'].append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"errcode":0}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/shop?brand=x'
    db=Database(tmp_path/'test.db');db.migrate()
    site=db.site('lecomte');site.update(url=url,enabled=True,permission_confirmed=True,policy_note='Owned local fixture',notify_enabled=True,webhook_env='WECOM_TEST')
    db.save_site(site)
    monkeypatch.setenv('WECOM_TEST','https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=LOCAL_TEST_ONLY')
    def factory(headless):
        crawler=crawler_factory(headless)
        async def on_page(page,context,**kwargs):
            state['webdriver'].append(await page.evaluate('navigator.webdriver'))
        crawler.crawler_strategy.set_hook('on_page_context_created',on_page)
        return crawler
    async def run():
        config={'url':url,'page_delay':0}
        first=await collect(config,factory=factory)
        assert len(first['products'])==90
        assert db.commit('lecomte',first)[1]==[]
        state['phase']=1
        second=await collect(config,factory=factory)
        events=db.commit('lecomte',second)[1]
        assert {(x['kind'],x['product_id']) for x in events}=={('NEW','90'),('NEW','91'),('REMOVED','0'),('REMOVED','1')}
        async def local_sender(ignored_url,payload):
            import httpx
            async with httpx.AsyncClient() as client:
                return await client.post(f'http://127.0.0.1:{server.server_port}/webhook',json=payload)
        notifier=Notifier(db,local_sender)
        await notifier.dispatch();await notifier.dispatch()
        assert len(state['webhooks'])==4
        state['phase']=2
        with pytest.raises(CollectionError) as error: await collect(config,factory=factory)
        assert error.value.status==403
        assert len(db.products('lecomte'))==90
        assert db.history('monitor_events')['total']==4
        assert state['webdriver'] and all(v is True for v in state['webdriver'])
        print('HEADED PIPELINE: 3 pages / 90 products; NEW=2 REMOVED=2; local webhook=4; 403 baseline retained; navigator.webdriver=true')
    try: asyncio.run(run())
    finally: server.shutdown();server.server_close();thread.join()
