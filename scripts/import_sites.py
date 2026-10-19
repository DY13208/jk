"""Import config/sites.json into the monitor database (existing sites are kept unless --overwrite).

Usage (host):      .venv\Scripts\python scripts\import_sites.py
Usage (Docker):    docker compose exec monitor python scripts/import_sites.py
Only site configuration is imported; snapshots and history are never part of the seed.
"""
import argparse, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from monitor.database import Database, import_sites  # noqa: E402

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', default=str(ROOT / 'config' / 'sites.json'))
    parser.add_argument('--db', default=os.getenv('DB_PATH', str(ROOT / 'data' / 'snapshots.db')))
    parser.add_argument('--overwrite', action='store_true', help='replace existing sites with the same id')
    args = parser.parse_args()
    db = Database(args.db); db.migrate()
    with db.connect() as conn: added = import_sites(conn, args.seed, overwrite=args.overwrite)
    print('imported:', ', '.join(added) or '(none - all sites already exist)')
    print('restart the monitor (docker compose restart monitor) so the scheduler picks up new sites.')