import asyncio
import json
from fastapi.testclient import TestClient
from monitor.web import create_app
from monitor.notifications import Notifier
from monitor import agentmail, weixin
from tests.test_monitor import product, document


def prepare(client,channel='email'):
    db=client.app.state.db
    site=db.site('lecomte');site.update(notification_channel=channel,email_to='first@example.test',notify_enabled=True)
    db.save_site(site);db.commit('lecomte',document([product(1)]));db.commit('lecomte',document([product(1),product(2)]))
    return db,site,db.history('monitor_outbox')['items'][0]['id']


def test_email_snapshot_is_actual_and_survives_config_change(tmp_path,monkeypatch):
    for key,value in {'SMTP_HOST':'smtp.test','SMTP_FROM':'sender@example.test','SMTP_USERNAME':'user','SMTP_PASSWORD':'DO_NOT_EXPOSE'}.items(): monkeypatch.setenv(key,value)
    with TestClient(create_app(tmp_path/'db',scheduled=False)) as client:
        db,site,row_id=prepare(client)
        delivered=[]
        asyncio.run(Notifier(db,email_sender=lambda config,mail:delivered.append(mail) or {}).dispatch())
        site.update(email_to='changed@example.test',notification_channel='weixin',name='Renamed site');db.save_site(site)
        listing=client.get('/api/notifications').json()
        row=listing['items'][0]
        assert row['basis']=='captured' and row['channel']=='email' and row['recipient']=='first@example.test'
        detail=client.get(f'/api/notifications/{row_id}').json()
        assert detail['preview']['html']==delivered[0].get_body(preferencelist=('html',)).get_content()
        assert 'Renamed site' not in detail['preview']['html']
        assert detail['sender']=='sender@example.test'
        assert 'DO_NOT_EXPOSE' not in json.dumps(detail)
        preview=client.get(f'/api/notifications/{row_id}/preview')
        assert preview.status_code==200 and "default-src 'none'" in preview.headers['content-security-policy']
        assert 'sandbox' in preview.headers['content-security-policy']
        assert client.get('/api/notifications',params={'channel':'email','status':'sent'}).json()['total']==1
        assert client.get('/api/notifications',params={'channel':'weixin'}).json()['total']==0
        assert client.get('/api/notifications/99999').status_code==404
        assert client.get('/api/notifications/99999/preview').status_code==404


def test_legacy_is_not_mislabeled_as_current_recipient_and_pending_is_planned(tmp_path):
    with TestClient(create_app(tmp_path/'db',scheduled=False)) as client:
        db,site,row_id=prepare(client,'agentmail')
        row=client.get('/api/notifications').json()['items'][0]
        assert row['basis']=='planned' and row['recipient']=='first@example.test'
        with db.connect() as conn:
            conn.execute("UPDATE monitor_outbox SET status='sent',attempts=1 WHERE id=?",(row_id,))
        site.update(email_to='new@example.test',notification_channel='email');db.save_site(site)
        row=client.get(f'/api/notifications/{row_id}').json()
        assert row['basis']=='legacy' and row['channel']=='unknown'
        assert row['recipient']=='历史记录未留存接收人'
        assert row['preview']['source']=='reconstructed' and '商品 2' in row['preview']['text']
        assert row['current_configuration']['recipient_label']=='new@example.test'
        assert client.get('/api/notifications',params={'channel':'unknown','limit':1}).json()['total']==1
        assert client.get('/api/notifications',params={'site_id':'missing'}).json()['total']==0
        with db.connect() as conn:
            conn.execute("UPDATE monitor_outbox SET last_error='Agent Mail 已接受' WHERE id=?",(row_id,))
        inferred=client.get('/api/notifications',params={'channel':'agentmail'}).json()
        assert inferred['total']==1 and inferred['items'][0]['recipient']=='历史记录未留存接收人'


def test_agentmail_snapshot_matches_job(tmp_path):
    with TestClient(create_app(tmp_path/'db',scheduled=False)) as client:
        db,site,row_id=prepare(client,'agentmail')
        job=agentmail.claim(db)
        site.update(email_to='new@example.test');db.save_site(site)
        detail=client.get(f'/api/notifications/{row_id}').json()
        assert detail['recipient']==', '.join(job['to'])
        assert detail['preview']['html']==job['html'] and detail['preview']['text']==job['text']
        assert detail['channel']=='agentmail'


def test_weixin_records_actual_recipient_without_tokens(tmp_path,monkeypatch):
    async def status(db): return {'ready':True,'recipient':'user-old'}
    async def request(db,path,body): return {'ok':True,'recipient':'user-actual'}
    monkeypatch.setattr(weixin,'status',status);monkeypatch.setattr(weixin,'request',request)
    with TestClient(create_app(tmp_path/'db',scheduled=False)) as client:
        db,_,row_id=prepare(client,'weixin')
        asyncio.run(Notifier(db).dispatch())
        detail=client.get(f'/api/notifications/{row_id}').json()
        assert detail['recipient']=='user-actual' and detail['channel']=='weixin'
        assert detail['basis']=='captured' and detail['status']=='sent'
