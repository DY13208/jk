import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from monitor.notifications import Notifier
from monitor.web import create_app
from tests.test_monitor import db, document, product

@pytest.fixture
def queued(db,monkeypatch):
    monkeypatch.setenv('PUSHPLUS_LECOMTE_TOKEN','TEST_ONLY_TOKEN')
    site=db.site('lecomte')
    site.update(notification_channel='pushplus',webhook_env='PUSHPLUS_LECOMTE_TOKEN',notify_enabled=True)
    db.save_site(site)
    db.commit('lecomte',document([product(1)]))
    assert db.history('monitor_outbox')['total']==0
    db.commit('lecomte',document([product(1),product(2)]))
    return db


def test_pushplus_contents_and_dedup(queued):
    calls=[]
    async def send(url,payload):
        calls.append(payload)
        assert url=='https://www.pushplus.plus/send'
        assert payload['channel']=='wechat' and payload['template']=='txt'
        assert all(x in payload['content'] for x in ['商品 2','10.00','-50%','https://example.test/p-2'])
        return httpx.Response(200,json={'code':200})
    notifier=Notifier(queued,sender=send)
    asyncio.run(notifier.dispatch());asyncio.run(notifier.dispatch())
    assert len(calls)==1
    row=queued.history('monitor_outbox')['items'][0]
    assert row['status']=='sent' and '非微信送达确认' in row['last_error']

@pytest.mark.parametrize('scenario,expected',[('rejected','held'),('rate','pending'),('timeout','uncertain'),('invalid','uncertain'),('connect','pending')])
def test_pushplus_errors(queued,scenario,expected):
    async def send(*args):
        if scenario=='timeout': raise httpx.ReadTimeout('TEST_ONLY_TOKEN')
        if scenario=='connect': raise httpx.ConnectError('TEST_ONLY_TOKEN')
        if scenario=='invalid': return httpx.Response(200,text='TEST_ONLY_TOKEN')
        return httpx.Response(429 if scenario=='rate' else 200,json={'code':600,'msg':'TEST_ONLY_TOKEN'})
    asyncio.run(Notifier(queued,sender=send).dispatch())
    row=queued.history('monitor_outbox')['items'][0]
    assert row['status']==expected
    assert 'TEST_ONLY_TOKEN' not in row['last_error']


def test_pushplus_missing_token_and_api(queued,monkeypatch):
    monkeypatch.delenv('PUSHPLUS_LECOMTE_TOKEN')
    async def send(*args): raise AssertionError('must not send')
    asyncio.run(Notifier(queued,sender=send).dispatch())
    assert queued.history('monitor_outbox')['items'][0]['status']=='held'
    with TestClient(create_app(queued.path,scheduled=False)) as client:
        site=client.get('/api/sites').json()['items'][0]
        assert not site['pushplus_configured']
        assert client.put('/api/sites/'+site['id'],json=site).status_code==200
        assert 'TEST_ONLY_TOKEN' not in client.get('/api/sites').text


def test_pushplus_spacing_and_no_failure_alert(queued):
    queued.commit('lecomte',document([product(1),product(2),product(3)]))
    calls=[]
    async def send(*args): calls.append(1);return httpx.Response(200,json={'code':200})
    asyncio.run(Notifier(queued,sender=send).dispatch())
    assert len(calls)==1
    assert sorted(x['status'] for x in queued.history('monitor_outbox')['items'])==['pending','sent']
    before=queued.history('monitor_outbox')['total']
    queued.pause('lecomte','HTTP 403',queued.log_start('lecomte'))
    assert queued.history('monitor_outbox')['total']==before
