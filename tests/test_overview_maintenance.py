from datetime import datetime, timezone
import sqlite3

import pytest

from monitor.overview_trends import trends
from monitor.notification_history import diagnostics
from monitor.storage import sample_storage, storage_status
from scripts.verify_restore import verify_restore
from tests.test_monitor import db, product, document, enable


def test_trends_not_capped_and_timezone_boundary(db):
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with db.connect() as conn:
        conn.executemany("INSERT INTO monitor_logs(site_id,started,status,run_id) VALUES('lecomte',?,'SUCCESS',NULL)",
                         [(today+'T00:00:00+00:00',)]*2100)
        conn.execute("INSERT INTO monitor_events(site_id,product_id,kind,at,payload) VALUES('lecomte','1','NEW',?,'{}')",
                     (today+'T23:30:00+00:00',))
    result = trends(db, 90, -480)
    assert sum(result['chg'].values()) == 1
    assert sum(result['snap'].values()) == 0  # A log without a complete run isn't a snapshot.
    assert len(result['latest']) == 1
    assert result['last_success'] is not None
    db.commit('lecomte', document([product(1)]))
    with db.connect() as conn:
        rid = db.latest_id(conn, 'lecomte')
        conn.execute("UPDATE monitor_logs SET run_id=?", (rid,))
    assert sum(trends(db, 90, -480)['snap'].values()) == 2100


def test_samples_need_a_day_and_are_idempotent(db):
    sample_storage(db)
    sample_storage(db)
    assert storage_status(db)['growth']['sample_count'] == 1
    assert storage_status(db)['growth']['bytes_per_day'] is None
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM monitor_storage_samples').fetchone()
        conn.execute('INSERT INTO monitor_storage_samples VALUES(?,?,?,?,?)',
                     (row['hour']-24, 0, 0, 0, row['free_bytes']))
    assert storage_status(db)['growth']['bytes_per_day'] > 0


def test_notification_diagnosis_does_not_mutate_or_retry(db):
    enable(db, notify=True)
    db.commit('lecomte', document([product(1)]))
    db.commit('lecomte', document([product(1),product(2)]))
    with db.connect() as conn:
        conn.execute("UPDATE monitor_outbox SET status='uncertain',last_error='Agent Mail CLI 超时'")
    before = db.history('monitor_outbox')
    assert diagnostics(db)['groups'][0]['count'] == 1
    assert diagnostics(db)['automatic_retry'] is False
    assert db.history('monitor_outbox') == before


def test_isolated_restore_and_archive_preserve_source(db, tmp_path):
    db.commit('lecomte', document([product(1)]))
    target = tmp_path/'archive.db'
    report = verify_restore(db.path, target)
    assert report['ok']
    assert report['tables']['monitor_details'] == {'rows':1,'matches':True}
    assert verify_restore(target)['ok']
    with pytest.raises(FileExistsError): verify_restore(db.path, target)
    assert len(db.products('lecomte')) == 1
    with sqlite3.connect(target) as conn:
        assert conn.execute('SELECT COUNT(*) FROM monitor_details').fetchone()[0] == 1
