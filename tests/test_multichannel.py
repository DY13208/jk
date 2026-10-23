"""Multiple notification channels per site: migration, fan-out, independent failure/retry, health, API."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from monitor import agentmail
from monitor.business_health import business_health
from monitor.database import Database, EVENTS
from monitor.notifications import Notifier
from monitor.service import MonitorService
from monitor.web import create_app
from tests.test_monitor import db, enable, document, product  # noqa: F401

WECOM = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test'


def outbox(db):
    with db.connect() as c:
        return [dict(r) for r in c.execute('SELECT id,event_id,channel,status,attempts,last_error FROM monitor_outbox ORDER BY id')]


def multi(db, channels, site_id='lecomte'):
    site = enable(db, site_id, notify=True)
    site['notification_channels'] = channels
    db.save_site(site)
    return db.site(site_id)


def emit(db, site_id='lecomte', n=1):
    db.commit(site_id, document([product(i) for i in range(3)]))
    return db.commit(site_id, document([product(i) for i in range(3 + n)]))[1]


class Sender:
    """Mock HTTP sender: records calls; per-host behaviour can be switched."""
    def __init__(self): self.calls, self.fail = [], set()
    async def __call__(self, url, payload, headers=None):
        self.calls.append(url)
        if any(h in url for h in self.fail): raise RuntimeError('boom')
        body = {'code': 200} if 'day.app' in url else {'id': 'x'} if 'ntfy' in url else {'errcode': 0}
        return SimpleNamespace(status_code=200, json=lambda: body, headers={})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv('WECOM_TEST_WEBHOOK', WECOM)
    monkeypatch.setenv('BARK_KEY', 'BarkTestKey123456')
    monkeypatch.setenv('NTFY_TOPIC', 'ntfy-topic-for-tests-0123456789')
    monkeypatch.setenv('SMTP_HOST', 'smtp.test'); monkeypatch.setenv('SMTP_FROM', 'bot@test.example')


# ---- migration ---------------------------------------------------------------------------------------
def legacy_db(path):
    """A database as the previous release left it: single channel column, outbox UNIQUE(event_id), no channel."""
    old = Database(path); old.migrate()
    with sqlite3.connect(path) as c:
        c.execute("UPDATE monitor_sites SET notification_channel='email',email_to='a@test.example',notify_enabled=1,notification_channels=''")
        c.execute("INSERT INTO monitor_events(id,site_id,run_id,product_id,kind,at,payload) VALUES(1,'lecomte',NULL,'p','NEW','t','{}')")
        c.executescript('''CREATE TABLE ob(id INTEGER PRIMARY KEY,event_id INTEGER UNIQUE REFERENCES monitor_events(id),
            site_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
            next_at REAL NOT NULL DEFAULT 0,claimed_at REAL,last_error TEXT NOT NULL DEFAULT '',sent_at TEXT,delivery_snapshot TEXT);
            DROP TABLE monitor_outbox; ALTER TABLE ob RENAME TO monitor_outbox;''')
        c.execute("INSERT INTO monitor_outbox(event_id,site_id,status) VALUES(1,'lecomte','held')")
    return old


def test_migration_turns_single_channel_into_one_item_list(tmp_path):
    db = legacy_db(tmp_path/'old.db'); db.migrate()
    site = db.site('lecomte')
    assert site['notification_channels'] == [{'channel': 'email', 'env': '', 'to': 'a@test.example'}]
    assert site['notification_channel'] == 'email' and site['email_to'] == 'a@test.example'   # legacy fields intact
    rows = outbox(db)
    assert rows == [{'id': 1, 'event_id': 1, 'channel': 'email', 'status': 'held', 'attempts': 0, 'last_error': ''}]
    db.migrate()                                                                              # idempotent
    assert outbox(db) == rows and db.site('lecomte')['notification_channels'] == site['notification_channels']


def test_old_fields_are_fallback_when_list_is_empty(db):
    with db.connect() as c: c.execute("UPDATE monitor_sites SET notification_channel='bark',webhook_env='BARK_KEY',notification_channels='' WHERE id='lecomte'")
    assert db.site('lecomte')['notification_channels'] == [{'channel': 'bark', 'env': 'BARK_KEY', 'to': ''}]


def test_import_sites_seed_accepts_both_formats(tmp_path):
    seed = tmp_path/'sites.json'
    seed.write_text(json.dumps({'sites': [
        {'id': 'a1', 'name': 'A', 'url': 'https://a.test/', 'adapter': 'css_http', 'notification_channel': 'ntfy', 'webhook_env': 'NTFY_TOPIC'},
        {'id': 'b1', 'name': 'B', 'url': 'https://b.test/', 'adapter': 'css_http',
         'notification_channels': [{'channel': 'email', 'to': 'x@test.example'}, {'channel': 'bark', 'env': 'BARK_KEY'}]}]}), encoding='utf-8')
    db = Database(tmp_path/'s.db'); db.migrate()
    from monitor.database import import_sites
    with db.connect() as c: import_sites(c, seed)
    assert db.site('a1')['notification_channels'] == [{'channel': 'ntfy', 'env': 'NTFY_TOPIC', 'to': ''}]
    b = db.site('b1')
    assert [e['channel'] for e in b['notification_channels']] == ['email', 'bark']
    assert b['notification_channel'] == 'email' and b['email_to'] == 'x@test.example'          # primary mirrored


# ---- fan-out and independence -------------------------------------------------------------------------
def test_single_channel_site_queues_exactly_one_row_per_event(db, env):
    enable(db, notify=True)
    events = emit(db, n=2)
    rows = outbox(db)
    assert len(rows) == 2 and {r['channel'] for r in rows} == {'wecom'}
    assert sorted(r['event_id'] for r in rows) == sorted(e['id'] for e in events)


def test_fan_out_one_row_per_event_per_channel_deduped(db, env):
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}, {'channel': 'bark', 'env': 'BARK_KEY'},
               {'channel': 'bark', 'env': 'BARK_KEY'}])                                         # duplicate entry collapses
    emit(db, n=1)
    assert sorted(r['channel'] for r in outbox(db)) == ['bark', 'wecom']
    with db.connect() as c, pytest.raises(sqlite3.IntegrityError):
        r = outbox(db)[0]
        c.execute('INSERT INTO monitor_outbox(event_id,site_id,channel) VALUES(?,?,?)', (r['event_id'], 'lecomte', r['channel']))


def test_one_channel_failing_does_not_block_or_duplicate_others(db, env):
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}, {'channel': 'bark', 'env': 'BARK_KEY'},
               {'channel': 'ntfy', 'env': 'NTFY_TOPIC'}])
    emit(db, n=1)
    sender = Sender(); sender.fail.add('day.app')
    asyncio.run(Notifier(db, sender).dispatch())
    state = {r['channel']: r['status'] for r in outbox(db)}
    assert state == {'wecom': 'sent', 'bark': 'uncertain', 'ntfy': 'sent'}
    assert sum('qyapi' in u for u in sender.calls) == 1 and sum('ntfy' in u for u in sender.calls) == 1
    asyncio.run(Notifier(db, sender).dispatch())                                               # nothing re-sent
    assert len(sender.calls) == 3


def test_missing_config_on_one_channel_holds_only_that_channel(db, env, monkeypatch):
    monkeypatch.delenv('BARK_KEY')
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}, {'channel': 'bark', 'env': 'BARK_KEY'}])
    emit(db, n=1)
    asyncio.run(Notifier(db, Sender()).dispatch())
    assert {r['channel']: r['status'] for r in outbox(db)} == {'wecom': 'sent', 'bark': 'held'}


def test_retry_is_per_channel(db, env):
    import httpx
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}, {'channel': 'ntfy', 'env': 'NTFY_TOPIC'}])
    emit(db, n=1)
    async def sender(url, payload, headers=None):
        if 'ntfy' in url: raise httpx.ConnectError('down')
        return SimpleNamespace(status_code=200, json=lambda: {'errcode': 0}, headers={})
    asyncio.run(Notifier(db, sender).dispatch())
    rows = {r['channel']: r for r in outbox(db)}
    assert rows['wecom']['status'] == 'sent' and rows['ntfy']['status'] == 'pending' and rows['ntfy']['attempts'] == 1


def test_push_batch_and_email_digest_stay_within_their_channel(db, env):
    sent = []
    multi(db, [{'channel': 'email', 'to': 'r@test.example'}, {'channel': 'bark', 'env': 'BARK_KEY'}])
    emit(db, n=3)
    sender = Sender()
    asyncio.run(Notifier(db, sender, email_sender=lambda c, m: sent.append(str(m['Subject'])) or {}).dispatch())
    rows = outbox(db)
    assert len(rows) == 6 and all(r['status'] == 'sent' for r in rows)
    assert len(sent) == 1 and sum('day.app' in u for u in sender.calls) == 1                     # one digest each


def test_removed_channel_rows_are_held_not_sent_elsewhere(db, env):
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}, {'channel': 'bark', 'env': 'BARK_KEY'}])
    emit(db, n=1)
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}])
    sender = Sender(); asyncio.run(Notifier(db, sender).dispatch())
    assert {r['channel']: r['status'] for r in outbox(db)} == {'wecom': 'sent', 'bark': 'held'}
    assert not any('day.app' in u for u in sender.calls)


def test_agentmail_worker_claims_only_its_channel_rows(db, env):
    multi(db, [{'channel': 'agentmail', 'to': 'q@test.example'}, {'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}])
    emit(db, n=1)
    asyncio.run(Notifier(db, Sender()).dispatch())
    assert {r['channel']: r['status'] for r in outbox(db)} == {'agentmail': 'pending', 'wecom': 'sent'}
    job = agentmail.claim(db)
    assert job and job['to'] == ['q@test.example']
    assert agentmail.finish(db, job['job'], {'returncode': 0, 'ok': True, 'queued': True})['status'] == 'sent'
    assert agentmail.claim(db) is None
    assert {r['channel']: r['status'] for r in outbox(db)} == {'agentmail': 'sent', 'wecom': 'sent'}


# ---- health / channels --------------------------------------------------------------------------------
def test_health_checks_every_selected_channel(db, env, monkeypatch):
    monkeypatch.delenv('NTFY_TOPIC')
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}, {'channel': 'ntfy', 'env': 'NTFY_TOPIC'}])
    reasons = next(s for s in business_health(db, MonitorService(db))['sites'] if s['site_id'] == 'lecomte')['reasons']
    assert any('ntfy' in r for r in reasons) and not any('企业微信' in r for r in reasons)


def test_channels_overview_lists_site_under_each_channel(db, env):
    from monitor import channels
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}, {'channel': 'bark', 'env': 'BARK_KEY'}])
    items = {c['id']: c for c in channels.overview(db)}
    assert 'lecomte' in [s['id'] for s in items['bark']['sites']] and 'lecomte' in [s['id'] for s in items['wecom']['sites']]
    assert 'WECOM_TEST_WEBHOOK' in channels.env_names(db, 'wecom')


# ---- API ----------------------------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch, env):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    with TestClient(create_app(tmp_path/'api.db', scheduled=False)) as c:
        c.sender = Sender(); c.app.state.push_sender = c.sender
        yield c


def body(**extra):
    return {'name': 'L', 'url': 'https://shop.test/', 'adapter': 'css_http', 'notify_enabled': True, **extra}


def test_api_accepts_old_single_channel_field(client):
    r = client.put('/api/sites/old1', json=body(id='old1', notification_channel='bark', webhook_env='BARK_KEY'))
    assert r.status_code == 200, r.text
    site = next(s for s in client.get('/api/sites').json()['items'] if s['id'] == 'old1')
    assert site['notification_channels'] == [{'channel': 'bark', 'env': 'BARK_KEY', 'to': ''}]


def test_api_saves_multiple_channels_and_validates_each(client):
    bad = client.put('/api/sites/m1', json=body(id='m1', notification_channels=[{'channel': 'email'}, {'channel': 'bark', 'env': 'BARK_KEY'}]))
    assert bad.status_code == 422                                                               # email needs a recipient
    bad = client.put('/api/sites/m1', json=body(id='m1', notification_channels=[{'channel': 'bark', 'env': 'bad name'}]))
    assert bad.status_code == 422
    ok = client.put('/api/sites/m1', json=body(id='m1', notification_channels=[{'channel': 'email', 'to': 'r@test.example'}, {'channel': 'ntfy', 'env': 'NTFY_TOPIC'}]))
    assert ok.status_code == 200, ok.text
    site = next(s for s in client.get('/api/sites').json()['items'] if s['id'] == 'm1')
    assert [e['channel'] for e in site['notification_channels']] == ['email', 'ntfy']
    assert site['notification_channel'] == 'email'
    assert 'ntfy-topic-for-tests' not in client.get('/api/sites').text                           # never a secret value


def test_api_push_test_picks_selected_channel(client):
    client.put('/api/sites/m2', json=body(id='m2', notification_channels=[{'channel': 'email', 'to': 'r@test.example'},
                                                                 {'channel': 'bark', 'env': 'BARK_KEY'}, {'channel': 'ntfy', 'env': 'NTFY_TOPIC'}]))
    r = client.post('/api/sites/m2/push-test', json={'confirm': True})                          # first Bark/ntfy by default
    assert r.status_code == 200 and 'day.app' in client.sender.calls[-1]
    r = client.post('/api/sites/m2/push-test', json={'confirm': True, 'channel': 'ntfy'})
    assert r.status_code == 200 and 'ntfy' in client.sender.calls[-1]
    assert client.post('/api/sites/m2/push-test', json={'confirm': True, 'channel': 'wecom'}).status_code == 409


def test_delivery_history_filters_by_row_channel(db, env, tmp_path):
    from monitor.notification_history import listing
    multi(db, [{'channel': 'wecom', 'env': 'WECOM_TEST_WEBHOOK'}, {'channel': 'bark', 'env': 'BARK_KEY'}])
    emit(db, n=1)
    assert listing(db, channel='bark')['total'] == 1 and listing(db, channel='wecom')['total'] == 1
    assert listing(db, channel='bark')['items'][0]['channel'] == 'bark'


def test_old_client_editing_legacy_fields_keeps_other_channels(client):
    ok = client.put('/api/sites/m1', json=body(id='m1', notification_channels=[{'channel': 'email', 'to': 'r@test.example'}, {'channel': 'ntfy', 'env': 'NTFY_TOPIC'}]))
    assert ok.status_code == 200, ok.text
    site = ok.json()
    site.update(notification_channel='email', email_to='new@test.example')            # old client: legacy field only
    assert client.put('/api/sites/m1', json=site).status_code == 200
    site = client.get('/api/sites').json()['items']
    site = next(s for s in site if s['id'] == 'm1')
    assert site['notification_channels'] == [{'channel': 'email', 'env': '', 'to': 'new@test.example'},
                                             {'channel': 'ntfy', 'env': 'NTFY_TOPIC', 'to': ''}]
