"""Windows native Chrome/CDP acceptance against an owned local fixture only."""
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import pytest

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows native Chrome acceptance')


def test_native_chrome_full_dynamic_catalogue():
    from adapters import odoo_lecomte
    from scripts.native_chrome import browser_factory
    def cards(start,end):
        return ''.join(f'<div class="tp-product-item" data-product-template-id="{i}"><h6 class="tp-product-title"><a title="Product {i}" href="/item-{i}">Product {i}</a></h6><span itemprop="price">10.00</span></div>' for i in range(start,end))
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200);self.end_headers()
            if self.path=='/robots.txt': self.wfile.write(b'User-agent: *\nAllow: /');return
            body='<html><body><main id="products">'+cards(0,30)+'</main><div class="tp-product-pager" data-total-count="90" data-page-count="3"></div><button class="tp-load-more-btn">Load more products</button>'
            import json
            body+='<script>const chunks='+json.dumps([cards(30,60),cards(60,90)])+';let page=0;document.querySelector("button").onclick=()=>{document.querySelector("#products").insertAdjacentHTML("beforeend",chunks[page++]);if(page===2)document.querySelector("button").remove();};</script></body></html>'
            self.wfile.write(body.encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    site={'url':f'http://127.0.0.1:{server.server_port}/shop','adapter_config':{'pagination_mode':'load_more','page_delay':0}}
    try:
        result=asyncio.run(odoo_lecomte(site,factory=browser_factory(site)))
        assert len(result['products'])==90
        assert len({p['product_id'] for p in result['products']})==90
        assert [p['items'] for p in result['meta']['pages']]==[30,30,30]
        assert result['meta']['complete'] is True
        assert not any('/page/' in path for path in calls)
    finally: server.shutdown();server.server_close();thread.join()
