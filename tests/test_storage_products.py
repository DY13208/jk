import os
from collections import namedtuple

from monitor.storage import storage_status
from tests.test_monitor import db, product, document


def test_product_paging_preserves_unicode_literal_search_and_latest_snapshot(db):
    items = [dict(product(i), name=name) for i, name in enumerate(['Straße', '中文商品', '100%_折扣', '普通'])]
    db.commit('lecomte', document(items))
    assert db.product_page('lecomte', 'STRASSE')['total'] == 1
    assert db.product_page('lecomte', '%_')['items'][0]['name'] == '100%_折扣'
    assert db.product_page('lecomte', '中文')['total'] == 1
    page = db.product_page('lecomte', limit=2, offset=1)
    assert page['total'] == 4
    assert [p['product_id'] for p in page['items']] == ['1','2']
    assert db.product_page('lecomte', offset=100)['items'] == []
    assert db.product_page('missing') == {'items':[], 'total':0}
    db.commit('lecomte', document([product(8)]))
    assert db.product_page('lecomte')['total'] == 1
    assert db.product_counts()['lecomte'] == 1
    assert db.product_counts()['site2'] == 0


def test_overview_never_hydrates_product_payloads(db, monkeypatch):
    db.commit('lecomte', document([product(1), product(2)]))
    def forbidden(*args):
        raise AssertionError('Full product loading is forbidden here')
    monkeypatch.setattr(db, 'products', forbidden)
    assert db.overview()['product_counts']['lecomte'] == 2


def test_storage_capacity_warning_and_preservation(db, monkeypatch):
    from monitor import storage
    folder = db.path.parent/'backups'
    folder.mkdir(exist_ok=True)
    backup = folder/'manual-test.db'
    backup.write_bytes(b'preserve')
    os.utime(backup, (1,1))
    Usage = namedtuple('Usage','total used free')
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda path:Usage(100*1024**3, 99*1024**3, 1024**3))
    report = storage_status(db)
    assert report['backup_count'] >= 1
    assert report['other_backup_count'] >= 1
    assert len(report['warnings']) == 2
    assert report['database_bytes'] == db.path.stat().st_size
    assert backup.read_bytes() == b'preserve'
    assert not report['retention']['automatic_history_cleanup']
