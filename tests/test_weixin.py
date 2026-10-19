import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from monitor import weixin
from monitor import product_images
from monitor.notifications import Notifier
from monitor.web import create_app
from tests.test_monitor import db, document, product


@pytest.fixture
def queued(db):
    site=db.site('lecomte');site.update(notification_channel='weixin',notify_enabled=True)
    db.save_site(site)
    db.commit('lecomte',document([product(1)]))
    assert db.history('monitor_outbox')['total']==0
    db.commit('lecomte',document([product(1),product(2)]))
    return db


def test_weixin_contents_dedup_and_no_failure_alert(queued,monkeypatch):
    calls=[]
    async def state(db):return {'ready':True}
    async def send(db,path,body=None):calls.append(body);return {'ok':True}
    monkeypatch.setattr(weixin,'status',state);monkeypatch.setattr(weixin,'request',send)
    service=Notifier(queued)
    asyncio.run(service.dispatch());asyncio.run(service.dispatch())
    assert len(calls)==1
    assert all(x in calls[0]['text'] for x in ['商品 2','10.00','-50%','https://example.test/p-2'])
    queued.pause('lecomte','HTTP 403',queued.log_start('lecomte'))
    assert queued.history('monitor_outbox')['total']==1
    assert queued.history('monitor_outbox')['items'][0]['status']=='sent'


def test_weixin_waits_for_activation_without_attempts(queued,monkeypatch):
    async def state(db):return {'ready':False}
    async def never(*args):raise AssertionError('must not send')
    monkeypatch.setattr(weixin,'status',state);monkeypatch.setattr(weixin,'request',never)
    asyncio.run(Notifier(queued).dispatch())
    row=queued.history('monitor_outbox')['items'][0]
    assert row['status']=='pending' and row['attempts']==0 and row['next_at']>0


@pytest.mark.parametrize('failure,expected',[('timeout','uncertain'),('busy','pending'),('rejected','uncertain')])
def test_weixin_ambiguous_send_not_repeated(queued,monkeypatch,failure,expected):
    async def state(db):return {'ready':True}
    async def send(*args):
        if failure=='timeout':raise httpx.ReadTimeout('PRIVATE_TOKEN')
        response=httpx.Response(409 if failure=='busy' else 502,request=httpx.Request('POST','http://local/send'))
        raise httpx.HTTPStatusError('PRIVATE_TOKEN',request=response.request,response=response)
    monkeypatch.setattr(weixin,'status',state);monkeypatch.setattr(weixin,'request',send)
    asyncio.run(Notifier(queued).dispatch())
    row=queued.history('monitor_outbox')['items'][0]
    assert row['status']==expected and 'PRIVATE_TOKEN' not in row['last_error']


def test_weixin_api_no_send_endpoint(queued,monkeypatch):
    calls=[]
    async def request(db,path,body=None):calls.append(path);return {'ok':True}
    monkeypatch.setattr(weixin,'request',request)
    with TestClient(create_app(queued.path,scheduled=False)) as client:
        assert client.post('/api/weixin/send',json={}).status_code==404
        assert client.post('/api/weixin/login',json={},headers={'Origin':'https://evil.test'}).status_code==403
        assert client.post('/api/weixin/verify',json={'code':'bad'}).status_code==400
        assert client.post('/api/weixin/login',json={}).status_code==200
        assert calls==['login']
        site=client.get('/api/sites').json()['items'][0]
        assert client.put('/api/sites/'+site['id'],json=site).status_code==200


@pytest.mark.parametrize('image_ok,provider_image',[ (True,'accepted'),(True,'uncertain'),(False,'none') ])
def test_optional_picture_and_partial_delivery_no_text_retry(queued,monkeypatch,image_ok,provider_image):
    with queued.connect() as conn:
        import json
        row=conn.execute("SELECT id,payload FROM monitor_events WHERE kind='NEW'").fetchone()
        payload=json.loads(row['payload']);payload['product']['image']='https://shop.example/product.jpg'
        conn.execute('UPDATE monitor_events SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    calls=[]
    async def state(db):return {'ready':True}
    async def picture(url):return {'mime':'image/jpeg','data':'test-fixture'} if image_ok else None
    async def send(db,path,body=None):calls.append(body);return {'ok':True,'image_status':provider_image}
    monkeypatch.setattr(weixin,'status',state);monkeypatch.setattr(weixin,'request',send)
    monkeypatch.setattr(product_images,'attachment',picture)
    service=Notifier(queued);asyncio.run(service.dispatch());asyncio.run(service.dispatch())
    assert len(calls)==1 and ('image' in calls[0])==image_ok
    row=queued.history('monitor_outbox')['items'][0]
    assert row['status']=='sent'
    if provider_image=='uncertain':assert '不自动重发文字' in row['last_error']
    if not image_ok:assert '降级为文字' in row['last_error']
