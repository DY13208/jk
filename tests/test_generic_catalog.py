"""Generic (non-Odoo) catalogue support: heuristic schema, next-page detection, extension page captures."""
import pytest
from crawl4ai_run.run_crawl4ai import auto_schema, next_page_link, CollectionError
from monitor.extension_capture import normalize
from tests.test_monitor import db  # noqa: F401 (fixture)


def card(i, price='49,95 €', old=None):
    extra = f'<del class="price-old">{old}</del>' if old else ''
    return (f'<div class="product-card"><a class="card-link" href="/p/item-{i}"><img data-src="/img/{i}.jpg" src="data:x">'
            f'<h3 class="card-title">Item {i}</h3></a><div class="card-prices"><span class="price-now">{price}</span>{extra}</div>'
            f'<a href="/account/login">wishlist</a></div>')


def page(ids, nxt=None, **kw):
    link = f'<a class="pager-next" rel="next" href="{nxt}">Next</a>' if nxt else ''
    return '<html><body><nav><a href="/">home</a></nav><main>' + ''.join(card(i, **kw) for i in ids) + link + '</main></body></html>'


def test_auto_schema_finds_cards_link_name_price_image():
    schema = auto_schema(page(range(6)), 'https://shop.test/list')
    fields = {f['name']: f for f in schema['fields']}
    assert schema['baseSelector'] == 'div.product-card'
    assert fields['url']['selector'] == 'a.card-link' and fields['name']['selector'] == 'h3.card-title'
    assert 'price-now' in fields['sale_price']['selector'] and fields['image']['attribute'] == 'data-src'


def test_auto_schema_rejects_pages_without_products():
    with pytest.raises(CollectionError): auto_schema('<html><body><p>nothing</p></body></html>', 'https://shop.test/')


def test_next_page_link_variants():
    assert next_page_link(page([1], '/list?page=2'), 'https://shop.test/list', {}, set()) == 'https://shop.test/list?page=2'
    html = '<a href="/list?page=2">2</a><a href="/list?page=3">3</a>'
    assert next_page_link(html, 'https://shop.test/list?page=2', {}, set()) == 'https://shop.test/list?page=3'
    assert next_page_link('<a href="/list?page=2">2</a>', 'https://shop.test/list?page=2', {}, set()) is None
    with pytest.raises(CollectionError): next_page_link('<a rel="next" href="https://evil.test/x">n</a>', 'https://shop.test/list', {}, set())


def test_extension_generic_pages_auto_schema(db):
    site = db.site('site2'); site.update(url='https://shop.test/list', adapter='css_catalog', adapter_config={'execution_mode': 'extension'})
    capture = {'checkpoints': [{'page': 1, 'url': 'https://shop.test/list', 'html': page(range(5), '/list?page=2')},
                               {'page': 2, 'url': 'https://shop.test/list?page=2', 'html': page(range(5, 8), old='60,00 €')}]}
    document = normalize(site, capture)
    assert document['meta']['site_total'] == 8 and document['meta']['site_pages'] == 2
    assert document['products'][-1]['original_price'] == '60.00' and document['products'][0]['sale_price'] == '49.95'
    capture['checkpoints'][1]['url'] = 'https://evil.test/list'
    with pytest.raises(CollectionError): normalize(site, capture)
