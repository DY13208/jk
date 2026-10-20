"""Unified 通知设置: channel overview (presence only, never secrets) and site-independent test sends (mocked)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from monitor.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    for name in ('BARK_KEY', 'NTFY_TOPIC', 'SMTP_HOST', 'SMTP_FROM', 'WECOM_LECOMTE_WEBHOOK', 'PUSHPLUS_LECOMTE_TOKEN'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path/'n.db', scheduled=False)) as c:
        c.sent = []
        async def sender(url, payload, headers=None):
            c.sent.append((url, payload, headers))
            body = {'code': 200} if 'day.app' in url or 'pushplus' in url else {'id': 'x'} if 'ntfy' in url else {'errcode': 0}
            return SimpleNamespace(status_code=200, json=lambda: body)
        c.app.state.push_sender = sender
        c.app.state.email_sender = lambda config, mail: c.sent.append(('smtp', config['recipients'], str(mail['Subject']))) or {}
        yield c


def channel(client, cid): return next(x for x in client.get('/api/notify/channels').json()['channels'] if x['id'] == cid)


def test_lists_every_channel_with_env_names_and_apps(client):
    items = client.get('/api/notify/channels').json()['channels']
    assert {c['id'] for c in items} == {'email', 'bark', 'ntfy', 'wecom', 'weixin', 'pushplus', 'agentmail'}
    bark = next(c for c in items if c['id'] == 'bark')
    assert not bark['configured'] and 'BARK_KEY' in [e['name'] for e in bark['env']]
    assert any('apps.apple.com' in a[1] for a in bark['apps'])
    assert len(next(c for c in items if c['id'] == 'ntfy')['apps']) == 3


def test_presence_only_never_secret_values(client, monkeypatch):
    monkeypatch.setenv('BARK_KEY', 'TopSecretKey12345')
    text = client.get('/api/notify/channels').text
    assert 'TopSecretKey12345' not in text
    bark = channel(client, 'bark')
    assert bark['configured'] and next(e for e in bark['env'] if e['name'] == 'BARK_KEY')['set'] is True


def test_bark_test_send_without_site(client, monkeypatch):
    monkeypatch.setenv('BARK_KEY', 'TopSecretKey12345')
    assert client.post('/api/notify/channels/bark/test', json={}).status_code == 400          # needs confirm
    r = client.post('/api/notify/channels/bark/test', json={'confirm': True})
    assert r.status_code == 200 and r.json()['status'] == 'sent'
    url, payload, _ = client.sent[-1]
    assert url.startswith('https://api.day.app') and '测试' in payload['title']


def test_ntfy_and_wecom_and_pushplus_tests(client, monkeypatch):
    monkeypatch.setenv('NTFY_TOPIC', 'abcdefghijklmnopqrstuvwxyz')
    monkeypatch.setenv('WECOM_LECOMTE_WEBHOOK', 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k')
    monkeypatch.setenv('PUSHPLUS_LECOMTE_TOKEN', 'tok123')
    assert client.post('/api/notify/channels/ntfy/test', json={'confirm': True}).json()['status'] == 'sent'
    assert client.post('/api/notify/channels/wecom/test', json={'confirm': True, 'env': 'WECOM_LECOMTE_WEBHOOK'}).json()['status'] == 'sent'
    assert client.post('/api/notify/channels/pushplus/test', json={'confirm': True, 'env': 'PUSHPLUS_LECOMTE_TOKEN'}).json()['status'] == 'sent'


def test_test_send_rejects_arbitrary_env_and_reports_missing_config(client):
    assert client.post('/api/notify/channels/wecom/test', json={'confirm': True, 'env': 'PATH'}).status_code == 400
    assert client.post('/api/notify/channels/bark/test', json={'confirm': True, 'env': 'BARK_SERVER'}).status_code == 400
    r = client.post('/api/notify/channels/bark/test', json={'confirm': True})
    assert r.status_code == 409 and 'BARK_KEY' not in r.json()['detail'] or r.status_code == 409
    assert client.post('/api/notify/channels/nope/test', json={'confirm': True}).status_code == 404


def test_email_test_send(client, monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.test'); monkeypatch.setenv('SMTP_FROM', 'bot@example.test')
    assert channel(client, 'email')['configured']
    r = client.post('/api/notify/channels/email/test', json={'confirm': True, 'to': 'me@example.test'})
    assert r.json()['status'] == 'sent' and client.sent[-1][:2] == ('smtp', ['me@example.test'])
    assert client.post('/api/notify/channels/email/test', json={'confirm': True, 'to': 'bad'}).status_code == 409


def test_agentmail_test_is_queued(client):
    r = client.post('/api/notify/channels/agentmail/test', json={'confirm': True, 'to': 'me@example.test'})
    assert r.json()['status'] == 'queued'
    assert client.get('/api/agentmail/status').json()['tests'][0]['recipients'] == 'me@example.test'


def test_site_referenced_env_names_are_listed(client):
    site = client.get('/api/sites').json()['items'][0]
    site.update(notification_channel='pushplus', webhook_env='PUSHPLUS_MY_TOKEN')
    assert client.put('/api/sites/' + site['id'], json=site).status_code == 200
    assert 'PUSHPLUS_MY_TOKEN' in [e['name'] for e in channel(client, 'pushplus')['env']]


def test_admin_has_unified_page_and_site_dialog_links_to_it():
    js = open('admin/app.js', encoding='utf-8').read(); html = open('admin/index.html', encoding='utf-8').read()
    assert "api('notify/channels')" in js and 'data-chtest' in js
    assert 'apps.apple.com' not in html and 'push-apps' not in html   # app links moved out of the site dialog
    assert 'href="#notifications"' in html
