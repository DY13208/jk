import json
from pathlib import Path
from monitor.database import Database, import_sites

ROOT = Path(__file__).resolve().parents[1]


def test_seed_is_shareable_and_imports_without_overwriting(tmp_path, monkeypatch):
    seed = json.loads((ROOT / 'config' / 'sites.json').read_text(encoding='utf-8'))
    ids = {s['id'] for s in seed['sites']}
    assert {'lecomte', 'bergzeit_arcteryx', 'boardersla_arcteryx', 'niponino_arcteryx', 'thinkempire_arcteryx'} <= ids
    assert all(not s['notify_enabled'] and s['email_to'] == '' for s in seed['sites'])  # never email the owner
    assert '@' not in json.dumps(seed)
    monkeypatch.setenv('SITES_SEED', str(ROOT / 'config' / 'sites.json'))
    db = Database(tmp_path / 'snapshots.db'); db.migrate()
    lecomte = db.site('lecomte')
    assert lecomte['adapter_config'].get('execution_mode') == 'extension'  # seed wins over the built-in default
    assert db.site('thinkempire_arcteryx')['adapter'] == 'shopify_json'
    site = db.site('bergzeit_arcteryx'); site['interval_minutes'] = 99; db.save_site(site)
    with db.connect() as conn: assert import_sites(conn, ROOT / 'config' / 'sites.json') == []
    assert db.site('bergzeit_arcteryx')['interval_minutes'] == 99