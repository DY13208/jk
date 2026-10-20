import json
import pytest
from adapters import collect_bergzeit
from crawl4ai_run.run_crawl4ai import CollectionError
from monitor.extension_capture import normalize
from tests.test_monitor import db  # noqa: F401
from tests.test_generic_catalog import page

URL = 'https://www.bergzeit.fr/marques/arcteryx/?sortBy=new'


def bz(ids, total):
    items = [{'type': 'product', 'data': {'id': str(i), 'name': f'P{i}', 'url': f'/p/x/{i}/', 'price': {'current': '10,00 €'}}} for i in ids]
    return 200, f'<script>resultCount: {total},\n elementsList: {json.dumps(items)},</script>'


def fake(attempts):
    state = {'attempt': -1}
    def fetch(url):
        page = int(url.rsplit('p=', 1)[1])
        if page == 1: state['attempt'] = min(state['attempt'] + 1, len(attempts) - 1)
        pages = attempts[state['attempt']]
        return pages[page - 1] if page <= len(pages) else bz([], pages[-1][1].split('resultCount: ')[1].split(',')[0])
    return fetch, state


def test_bergzeit_retries_when_total_changes_then_accepts_within_tolerance():
    fetch, _ = fake([[bz(range(0, 2), 4), bz(range(2, 3), 9)],            # attempt 1: total jumps 4 -> 9
                     [bz(range(0, 2), 4), bz(range(1, 4), 5)]])           # attempt 2: shifted duplicate, 5 vs 4 (tolerance 1)
    doc = collect_bergzeit(URL, {'page_delay': 0, 'retry_delays': [0]}, fetch=fetch)
    assert doc['meta']['site_total'] == 4 and doc['meta']['attempts'] == 2
    assert sorted(p['product_id'] for p in doc['products']) == ['0', '1', '2', '3']


def test_bergzeit_fails_without_commit_when_still_inconsistent():
    fetch, _ = fake([[bz(range(0, 2), 10), bz([], 10)]])
    with pytest.raises(CollectionError, match='持续变化'):
        collect_bergzeit(URL, {'page_delay': 0, 'attempts': 2, 'retry_delays': [0]}, fetch=fetch)


def test_extension_empty_page_distinguishes_challenge_from_empty(db):
    site = db.site('site2'); site.update(url='https://shop.test/list', adapter='css_catalog', adapter_config={'execution_mode': 'extension'})
    challenge = {'checkpoints': [{'page': 1, 'url': 'https://shop.test/list', 'title': 'Just a moment...', 'html': '<html><body><p>wait</p></body></html>'}]}
    with pytest.raises(CollectionError, match='Cloudflare') as error: normalize(site, challenge)
    assert error.value.status == 403
    site['adapter_config']['schema'] = {'name': 'p', 'baseSelector': 'div.product-card', 'fields': [{'name': 'name', 'selector': 'h3', 'type': 'text'}]}
    empty = {'checkpoints': [{'page': 1, 'url': 'https://shop.test/list', 'title': 'Shop', 'cards': 0, 'html': '<html><body><p>no cards</p></body></html>'}]}
    with pytest.raises(CollectionError, match='匹配 0 个') as error: normalize(site, empty)
    assert error.value.status is None


def test_bergzeit_backs_off_on_503_then_succeeds():
    answers = [(503, ''), (503, ''), bz(range(0, 2), 2)]
    def fetch(url): return answers.pop(0) if answers else bz([], 2)
    doc = collect_bergzeit(URL, {'page_delay': 0, 'retry_delays': [0, 0, 0]}, fetch=fetch)
    assert doc['meta']['site_total'] == 2
