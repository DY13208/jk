import asyncio, re
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from monitor import push
from monitor.notifications import Notifier
from monitor.web import SiteInput, create_app
from tests.test_monitor import db, document, product

KEY = 'TESTONLYBARKKEY123'
TOPIC = 'test-only-topic-abcdefghijklmnop'


def ev(kind='NEW', i=1, old=None):
    p = {'name': f'Item {i}', 'sale_price': '10.00', 'currency': 'EUR', 'url': f'https://example.test/p-{i}', 'image': 'https://example.test/i.jpg'}
    return {'id': i, 'kind': kind, 'payload': {'product': p, **({'old_price': old} if old else {})}}


SITE = {'name': 'Shop', 'notification_channel': 'bark', 'webhook_env': 'BARK_KEY'}


def test_bark_payload_single_and_level(monkeypatch):
    monkeypatch.setenv('BARK_KEY', KEY)
    url, headers, extra = push.config(SITE)
    assert url == 'https://api.day.app/push' and headers is None
    body = push.payload('bark', push.build(SITE, [ev('PRICE_DROP', old='20.00')]), extra)
    assert body['device_key'] == KEY and body['group'] == '商品监控' and body['level'] == 'timeSensitive'
    assert body['url'] == 'https://example.test/p-1' and 'Item 1' in body['body']
    assert push.payload('bark', push.build(SITE, [ev('NEW')]), extra)['level'] == 'active'


def test_ntfy_payload_batch_truncates(monkeypatch):
    monkeypatch.setenv('NTFY_TOPIC', TOPIC); monkeypatch.setenv('NTFY_TOKEN', 'tk_test')
    site = {**SITE, 'notification_channel': 'ntfy', 'webhook_env': 'NTFY_TOPIC'}
    url, headers, extra = push.config(site)
    assert url == 'https://ntfy.sh' and headers == {'Authorization': 'Bearer tk_test'}
    body = push.payload('ntfy', push.build(site, [ev('NEW', i) for i in range(12)]), extra)
    assert body['topic'] == TOPIC and '12 项' in body['title'] and '另有 4 项' in body['message']
    assert 'click' not in body and body['priority'] == 3


@pytest.mark.parametrize('env,value', [('BARK_SERVER', 'http://bark.local'), ('BARK_SERVER', 'ftp://x'), ('BARK_KEY', 'bad key!')])
def test_validation(monkeypatch, env, value):
    monkeypatch.setenv('BARK_KEY', KEY); monkeypatch.setenv(env, value)
    with pytest.raises(ValueError) as exc: push.config(SITE)
    assert KEY not in str(exc.value)


def test_self_hosted_http_opt_in(monkeypatch):
    monkeypatch.setenv('BARK_KEY', KEY); monkeypatch.setenv('BARK_SERVER', 'http://bark.local:8080/'); monkeypatch.setenv('BARK_ALLOW_HTTP', '1')
    assert push.config(SITE)[0] == 'http://bark.local:8080/push'


def test_site_input_requires_env_name():
    base = dict(id='x', name='x', url='https://example.test', adapter='css_catalog', notify_enabled=True)
    with pytest.raises(ValueError): SiteInput(**base, notification_channel='ntfy')
    assert SiteInput(**base, notification_channel='bark', webhook_env='BARK_KEY').notification_channel == 'bark'


@pytest.fixture
def queued(db, monkeypatch):
    monkeypatch.setenv('NTFY_TOPIC', TOPIC)
    site = db.site('lecomte')
    site.update(notification_channel='ntfy', webhook_env='NTFY_TOPIC', notify_enabled=True)
    db.save_site(site)
    db.commit('lecomte', document([product(1)]))
    db.commit('lecomte', document([product(1), product(2), product(3)]))
    return db


def test_dispatch_batches_into_one_push(queued):
    calls = []
    async def send(url, payload, headers=None):
        calls.append((url, payload)); return httpx.Response(200, json={'id': 'abc'})
    asyncio.run(Notifier(queued, sender=send).dispatch())
    assert len(calls) == 1 and calls[0][0] == 'https://ntfy.sh' and calls[0][1]['topic'] == TOPIC
    rows = queued.history('monitor_outbox')['items']
    assert len(rows) == 2 and all(r['status'] == 'sent' for r in rows)


@pytest.mark.parametrize('code,expected', [(429, 'pending'), (403, 'held'), (500, 'pending')])
def test_dispatch_errors(queued, code, expected):
    async def send(url, payload, headers=None): return httpx.Response(code, text=TOPIC)
    asyncio.run(Notifier(queued, sender=send).dispatch())
    rows = queued.history('monitor_outbox')['items']
    assert {r['status'] for r in rows} == {expected} and all(TOPIC not in (r['last_error'] or '') for r in rows)


def test_ui_has_channels_and_preserves_unknown_values():
    js = Path('admin/app.js').read_text(encoding='utf-8'); html = Path('admin/index.html').read_text(encoding='utf-8')
    assert "['bark','Bark" in js and "['ntfy','ntfy" in js and 'id="channel-list"' in html   # multi-select checkboxes
    assert 'data-chpick' in js and 'collectChannels()' in js and 'sel.add(new Option(v,v))' in js
    assert 'data-pushtest' in js and '随机' in js


def test_chinese_payload_sent_as_utf8_json(monkeypatch):
    monkeypatch.setenv('BARK_KEY', KEY)
    url, headers, extra = push.config(SITE)
    note = push.build(SITE, [ev('NEW')]); note['title'] = '商品监控测试推送'; note['body'] = '价格下降：始祖鸟 Atom 夹克'
    data = push.payload('bark', note, extra)
    body, h = push.encode(data, headers)
    assert h['Content-Type'] == 'application/json; charset=utf-8'
    assert '商品监控测试推送'.encode('utf-8') in body and b'\\u' not in body
    import json as _json
    assert _json.loads(body.decode('utf-8'))['body'] == '价格下降：始祖鸟 Atom 夹克'
    seen = {}
    def handler(request):
        seen['ct'] = request.headers['content-type']; seen['body'] = request.content
        return httpx.Response(200, json={'code': 200})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = asyncio.run(Notifier.send(url, data))
    assert r.status_code == 200 and seen['ct'] == 'application/json; charset=utf-8'
    assert _json.loads(seen['body'].decode('utf-8'))['title'] == '商品监控测试推送'
    ntfy = push.encode(push.payload('ntfy', note, {'topic': TOPIC}))[0]
    assert _json.loads(ntfy.decode('utf-8'))['title'] == '商品监控测试推送'


def test_tap_url_single_digest_and_site_fallback(monkeypatch):
    monkeypatch.setenv('BARK_KEY', KEY); monkeypatch.setenv('NTFY_TOPIC', TOPIC)
    site = {**SITE, 'name': '始祖鸟店', 'url': 'https://shop.example.test/list'}
    _, _, extra = push.config(site)
    one = push.payload('bark', push.build(site, [ev('NEW', 1)]), extra, site['name'])
    assert one['url'] == 'https://example.test/p-1' and one['group'] == '始祖鸟店'
    same = push.build(site, [ev('NEW', 1), ev('PRICE_DROP', 1, old='20.00')])
    assert same['url'] == 'https://example.test/p-1'
    many = push.build(site, [ev('NEW', i) for i in range(3)])
    assert many['url'] == 'https://shop.example.test/list'
    ntfy = push.payload('ntfy', many, {'topic': TOPIC})
    assert ntfy['click'] == 'https://shop.example.test/list'
    test = push.build(site, [{'id': 0, 'kind': 'TEST', 'payload': {}}])
    assert test['url'] == 'https://shop.example.test/list'
    bad = {**site, 'url': 'javascript:alert(1)'}
    e = ev('NEW', 1); e['payload']['product']['url'] = 'ftp://x.test/a'
    assert push.build(bad, [e])['url'] == '' and 'url' not in push.payload('bark', push.build(bad, [e]), extra)
