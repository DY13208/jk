import asyncio
import smtplib
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from monitor.database import Database
from monitor.email_delivery import configuration, build_email, send_email, recipients
from monitor.notifications import Notifier, message
from monitor.web import create_app
from tests.test_monitor import product, document


@pytest.fixture
def mail_db(tmp_path, monkeypatch):
    for key, value in {'SMTP_HOST':'smtp.example.test','SMTP_FROM':'sender@example.test',
                       'SMTP_SECURITY':'ssl','SMTP_PORT':'465','SMTP_USERNAME':'test-user',
                       'SMTP_PASSWORD':'TEST_ONLY'}.items(): monkeypatch.setenv(key,value)
    db=Database(tmp_path/'mail.db');db.migrate()
    site=db.site('lecomte')
    site.update(notification_channel='email',email_to='one@example.test,two@example.test',notify_enabled=True)
    db.save_site(site)
    db.commit('lecomte',document([product(1)]))
    assert db.history('monitor_outbox')['total']==0
    db.commit('lecomte',document([product(1),product(2)]))
    return db


def test_email_contents_baseline_and_dedup(mail_db):
    delivered=[]
    def sender(config, mail): delivered.append((config,mail)); return {}
    notifier=Notifier(mail_db,email_sender=sender)
    asyncio.run(notifier.dispatch());asyncio.run(notifier.dispatch())
    assert len(delivered)==1
    config,mail=delivered[0]
    assert config['recipients']==['one@example.test','two@example.test']
    assert mail['Subject'].startswith('新品上架')
    assert all(v in mail.get_body(preferencelist=('plain',)).get_content() for v in ('商品 2','10.00','-50%','https://example.test/p-2'))
    assert mail['Message-ID']
    assert mail_db.history('monitor_outbox')['items'][0]['status']=='sent'


@pytest.mark.parametrize('exc,state',[
    (smtplib.SMTPAuthenticationError(535,b'bad secret'),'held'),
    (smtplib.SMTPDataError(451,b'try later'),'pending'),
    (smtplib.SMTPRecipientsRefused({'one@example.test':(550,b'unknown')}),'held'),
    (smtplib.SMTPRecipientsRefused({'one@example.test':(450,b'later')}),'pending'),
    (TimeoutError('TEST_SECRET'),'uncertain'),
])
def test_email_failures_preserve_queue_without_leaking_secrets(mail_db,exc,state):
    def sender(*args): raise exc
    asyncio.run(Notifier(mail_db,email_sender=sender).dispatch())
    row=mail_db.history('monitor_outbox')['items'][0]
    assert row['status']==state
    assert 'TEST_SECRET' not in row['last_error'] and 'bad secret' not in row['last_error']


def test_email_partial_acceptance_no_automatic_resend(mail_db):
    calls=[]
    def sender(*args): calls.append(1);return {'two@example.test':(550,b'unknown')}
    notifier=Notifier(mail_db,email_sender=sender)
    asyncio.run(notifier.dispatch());asyncio.run(notifier.dispatch())
    assert calls==[1]
    assert mail_db.history('monitor_outbox')['items'][0]['status']=='uncertain'


def test_missing_configuration_or_disabled_never_sends(mail_db,monkeypatch):
    monkeypatch.delenv('SMTP_HOST')
    sender=Mock()
    asyncio.run(Notifier(mail_db,email_sender=sender).dispatch())
    sender.assert_not_called()
    assert mail_db.history('monitor_outbox')['items'][0]['status']=='held'
    site=mail_db.site('lecomte');site['notify_enabled']=False;mail_db.save_site(site)
    with mail_db.connect() as conn: conn.execute("UPDATE monitor_outbox SET status='pending'")
    asyncio.run(Notifier(mail_db,email_sender=sender).dispatch());sender.assert_not_called()


@pytest.mark.parametrize('value',['bad','one@example.test\nBcc:bad@example.test','', 'a@example.test,'])
def test_recipient_validation(value):
    with pytest.raises(ValueError): recipients(value)


@pytest.mark.parametrize('mode',['ssl','starttls'])
def test_smtp_transport_uses_tls_auth_and_explicit_recipients(mail_db,monkeypatch,mode):
    monkeypatch.setenv('SMTP_SECURITY',mode)
    config=configuration(mail_db.site('lecomte'))
    event=mail_db.history('monitor_events')['items'][0]
    mail=build_email(mail_db.site('lecomte'),event,message(mail_db.site('lecomte'),event),config)
    client=Mock();client.send_message.return_value={}
    constructor=Mock(return_value=client)
    monkeypatch.setattr('monitor.email_delivery.smtplib.'+('SMTP_SSL' if mode=='ssl' else 'SMTP'),constructor)
    assert send_email(config,mail)=={}
    if mode=='starttls': client.starttls.assert_called_once();assert client.ehlo.call_count==2
    else: assert constructor.call_args.kwargs['context'].check_hostname
    client.login.assert_called_once_with('test-user','TEST_ONLY')
    client.send_message.assert_called_once_with(mail,from_addr='sender@example.test',to_addrs=config['recipients'])
    client.quit.assert_called_once()


def test_quit_failure_does_not_resend_accepted_email(mail_db,monkeypatch):
    config=configuration(mail_db.site('lecomte'));client=Mock()
    client.send_message.return_value={};client.quit.side_effect=TimeoutError()
    monkeypatch.setattr('monitor.email_delivery.smtplib.SMTP_SSL',Mock(return_value=client))
    assert send_email(config,Mock())=={}
    client.close.assert_called_once()


def test_additive_v1_migration_keeps_data_and_notification_settings(tmp_path):
    db=Database(tmp_path/'old.db');db.migrate()
    db.commit('lecomte',document([product(1)]))
    with db.connect() as conn:
        conn.execute("UPDATE monitor_sites SET webhook_env='WECOM_OLD',notify_enabled=0")
        conn.execute('ALTER TABLE monitor_sites DROP COLUMN notification_channel')
        conn.execute('ALTER TABLE monitor_sites DROP COLUMN email_to')
    db.migrate();db.migrate()
    assert db.site('lecomte')['notification_channel']=='wecom'
    assert db.site('lecomte')['webhook_env']=='WECOM_OLD'
    assert len(db.products('lecomte'))==1
    assert len(list((tmp_path/'backups').glob('before-migration-*.db')))==1


def test_admin_email_config_and_secrets_not_returned(tmp_path,monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD',raising=False)
    monkeypatch.setenv('SMTP_PASSWORD','NEVER_RETURN_ME')
    with TestClient(create_app(tmp_path/'api.db',scheduled=False)) as client:
        site=client.get('/api/sites').json()['items'][0]
        site.update(notification_channel='email',email_to='one@example.test',notify_enabled=True)
        response=client.put('/api/sites/lecomte',json=site)
        assert response.status_code==200 and response.json()['email_to']=='one@example.test'
        assert 'NEVER_RETURN_ME' not in response.text
        site['email_to']='';assert client.put('/api/sites/lecomte',json=site).status_code==422
