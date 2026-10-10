import asyncio
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
import yaml

from monitor.collector import collect, parse
from monitor.store import Incomplete


def card(i):
    return f'<article class="product" data-id="{i}" data-sku="SKU-{i}" data-stock="in_stock"><h2>P{i}</h2><span class="price">10</span><span class="original">20</span><a href="/p/{i}">Open</a></article>'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        mode = parsed.path
        n = int(parse_qs(parsed.query).get('page', ['1'])[0])
        status = 403 if mode == '/blocked' or (mode == '/fail' and n == 2) else 200
        content = '<div id="total" data-count="4"></div>'
        if mode in ('/dynamic', '/scroll'):
            content += card(1) + card(2)
            action = "document.body.insertAdjacentHTML('beforeend'," + repr(card(3) + card(4) + '<div id="end"></div>') + ");this.remove()"
            content += f'<button class="more" onclick="{escape(action, quote=True)}">More</button>'
            if mode == '/scroll':
                content += '<div style="height:2000px"></div><script>window.addEventListener("scroll",()=>{let b=document.querySelector("button");if(b)b.click()})</script>'
        elif mode == '/missing':
            content += card(1) + '<div id="end"></div>'
        else:
            content += card(n * 2 - 1) + card(n * 2)
            if n == 1:
                content += f'<a class="next" href="{mode}?page=2">Next</a>'
            else:
                content += '<div id="end"></div>'
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(content.encode())


@pytest.fixture
def config():
    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = yaml.safe_load(Path('config/example.yaml').read_text())['sites']['example']
    cfg['url'] = f'http://127.0.0.1:{server.server_port}/shop?page=1'
    yield cfg
    server.shutdown()
    server.server_close()


def test_all_pages(config):
    products, evidence = asyncio.run(collect(config))
    assert {p['id'] for p in products} == {'1', '2', '3', '4'}
    assert evidence['expected'] == 4 and len(evidence['pages']) == 2
    assert all(p['sku'] and p['stock'] for p in products)


@pytest.mark.parametrize('path,mode', [('/dynamic', 'load_more'), ('/scroll', 'scroll')])
def test_dynamic_complete(config, path, mode):
    config['url'] = config['url'].replace('/shop?page=1', path)
    config['dynamic'] = mode
    config['load_more_selector'] = 'button.more'
    products, evidence = asyncio.run(collect(config))
    assert len(products) == 4 and evidence['complete']


@pytest.mark.parametrize('path', ['/blocked', '/fail', '/missing'])
def test_fail_closed(config, path):
    config['url'] = config['url'].replace('/shop?page=1', path)
    with pytest.raises(Incomplete):
        asyncio.run(collect(config))


def test_total_selector_required(config):
    with pytest.raises(Incomplete):
        parse(card(1), config['url'], config)


def test_unstable_complete_traversals_rejected(monkeypatch):
    from monitor import collector
    from tests.test_store import product
    counter = 0

    async def once(_):
        nonlocal counter
        counter += 1
        return [product(counter)], {'complete': True, 'expected': 1, 'pages': ['fixture']}

    monkeypatch.setattr(collector, 'collect_once', once)
    with pytest.raises(Incomplete, match='changed between'):
        asyncio.run(collector.collect({}))
