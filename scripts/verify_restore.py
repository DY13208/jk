"""Create and validate an isolated SQLite restore. Never starts collection or delivery.

Usage: python scripts/verify_restore.py --db data/snapshots.db
       python scripts/verify_restore.py --backup data/backups/manual-example.db
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path


def fingerprint(conn, table):
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
        digest.update(json.dumps(row, ensure_ascii=True, default=str, separators=(',', ':')).encode())
        digest.update(b'\n')
        count += 1
    return {'rows':count, 'sha256':digest.hexdigest()}


def verify_restore(source, archive_to=None):
    source = Path(source).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='jk-restore-') as directory:
        snapshot = Path(directory)/'snapshot.db'
        restored = Path(directory)/'restored.db'
        with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as live:
            with sqlite3.connect(snapshot) as destination:
                live.backup(destination)
        shutil.copy2(snapshot, restored)
        with sqlite3.connect(snapshot) as reference, sqlite3.connect(restored) as check:
            integrity = check.execute('PRAGMA integrity_check').fetchone()[0]
            violations = len(check.execute('PRAGMA foreign_key_check').fetchall())
            tables = [row[0] for row in reference.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            compared = {}
            for table in tables:
                expected, actual = fingerprint(reference, table), fingerprint(check, table)
                compared[table] = {'rows':actual['rows'], 'matches':expected==actual}
            report = {'ok':integrity=='ok' and violations==0 and all(v['matches'] for v in compared.values()),
                    'integrity':integrity,'foreign_key_violations':violations,'tables':compared,
                    'scope':'isolated SQLite restore; no workers or external delivery started'}
        if archive_to and report['ok']:
            target = Path(archive_to)
            # Exclusive create prevents overwriting the live database or an existing archive.
            with target.open('xb') as output, snapshot.open('rb') as data:
                shutil.copyfileobj(data, output)
            target.chmod(0o600)
            report['archive_created'] = True
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--db')
    group.add_argument('--backup')
    parser.add_argument('--archive-to', help='Optional new .db archive path; never overwrites an existing file')
    args = parser.parse_args()
    report = verify_restore(args.db or args.backup, args.archive_to)
    print(json.dumps(report, ensure_ascii=True))
    raise SystemExit(0 if report['ok'] else 1)
