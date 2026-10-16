import asyncio
from types import SimpleNamespace

import pytest
from crawl4ai_run.run_crawl4ai import collect, CollectionError
from tests.test_monitor import FakeCrawler, page


class RunConfig:
    def clone(self,**kwargs): return SimpleNamespace(**kwargs)


@pytest.mark.parametrize('scenario',['complete','lost','duplicate','changed','partial','403','stalled'])
def test_cumulative_load_more_integrity(scenario):
    first=page([1,2],links=False)
    second=page([1,2,3,4],links=False)
    if scenario=='lost': second=page([2,3,4],links=False)
    if scenario=='duplicate': second=page([1,2,3,3],links=False)
    if scenario=='changed': second.extracted_content=second.extracted_content.replace('10.00','11.00',1)
    if scenario=='partial': second=page([1,2,3],links=False)
    if scenario=='403': second=page([],status=403,links=False)
    if scenario=='stalled': second=page([1,2],links=False)
    class Recorder(FakeCrawler):
        async def arun(self,**kwargs):
            if hasattr(kwargs['config'],'js_only'):
                assert kwargs['config'].js_only
                assert 'click()' in kwargs['config'].js_code_before_wait
            assert kwargs['url']=='https://example.test/shop?brand=x'
            return await super().arun(**kwargs)
    async def run():
        return await collect(dict(url='https://example.test/shop?brand=x',pagination_mode='load_more',page_delay=0),
            factory=lambda _:Recorder([first,second]),run_config_factory=RunConfig)
    if scenario=='complete':
        result=asyncio.run(run())
        assert [x['product_id'] for x in result['products']]==['1','2','3','4']
        assert [p['items'] for p in result['meta']['pages']]==[2,2]
        assert [p['listing_page'] for p in result['products']]==[1,1,2,2]
    else:
        with pytest.raises(CollectionError): asyncio.run(run())


@pytest.mark.skipif(__import__('os').getenv('RUN_HEADED_TEST')!='1',reason='requires Chrome + Xvfb')
def test_real_chrome_load_more_without_direct_page_navigation():
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from urllib.parse import urlsplit,parse_qs
    calls=[]
    def cards(start,end):
        return ''.join(f'<div class="tp-product-item" data-product-template-id="{i}"><h6 class="tp-product-title"><a title="Product number {i}" href="/product-{i}">Product number {i}</a></h6><span itemprop="price">10.00</span><span itemprop="priceCurrency">EUR</span></div>' for i in range(start,end))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            parsed=urlsplit(self.path);calls.append(parsed.path)
            if '/page/' in parsed.path:
                self.send_response(403);self.end_headers();return
            self.send_response(200);self.end_headers()
            if parsed.path=='/robots.txt': self.wfile.write(b'User-agent: *\nAllow: /');return
            if parsed.path=='/batch':
                stage=int(parse_qs(parsed.query)['stage'][0]);self.wfile.write(cards(stage*30,(stage+1)*30).encode());return
            body='<html><body><main id="products">'+cards(0,30)+'</main><div class="tp-product-pager" data-page-count="3" data-total-count="90"></div><button class="tp-load-more-btn">Load more</button>'
            body+='''<script>let stage=0;document.querySelector('button').onclick=async()=>{stage++;let r=await fetch('/batch?stage='+stage);document.querySelector('#products').insertAdjacentHTML('beforeend',await r.text());if(stage==2)document.querySelector('button').remove();};</script></body></html>'''
            self.wfile.write(body.encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        result=asyncio.run(collect(dict(url=f'http://127.0.0.1:{server.server_port}/shop',pagination_mode='load_more',page_delay=0)))
        assert len(result['products'])==90
        assert [p['items'] for p in result['meta']['pages']]==[30,30,30]
        assert '/batch' in calls and not any('/page/' in p for p in calls)
    finally: server.shutdown();server.server_close();thread.join()
