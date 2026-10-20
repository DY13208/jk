"""FlareSolverr fetch path (execution_mode=flaresolverr), fully mocked: no network, no container."""
import asyncio
import json
import pytest
from adapters import collect_flaresolverr, flaresolverr_challenge
from crawl4ai_run.run_crawl4ai import CollectionError
from monitor.extension_capture import normalize
from tests.test_generic_catalog import page
from tests.test_monitor import db  # noqa: F401 (fixture)

URL = 'https://shop.test/list'
SCHEMA = {'name': 'p', 'baseSelector': 'div.product-card', 'fields': [
    {'name': 'product_template_id', 'selector': 'a.card-link', 'type': 'attribute', 'attribute': 'href'},
    {'name': 'url', 'selector': 'a.card-link', 'type': 'attribute', 'attribute': 'href'},
    {'name': 'name', 'selector': 'h3.card-title', 'type': 'text'},
    {'name': 'sale_price', 'selector': 'span.price-now', 'type': 'regex', 'pattern': r'(\d[\d.,\s\u00a0]*\d|\d)'},
    {'name': 'image', 'selector': 'img', 'type': 'attribute', 'attribute': 'data-src'}]}
CONFIG = {'schema': SCHEMA, 'decimal_separator': ',', 'page_delay': 8, 'execution_mode': 'flaresolverr'}
CHALLENGE = '<html><head><title>Just a moment...</title></head><body><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/x"></script></body></html>'
PAGES = {URL: page([1, 2], '/list?page=2'), URL + '?page=2': page([3])}


class Solver:
    """Records every FlareSolverr command; answers request.get from a scripted list or PAGES."""
    def __init__(self, script=None):
        self.calls, self.script = [], list(script or [])
    def __call__(self, payload):
        self.calls.append(payload)
        if payload['cmd'] != 'request.get': return {'status': 'ok', 'message': ''}
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception): raise item
            if isinstance(item, dict): return item
            html = item
        else: html = PAGES[payload['url']]
        return {'status': 'ok', 'message': 'Challenge solved!', 'solution': {'url': payload['url'], 'status': 200, 'response': html}}


def run(solver, sleeps=None, config=CONFIG):
    return collect_flaresolverr(URL, config, post=solver, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_one_session_reused_for_all_pages_and_destroyed(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda s: None)
    solver = Solver(); doc = run(solver)
    cmds = [c['cmd'] for c in solver.calls]
    assert cmds == ['sessions.create', 'request.get', 'request.get', 'sessions.destroy']
    assert len({c['session'] for c in solver.calls}) == 1
    assert all(c['maxTimeout'] == 120000 for c in solver.calls if c['cmd'] == 'request.get')
    assert [p['product_id'] for p in doc['products']] == ['/p/item-1', '/p/item-2', '/p/item-3']
    assert doc['meta']['complete'] and doc['meta']['tool'] == 'flaresolverr' and doc['meta']['site_pages'] == 2


def test_page_delay_is_honoured(monkeypatch):
    delays = []; monkeypatch.setattr('time.sleep', delays.append)
    run(Solver()); assert delays == [8]


def test_same_products_as_extension_capture(monkeypatch, db):
    """Identity/price/fields must equal the extension path so switching modes creates no events."""
    monkeypatch.setattr('time.sleep', lambda s: None)
    site = {'id': 'x', 'adapter': 'css_catalog', 'url': URL, 'adapter_config': CONFIG}
    capture = {'checkpoints': [{'page': i, 'url': u, 'title': 't', 'html': h} for i, (u, h) in enumerate(PAGES.items(), 1)]}
    assert run(Solver())['products'] == normalize(site, capture)['products']


def test_retry_with_backoff_then_success(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda s: None)
    sleeps = []
    solver = Solver([{'status': 'error', 'message': 'Error solving the challenge. Timeout after 120.0 seconds.'}, CHALLENGE])
    doc = run(solver, sleeps)
    assert sleeps == [15, 45] and len(doc['products']) == 3


@pytest.mark.parametrize('answer', [CHALLENGE, {'status': 'error', 'message': 'Timeout'}])
def test_persistent_challenge_or_error_fails_without_pause(answer):
    solver = Solver([answer] * 3)
    with pytest.raises(CollectionError) as err: run(solver)
    assert err.value.status is None  # not a 403 pause: the next interval retries
    assert solver.calls[-1]['cmd'] == 'sessions.destroy'


def test_challenge_on_later_page_fails_whole_crawl(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda s: None)
    solver = Solver([PAGES[URL], CHALLENGE, CHALLENGE, CHALLENGE])
    with pytest.raises(CollectionError): run(solver)
    assert solver.calls[-1]['cmd'] == 'sessions.destroy'


def test_page_without_products_fails():
    with pytest.raises(CollectionError): run(Solver(['<html><title>Arc</title><body><p>vide</p></body></html>']))


def test_solver_unreachable_is_collection_error():
    def down(payload): raise ConnectionRefusedError()
    with pytest.raises(CollectionError, match='FlareSolverr'): run(down)


def test_challenge_detection():
    assert flaresolverr_challenge(CHALLENGE)
    assert flaresolverr_challenge('<title>Un instant…</title>')
    assert not flaresolverr_challenge(page([1]))


def test_service_routes_flaresolverr_mode_and_no_events_on_incomplete_crawl(db, monkeypatch):
    from monitor.service import MonitorService
    import adapters
    site = db.site('site2'); site.update(url=URL, adapter='css_catalog', enabled=True, permission_confirmed=True,
                                         adapter_config=CONFIG); db.save_site(site)
    monkeypatch.setattr('time.sleep', lambda s: None)
    monkeypatch.setattr(adapters, 'collect_flaresolverr', lambda url, cfg: collect_flaresolverr(url, cfg, post=Solver(), sleep=lambda s: None))
    service = MonitorService(db, adapters={'css_catalog': lambda s: (_ for _ in ()).throw(AssertionError('docker path used'))})
    first = asyncio.run(service.run('site2'))
    assert first['status'] == 'SUCCESS' and first['events'] == []
    monkeypatch.setattr(adapters, 'collect_flaresolverr', lambda url, cfg: collect_flaresolverr(url, cfg, post=Solver([PAGES[URL]] + [CHALLENGE] * 3), sleep=lambda s: None))
    failed = asyncio.run(service.run('site2'))
    assert failed['status'] == 'FAILED' and db.site('site2')['enabled']  # no pause, no REMOVED event
    with db.connect() as c: assert c.execute("SELECT COUNT(*) FROM monitor_events WHERE site_id='site2'").fetchone()[0] == 0


def test_api_accepts_flaresolverr_mode():
    from monitor.web import SiteInput
    fields = dict(id='s', name='n', url=URL, adapter='css_catalog', interval_minutes=240, enabled=False,
                  permission_confirmed=False, policy_note='', adapter_config={'execution_mode': 'flaresolverr'},
                  event_rules=['NEW'], notify_enabled=False, webhook_env='')
    assert SiteInput(**fields).adapter_config['execution_mode'] == 'flaresolverr'
    with pytest.raises(Exception): SiteInput(**{**fields, 'adapter_config': {'execution_mode': 'bogus'}})


def test_admin_dialog_keeps_execution_mode():
    js = open('admin/app.js', encoding='utf-8').read(); html = open('admin/index.html', encoding='utf-8').read()
    assert '<option value="flaresolverr">' in html
    assert "['execution_mode',s.adapter_config.execution_mode||'docker']" in js  # unknown values are added, not reset
