import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from monitor.web import create_app
from monitor.extension_capture import normalize
from monitor import host_collector as host
from tests.test_monitor import db,enable
from crawl4ai_run.run_crawl4ai import CollectionError


def checkpoint(ids,page=1,total=90,pages=3):
    html=''.join(f'<div class="tp-product-item" data-product-template-id="{i}"><h6 class="tp-product-title"><a href="/product-{i}" title="Item {i}">Item {i}</a></h6><span itemprop="price">10.00</span></div>' for i in ids)
    html+=f'<div class="tp-product-pager" data-page-count="{pages}" data-total-count="{total}"></div>'
    return {'page':page,'html':html}


def capture(ids=range(90)):
    values=list(ids)
    return {'checkpoints':[checkpoint(values[:30]),checkpoint(values[:60],2),checkpoint(values,3)]}


def test_extension_reuses_crawl4ai_rules_and_same_ninety_changes(db):
    site=enable(db)
    first=normalize(site,capture());assert db.commit(site['id'],first)[1]==[]
    second=normalize(site,capture(range(2,92)))
    assert [p['items'] for p in second['meta']['pages']]==[30,30,30]
    events=db.commit(site['id'],second)[1]
    assert {(e['kind'],e['product_id']) for e in events}=={('REMOVED','0'),('REMOVED','1'),('NEW','90'),('NEW','91')}


@pytest.mark.parametrize('fault',['partial','duplicate','lost','changed_total','order','price','no_growth','empty'])
def test_extension_incomplete_capture_rejected(db,fault):
    data=capture()
    if fault=='partial': data['checkpoints'].pop()
    if fault=='duplicate': data['checkpoints'][2]=checkpoint(list(range(89))+[88],3)
    if fault=='lost': data['checkpoints'][1]=checkpoint(range(1,61),2)
    if fault=='changed_total': data['checkpoints'][1]=checkpoint(range(60),2,total=91)
    if fault=='order': data['checkpoints'][1]['page']=3
    if fault=='price': data['checkpoints'][1]['html']=data['checkpoints'][1]['html'].replace('10.00','9.00',1)
    if fault=='no_growth': data['checkpoints'][1]=checkpoint(range(30),2)
    if fault=='empty': data={}
    with pytest.raises(CollectionError): normalize(db.site('lecomte'),data)
    assert db.products('lecomte')==[]


def test_extension_jobs_are_isolated_from_native_worker(db):
    site=enable(db);site['adapter_config']['execution_mode']='extension';db.save_site(site)
    host.heartbeat(db,'extension')
    async def run():
        task=asyncio.create_task(host.collect(db,site));await asyncio.sleep(0)
        assert host.claim(db) is None
        job=host.claim(db,'extension');assert job
        with pytest.raises(LookupError): host.finish(db,{**job,'result':{'capture':capture()}})
        host.finish(db,{**job,'result':{'capture':capture()}},'extension')
        assert len((await task)['products'])==90
    asyncio.run(run())


def test_extension_token_cannot_access_native_worker_and_verify_is_read_only(tmp_path):
    with TestClient(create_app(tmp_path/'db.sqlite',scheduled=False)) as client:
        db=client.app.state.db
        site=db.site('lecomte');site['adapter_config']['execution_mode']='extension';db.save_site(site)
        headers={'x-collector-token':(tmp_path/'extension-worker.token').read_text()}
        assert client.post('/api/collector/worker/heartbeat',headers=headers).status_code==403
        assert client.post('/api/collector/extension/worker/heartbeat',headers=headers).status_code==200
        sites=client.post('/api/collector/extension/worker/sites',headers=headers).json()['items']
        assert len(sites)==1 and 'email_to' not in sites[0]
        result=client.post('/api/collector/extension/worker/verify',headers=headers,json={'site_id':'lecomte','capture':capture()})
        assert result.json()=={'products':90,'pages':3}
        assert db.products('lecomte')==[] and db.history('monitor_events')['total']==0


def test_extension_manifest_has_no_cookie_debugger_or_broad_site_access():
    root=Path(__file__).resolve().parents[1]/'chrome-extension'
    manifest=json.loads((root/'manifest.json').read_text())
    assert not {'cookies','debugger','webRequest','tabs'}.intersection(manifest['permissions'])
    assert '<all_urls>' not in manifest['host_permissions']
    assert not manifest.get('web_accessible_resources')


@pytest.mark.skipif(__import__('os').getenv('RUN_HEADED_TEST')!='1',reason='requires Chrome + Xvfb')
def test_extension_capture_script_real_dom_and_secret_stripping():
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from crawl4ai_run.run_crawl4ai import crawler_factory
    from crawl4ai import CrawlerRunConfig,CacheMode
    root=Path(__file__).resolve().parents[1]
    first=checkpoint(range(30))['html'].replace('<h6','<input name="csrf_token" value="CARD_SECRET"><h6',1)
    second=checkpoint(range(60),2)['html']
    third=checkpoint(range(90),3)['html']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            self.send_response(200);self.end_headers()
            if self.path=='/robots.txt': self.wfile.write(b'User-agent: *\nAllow: /');return
            body='<html><body><div id="catalogue">'+first+'</div><button class="tp-load-more-btn" href="/shop/page/2">Load more</button>'
            body+='<input name="password" value="DO_NOT_EXPORT"><script>let phase=0;const html='+json.dumps([second,third])+';document.querySelector("button").onclick=()=>{document.querySelector("#catalogue").innerHTML=html[phase++];};</script></body></html>'
            self.wfile.write(body.encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/shop'
    async def run():
        async with crawler_factory(False) as crawler:
            await crawler.arun(url=url,config=CrawlerRunConfig(session_id='catalog',cache_mode=CacheMode.BYPASS,check_robots_txt=True))
            page=crawler.crawler_strategy.browser_manager.sessions['catalog'][1]
            await page.add_script_tag(path=str(root/'chrome-extension'/'capture.js'))
            result=await page.evaluate('captureCatalogue({origin:location.origin,search:location.search})')
            assert len(result['checkpoints'])==3
            assert 'DO_NOT_EXPORT' not in json.dumps(result)
            assert 'CARD_SECRET' not in json.dumps(result)
            return result
    try: result=asyncio.run(run());assert len(result['checkpoints'])==3
    finally: server.shutdown();server.server_close();thread.join()
