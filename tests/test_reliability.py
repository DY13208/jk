"""Regression coverage for administration and delivery isolation; no external sends."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from monitor.web import create_app
from monitor.notifications import Notifier
from crawl4ai_run.run_crawl4ai import CollectionError
from tests.test_monitor import db, document, product, enable


def test_missing_completeness_flag_cannot_replace_baseline(db):
    db.commit('lecomte',document([product(1),product(2)]))
    snapshot=document([product(1)])
    del snapshot['meta']['complete']
    with pytest.raises(CollectionError): db.commit('lecomte',snapshot)
    assert len(db.products('lecomte'))==2
    assert db.history('monitor_events')['total']==0


def test_edit_paused_site_preserves_reason_until_explicit_resume(db):
    db.pause('lecomte','HTTP 403',db.log_start('lecomte'))
    site=db.site('lecomte');site['name']='修改名称';db.save_site(site)
    assert db.site('lecomte')['paused_reason']=='HTTP 403'
    site.update(enabled=True,permission_confirmed=True);db.save_site(site)
    assert db.site('lecomte')['paused_reason']==''


def test_other_dispatcher_does_not_expire_agentmail_claim(db):
    site=enable(db,notify=True);site.update(notification_channel='agentmail',email_to='test@example.com');db.save_site(site)
    db.commit('lecomte',document([product(1)]))
    db.commit('lecomte',document([product(1),product(2)]))
    with db.connect() as c:
        c.execute("UPDATE monitor_outbox SET status='sending',claimed_at=?",(time.time()-90,))
    asyncio.run(Notifier(db).dispatch())
    assert db.history('monitor_outbox')['items'][0]['status']=='sending'
    with db.connect() as c: c.execute("UPDATE monitor_outbox SET status='held'")
    overview=db.overview()
    assert overview['pending_notifications']==0
    assert overview['attention_notifications']==1


def test_smtp_readiness_validates_actual_configuration(db,monkeypatch):
    monkeypatch.setenv('SMTP_HOST','smtp.example.com')
    monkeypatch.setenv('SMTP_FROM','sender@example.com')
    monkeypatch.setenv('SMTP_PORT','invalid')
    site=db.site('lecomte');site['email_to']='test@example.com';db.save_site(site)
    assert db.site('lecomte')['email_configured'] is False


def test_backups_download_and_path_isolation(tmp_path):
    with TestClient(create_app(tmp_path/'snapshots.db',scheduled=False)) as client:
        filename=client.post('/api/backups').json()['filename']
        assert filename in [x['filename'] for x in client.get('/api/backups').json()['items']]
        response=client.get('/api/backups/'+filename)
        assert response.status_code==200
        assert response.content.startswith(b'SQLite format 3')
        assert client.get('/api/backups/unknown.db').status_code==404
        assert client.get('/api/backups/%2e%2e%5csecret.db').status_code==404
