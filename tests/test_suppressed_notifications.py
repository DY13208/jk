import asyncio
import json

from fastapi.testclient import TestClient

from monitor.notifications import Notifier
from monitor import agentmail
from monitor.web import create_app
from tests.test_monitor import db, enable


def excluded(db, status='held'):
    with db.connect() as conn:
        event = conn.execute("INSERT INTO monitor_events(site_id,product_id,kind,at,payload) VALUES('lecomte','','ACCESS_DENIED','2026-10-10','{}')").lastrowid
        row = conn.execute('INSERT INTO monitor_outbox(event_id,site_id,status,last_error) VALUES(?,?,?,?)',
                           (event,'lecomte',status,'historical configuration failure')).lastrowid
    return row


def test_migration_preserves_excluded_history_and_uncertain(db):
    row = excluded(db)
    uncertain = excluded(db, 'uncertain')
    db.migrate()
    with db.connect() as conn:
        result = dict(conn.execute('SELECT * FROM monitor_outbox WHERE id=?',(row,)).fetchone())
        assert result['status'] == 'suppressed'
        assert 'historical configuration failure' in result['last_error']
        assert conn.execute('SELECT status FROM monitor_outbox WHERE id=?',(uncertain,)).fetchone()[0]=='uncertain'
    db.migrate()
    with db.connect() as conn:
        assert dict(conn.execute('SELECT * FROM monitor_outbox WHERE id=?',(row,)).fetchone()) == result
    assert db.overview()['attention_notifications'] == 1


def test_excluded_retry_rejected_and_history_filter_works(tmp_path):
    with TestClient(create_app(tmp_path/'db',scheduled=False)) as client:
        db=client.app.state.db
        row=excluded(db)
        assert client.post(f'/api/notifications/{row}/retry').status_code == 409
        db.migrate()
        response=client.get('/api/notifications?status=suppressed')
        assert response.status_code == 200
        assert response.json()['total'] == 1
        assert client.post(f'/api/notifications/{row}/retry').status_code == 409


def test_dispatch_excludes_without_sending(db):
    enable(db, notify=True)
    excluded(db, 'pending')
    async def forbidden(*args,**kwargs): raise AssertionError('Must not send')
    asyncio.run(Notifier(db,sender=forbidden).dispatch())
    assert db.history('monitor_outbox')['items'][0]['status'] == 'suppressed'


def test_agentmail_excludes_without_claiming(db):
    site=enable(db, notify=True)
    site.update(notification_channel='agentmail',email_to='test@example.test')
    db.save_site(site)
    excluded(db, 'pending')
    assert agentmail.claim(db) is None
    assert db.history('monitor_outbox')['items'][0]['status'] == 'suppressed'
