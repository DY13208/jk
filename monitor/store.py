"""Only complete, validated runs are committed; inventory and events share a transaction."""
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


class Incomplete(ValueError):
    pass


def validate(products, expected):
    if expected is None or len(products) != expected:
        raise Incomplete('Authoritative total does not match collected identities')
    ids = set()
    for p in products:
        key = p.get('id')
        if not key or key in ids:
            raise Incomplete('Missing or duplicate identity')
        ids.add(key)
        if not p.get('name') or not p.get('url') or not p.get('sku') or not p.get('stock') or p.get('price') is None:
            raise Incomplete('Missing required product fields')
        for field in ('price', 'original_price'):
            if p.get(field) is not None:
                try:
                    value = Decimal(str(p[field]))
                    if not value.is_finite() or value < 0:
                        raise InvalidOperation
                    p[field] = str(value.normalize())
                except InvalidOperation as exc:
                    raise Incomplete('Invalid price') from exc
        p['discount'] = (str(((1 - Decimal(p['price']) / Decimal(p['original_price'])) * 100).quantize(Decimal('.01')))
                         if p.get('original_price') and Decimal(p['original_price']) > 0 else None)


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots(
                    seq INTEGER PRIMARY KEY, site TEXT, at TEXT, products TEXT, evidence TEXT);
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY, site TEXT, at TEXT, kind TEXT, product TEXT, previous TEXT,
                    publish INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS failures(
                    seq INTEGER PRIMARY KEY, site TEXT, at TEXT, reason TEXT);
                CREATE INDEX IF NOT EXISTS snapshots_site ON snapshots(site, seq);
                CREATE INDEX IF NOT EXISTS events_site ON events(site, seq);
            ''')
            if 'publish' not in {row[1] for row in db.execute('PRAGMA table_info(events)')}:
                db.execute('ALTER TABLE events ADD COLUMN publish INTEGER NOT NULL DEFAULT 1')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def latest(self, site):
        with self.connect() as db:
            row = db.execute('SELECT products FROM snapshots WHERE site=? ORDER BY seq DESC LIMIT 1', (site,)).fetchone()
            return json.loads(row[0]) if row else None

    def commit(self, site, products, evidence, rules):
        validate(products, evidence.get('expected'))
        if not evidence.get('complete'):
            raise Incomplete('Traversal has no completion proof')
        at = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT products FROM snapshots WHERE site=? ORDER BY seq DESC LIMIT 1', (site,)).fetchone()
            old = {p['id']: p for p in json.loads(row[0])} if row else None
            new = {p['id']: p for p in products}
            seen = set()
            for prior, in db.execute('SELECT products FROM snapshots WHERE site=?', (site,)):
                seen.update(p['id'] for p in json.loads(prior))
            changes = []
            if old is not None:
                for key in sorted(new.keys() - old.keys()):
                    changes.append(('relisted' if key in seen else 'added', new[key], None))
                for key in sorted(old.keys() - new.keys()):
                    changes.append(('removed', old[key], None))
                for key in sorted(old.keys() & new.keys()):
                    if (new[key]['price'], new[key].get('original_price')) != (old[key]['price'], old[key].get('original_price')):
                        changes.append(('price_changed', new[key], old[key]))
                    if new[key].get('stock') != old[key].get('stock'):
                        changes.append(('stock_changed', new[key], old[key]))
            db.execute('INSERT INTO snapshots(site,at,products,evidence) VALUES(?,?,?,?)',
                       (site, at, json.dumps(sorted(products, key=lambda p: p['id'])), json.dumps(evidence)))
            for kind, product, previous in changes:
                db.execute('INSERT INTO events(site,at,kind,product,previous,publish) VALUES(?,?,?,?,?,?)',
                           (site, at, kind, json.dumps(product), json.dumps(previous), int(kind in rules)))
        return changes

    def failure(self, site, reason):
        with self.connect() as db:
            db.execute('INSERT INTO failures(site,at,reason) VALUES(?,?,?)',
                       (site, datetime.now(timezone.utc).isoformat(), reason))

    def feed(self, site):
        # Append-only event lines: returning to an earlier price still creates a new line.
        with self.connect() as db:
            lines = ['catalog-events-v1']
            for seq, at, kind, raw, previous in db.execute(
                    'SELECT seq,at,kind,product,previous FROM events WHERE site=? AND publish=1 ORDER BY seq', (site,)):
                p = json.loads(raw)
                lines.append(json.dumps({'event': seq, 'at': at, 'type': kind, 'product': p,
                                         'previous': json.loads(previous)}, ensure_ascii=False, sort_keys=True))
            return '\n'.join(lines) + '\n'
