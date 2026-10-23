"""Additive migration of diff_sqlite's real database; no replacement snapshot store."""
import json
import os
import sqlite3
import time
import uuid
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import diff_sqlite
from crawl4ai_run.run_crawl4ai import CollectionError, START, money

EVENTS = ['NEW', 'REMOVED', 'RETURNED', 'PRICE_DROP', 'PRICE_RISE']


def now(): return datetime.now(timezone.utc).isoformat()


SITE_FIELDS = ('id','name','url','adapter','interval_minutes','enabled','permission_confirmed','policy_note',
               'adapter_config','event_rules','notify_enabled','webhook_env','notification_channel','email_to','notification_channels')


def import_sites(db, path, overwrite=False):
    """Import a shareable site seed (config/sites.json). Existing sites are kept unless overwrite=True.
    Only configuration is imported; snapshots/history never are."""
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    added = []
    for site in document.get('sites', []):
        row = {k: site.get(k) for k in SITE_FIELDS}
        row['adapter_config'] = encode(row['adapter_config'] or {})
        row['event_rules'] = encode(row['event_rules'] or EVENTS)
        for k in ('enabled','permission_confirmed','notify_enabled'): row[k] = int(bool(row[k]))
        row['interval_minutes'] = int(row['interval_minutes'] or 15)
        for k in ('policy_note','webhook_env','email_to'): row[k] = row[k] or ''
        row['notification_channel'] = row['notification_channel'] or 'wecom'
        # The seed may use the multi-channel list, the legacy single fields, or both (list wins; first = primary).
        from .multichannel import parse, mirror
        entries = parse({**row, 'notification_channels': site.get('notification_channels') or []})
        row.update(mirror(row, entries)); row['notification_channels'] = encode(entries)
        verb = 'INSERT OR REPLACE' if overwrite else 'INSERT OR IGNORE'
        cur = db.execute(f"{verb} INTO monitor_sites({','.join(SITE_FIELDS)},created_at) VALUES({','.join('?'*len(SITE_FIELDS))},?)",
                         [row[k] for k in SITE_FIELDS] + [now()])
        if cur.rowcount: added.append(row['id'])
    return added
def encode(value): return json.dumps(value, ensure_ascii=False)


def run_summary(conn, run_id):
    """Counts for the email summary line: products in the run and change events it produced."""
    if not run_id: return None
    total = conn.execute('SELECT COUNT(*) FROM monitor_details WHERE run_id=?', (run_id,)).fetchone()[0]
    changes = conn.execute('SELECT COUNT(*) FROM monitor_events WHERE run_id=?', (run_id,)).fetchone()[0]
    return {'total': total, 'changes': changes}


def protect_file(path):
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        # Docker Desktop Windows bind mounts may allow SQLite writes but reject POSIX chmod.
        # An unwritable file still fails during SQLite operations, rather than being ignored.
        logging.getLogger(__name__).info('挂载目录不支持 POSIX chmod；数据库访问权限由宿主机目录控制')


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def backup(self, label='daily', keep=30):
        directory = self.path.parent / 'backups'
        directory.mkdir(exist_ok=True)
        target = directory / f'{label}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}.db'
        with self.connect() as source:
            destination = sqlite3.connect(target)
            try: source.backup(destination)
            finally: destination.close()
        protect_file(target)
        for old in sorted(directory.glob('daily-*.db'), reverse=True)[keep:]: old.unlink()
        return target.name

    def migrate(self):
        if self.path.exists():
            with self.connect() as db:
                migrated = db.execute("SELECT 1 FROM sqlite_master WHERE name='monitor_meta'").fetchone()
                columns = {r['name'] for r in db.execute('PRAGMA table_info(monitor_sites)')}
                outbox = {r['name'] for r in db.execute('PRAGMA table_info(monitor_outbox)')}
            if (not migrated or 'notification_channel' not in columns or 'notification_channels' not in columns
                    or (outbox and 'channel' not in outbox)): self.backup('before-migration')
        with self.connect() as db:
            db.executescript(diff_sqlite.SCHEMA)
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS monitor_meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS monitor_sites(
                    id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL,adapter TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL DEFAULT 15,enabled INTEGER NOT NULL DEFAULT 0,
                    permission_confirmed INTEGER NOT NULL DEFAULT 0,policy_note TEXT NOT NULL DEFAULT '',
                    adapter_config TEXT NOT NULL DEFAULT '{}',event_rules TEXT NOT NULL,
                    notify_enabled INTEGER NOT NULL DEFAULT 0,webhook_env TEXT NOT NULL DEFAULT '',
                    paused_reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS monitor_run_meta(
                    run_id INTEGER PRIMARY KEY REFERENCES runs(run_id),site_id TEXT NOT NULL,
                    complete INTEGER NOT NULL,evidence TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS monitor_run_site ON monitor_run_meta(site_id,run_id);
                CREATE TABLE IF NOT EXISTS monitor_details(
                    run_id INTEGER NOT NULL REFERENCES runs(run_id),product_id TEXT NOT NULL,payload TEXT NOT NULL,
                    PRIMARY KEY(run_id,product_id));
                CREATE TABLE IF NOT EXISTS monitor_events(
                    id INTEGER PRIMARY KEY,site_id TEXT NOT NULL,run_id INTEGER REFERENCES runs(run_id),
                    product_id TEXT NOT NULL,kind TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL,
                    UNIQUE(site_id,run_id,product_id,kind));
                CREATE TABLE IF NOT EXISTS monitor_outbox(
                    id INTEGER PRIMARY KEY,event_id INTEGER REFERENCES monitor_events(id),
                    site_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
                    next_at REAL NOT NULL DEFAULT 0,claimed_at REAL,last_error TEXT NOT NULL DEFAULT '',sent_at TEXT,
                    delivery_snapshot TEXT,channel TEXT NOT NULL DEFAULT '',UNIQUE(event_id,channel));
                CREATE TABLE IF NOT EXISTS monitor_logs(
                    id INTEGER PRIMARY KEY,site_id TEXT,started TEXT NOT NULL,ended TEXT,status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',run_id INTEGER REFERENCES runs(run_id));
                CREATE INDEX IF NOT EXISTS monitor_logs_site_id ON monitor_logs(site_id,id);
                CREATE INDEX IF NOT EXISTS monitor_logs_day ON monitor_logs(julianday(started));
                CREATE INDEX IF NOT EXISTS monitor_events_day ON monitor_events(julianday(at));
                CREATE TABLE IF NOT EXISTS monitor_storage_samples(
                    hour INTEGER PRIMARY KEY,database_bytes INTEGER NOT NULL,wal_bytes INTEGER NOT NULL,
                    backup_bytes INTEGER NOT NULL,free_bytes INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS monitor_leases(site_id TEXT PRIMARY KEY,owner TEXT,expires REAL);
                CREATE TABLE IF NOT EXISTS monitor_host_jobs(
                    id TEXT PRIMARY KEY,site_id TEXT NOT NULL,created REAL NOT NULL,status TEXT NOT NULL,
                    claim TEXT,payload TEXT NOT NULL,result TEXT);
                CREATE TABLE IF NOT EXISTS monitor_mail_tests(
                    id INTEGER PRIMARY KEY,site_id TEXT NOT NULL,recipients TEXT NOT NULL,status TEXT NOT NULL,
                    created TEXT NOT NULL,claimed_at REAL,finished TEXT,last_error TEXT NOT NULL DEFAULT '');
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(monitor_sites)')}
            for column, definition in [('notification_channel', "TEXT NOT NULL DEFAULT 'wecom'"), ('email_to', "TEXT NOT NULL DEFAULT ''")]:
                if column not in columns: db.execute(f'ALTER TABLE monitor_sites ADD COLUMN {column} {definition}')
            if 'notification_channels' not in columns:
                db.execute("ALTER TABLE monitor_sites ADD COLUMN notification_channels TEXT NOT NULL DEFAULT ''")
            outbox_columns = {r['name'] for r in db.execute('PRAGMA table_info(monitor_outbox)')}
            if 'delivery_snapshot' not in outbox_columns:
                db.execute('ALTER TABLE monitor_outbox ADD COLUMN delivery_snapshot TEXT')
            if 'channel' not in outbox_columns:
                # One row per event *per channel*: rebuild to replace UNIQUE(event_id) with UNIQUE(event_id,channel).
                # Existing rows get the channel they were (or would have been) sent through.
                db.execute('''CREATE TABLE monitor_outbox_multi(
                        id INTEGER PRIMARY KEY,event_id INTEGER REFERENCES monitor_events(id),
                        site_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
                        next_at REAL NOT NULL DEFAULT 0,claimed_at REAL,last_error TEXT NOT NULL DEFAULT '',sent_at TEXT,
                        delivery_snapshot TEXT,channel TEXT NOT NULL DEFAULT '',UNIQUE(event_id,channel))''')
                db.execute('''INSERT INTO monitor_outbox_multi(id,event_id,site_id,status,attempts,next_at,claimed_at,last_error,sent_at,delivery_snapshot,channel)
                        SELECT o.id,o.event_id,o.site_id,o.status,o.attempts,o.next_at,o.claimed_at,o.last_error,o.sent_at,o.delivery_snapshot,
                        COALESCE(json_extract(o.delivery_snapshot,'$.channel'),
                            CASE WHEN o.last_error LIKE 'Agent Mail%' THEN 'agentmail' WHEN o.last_error LIKE 'SMTP%' THEN 'email'
                            WHEN o.last_error LIKE '企业微信%' THEN 'wecom' WHEN o.last_error LIKE 'pushplus%' THEN 'pushplus'
                            WHEN o.last_error LIKE '腾讯微信%' OR o.last_error LIKE '微信%' THEN 'weixin' END,
                            s.notification_channel,'wecom')
                        FROM monitor_outbox o LEFT JOIN monitor_sites s ON s.id=o.site_id''')
                db.execute('DROP TABLE monitor_outbox')
                db.execute('ALTER TABLE monitor_outbox_multi RENAME TO monitor_outbox')
            db.execute('CREATE INDEX IF NOT EXISTS monitor_outbox_due ON monitor_outbox(status,channel,next_at)')
            # Rows inserted without a channel (older code paths/scripts) belong to the site's primary channel.
            db.execute('''CREATE TRIGGER IF NOT EXISTS monitor_outbox_default_channel AFTER INSERT ON monitor_outbox
                WHEN NEW.channel='' BEGIN UPDATE monitor_outbox SET channel=COALESCE((SELECT NULLIF(notification_channel,'')
                FROM monitor_sites WHERE id=NEW.site_id),'wecom') WHERE id=NEW.id; END''')
            # Each site's single channel becomes a one-item list (legacy fields stay as the primary/fallback).
            from .multichannel import parse
            for row in db.execute("SELECT * FROM monitor_sites WHERE notification_channels=''").fetchall():
                db.execute('UPDATE monitor_sites SET notification_channels=? WHERE id=?', (encode(parse(dict(row))), row['id']))
            # Policy-excluded alerts are history, not deliveries awaiting repair.
            db.execute("""UPDATE monitor_outbox SET status='suppressed',
                last_error='访问异常按现行规则不推送；原记录：' || last_error
                WHERE status IN ('held','pending') AND event_id IN
                (SELECT id FROM monitor_events WHERE kind='ACCESS_DENIED')""")
            seed = os.getenv('SITES_SEED')
            # Fresh databases only, so sites the owner later deletes are not resurrected on restart.
            if seed and Path(seed).is_file() and not db.execute('SELECT 1 FROM monitor_sites LIMIT 1').fetchone():
                import_sites(db, Path(seed))
            db.execute('INSERT OR IGNORE INTO monitor_sites(id,name,url,adapter,event_rules,created_at) VALUES(?,?,?,?,?,?)',
                ('lecomte', "Lecomte · Arc'teryx", START, 'odoo_lecomte', encode(EVENTS), now()))
            imported = db.execute("SELECT value FROM monitor_meta WHERE key='legacy_imported'").fetchone()
            if not imported:
                # Verify actual saved page evidence; never promote EasySpider partial runs.
                directory = self.path.parent.parent / 'legacy' / 'runs'
                for row in db.execute("SELECT * FROM runs WHERE tool='crawl4ai'").fetchall():
                    path = directory / f"crawl4ai_{row['run_tag']}.json"
                    if not path.exists(): continue
                    document = json.loads(path.read_text(encoding='utf-8'))
                    try: self.validate(document, legacy=True)
                    except CollectionError: continue
                    stored = diff_sqlite.load(db, row['run_id'])
                    if set(stored) != {str(p['product_id']) for p in document['products']}: continue
                    if row['started'] != document['meta']['started']: continue
                    db.execute('INSERT OR IGNORE INTO monitor_run_meta VALUES(?,?,?,?)',
                        (row['run_id'], 'lecomte', 1, encode(document['meta'])))
                    db.executemany('INSERT OR IGNORE INTO monitor_details VALUES(?,?,?)',
                        [(row['run_id'], str(p['product_id']), encode(p)) for p in document['products']])
                db.execute("INSERT INTO monitor_meta VALUES('legacy_imported','1')")
            db.execute("INSERT OR REPLACE INTO monitor_meta VALUES('schema_version','2')")
        protect_file(self.path)

    @staticmethod
    def validate(document, legacy=False):
        meta, products = document['meta'], document['products']
        pages = meta.get('pages', [])
        ids = [str(p.get('product_id') or '') for p in products]
        count = meta.get('site_pages')
        # Legacy documents predate the complete flag; their page evidence is mandatory.
        if (meta.get('complete') is False or (not legacy and meta.get('complete') is not True)
            or not isinstance(count, int) or count < 1):
            raise CollectionError('快照没有分页完整性证明')
        if (len(pages) != count or [p.get('page') for p in pages] != list(range(1, count + 1))
            or any((p.get('status') != 200 and not (legacy and p.get('page')==1 and p.get('status')==303 and p.get('success') is True))
                   or p.get('success') is False for p in pages)
            or sum(p.get('items', -1) for p in pages) != len(products)
            or meta.get('site_total') != len(products) or meta.get('extracted') != len(products)
            or not all(ids) or len(set(ids)) != len(ids)):
            raise CollectionError('快照分页、商品 ID 或总数不完整')
        for p in products:
            if not p.get('name') or not p.get('url') or p.get('sale_price') is None:
                raise CollectionError('商品关键字段缺失')
            p['product_id'] = str(p['product_id'])
            p['sale_price'] = money(p['sale_price'])
            p['original_price'] = money(p.get('original_price'))
        if not meta.get('started'): raise CollectionError('快照时间缺失')

    def site_dict(self, row):
        site = dict(row)
        for key in ('adapter_config', 'event_rules'): site[key] = json.loads(site[key])
        for key in ('enabled', 'permission_confirmed', 'notify_enabled'): site[key] = bool(site[key])
        site['webhook_configured'] = bool(site['webhook_env'] and os.getenv(site['webhook_env']))
        from .pushplus import token
        try:
            token(site)
            site['pushplus_configured'] = True
        except ValueError:
            site['pushplus_configured'] = False
        from . import push
        try:
            push.config(site)
            site['push_configured'] = True
        except ValueError:
            site['push_configured'] = False
        from .email_delivery import configuration
        try:
            configuration(site, self)
            site['email_configured'] = True
        except ValueError:
            site['email_configured'] = False
        # Agent Mail readiness also depends on the host worker; the admin UI combines both.
        site['agentmail_configured'] = bool(site['email_to'])
        from .multichannel import parse, effective
        site['notification_channels'] = parse(site)
        # Per selected channel: is its env name / recipient usable? (weixin/agentmail also need their workers.)
        status = {}
        for e in site['notification_channels']:
            view = effective(site, e); c = e['channel']
            try:
                if c == 'wecom': status[c] = bool(e['env'] and os.getenv(e['env']))
                elif c == 'pushplus': token(view); status[c] = True
                elif c in ('bark', 'ntfy'): push.config(view); status[c] = True
                elif c == 'email': configuration(view, self); status[c] = True
                elif c == 'agentmail': status[c] = bool(e['to'])
                else: status[c] = True
            except ValueError: status[c] = False
        site['channel_status'] = status
        return site

    def sites(self):
        with self.connect() as db:
            return [self.site_dict(row) for row in db.execute('SELECT * FROM monitor_sites ORDER BY created_at,id')]

    def site(self, site_id):
        return next((s for s in self.sites() if s['id'] == site_id), None)

    def save_site(self, site):
        from .multichannel import reconcile, mirror
        with self.connect() as db:
            stored = db.execute('SELECT * FROM monitor_sites WHERE id=?', (site['id'],)).fetchone()
        entries = reconcile(site, dict(stored) if stored else None)
        # Update the caller's dict too, so a site dict reused for a later save is not stale.
        site.update(mirror(site, entries)); site['notification_channels'] = entries
        site = dict(site)
        with self.connect() as db:
            # URL/adapter changes must use a new site ID so unrelated catalogues cannot share a baseline.
            old = db.execute('SELECT url,adapter,adapter_config FROM monitor_sites WHERE id=?', (site['id'],)).fetchone()
            has_history = db.execute('SELECT 1 FROM monitor_run_meta WHERE site_id=? LIMIT 1', (site['id'],)).fetchone()
            if old and has_history and (old['url'] != site['url'] or old['adapter'] != site['adapter']
                or {k:v for k,v in json.loads(old['adapter_config']).items() if k not in ('persistent_session','page_delay','pagination_mode','load_more_selector','execution_mode')}
                != {k:v for k,v in site['adapter_config'].items() if k not in ('persistent_session','page_delay','pagination_mode','load_more_selector','execution_mode')}):
                raise ValueError('已有历史的网站更改 URL 或适配器时，请使用新的网站 ID 建立独立基线')
            db.execute('''INSERT INTO monitor_sites(id,name,url,adapter,interval_minutes,enabled,permission_confirmed,
                policy_note,adapter_config,event_rules,notify_enabled,webhook_env,created_at,notification_channel,email_to,notification_channels)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,
                adapter=excluded.adapter,interval_minutes=excluded.interval_minutes,enabled=excluded.enabled,
                permission_confirmed=excluded.permission_confirmed,policy_note=excluded.policy_note,
                adapter_config=excluded.adapter_config,event_rules=excluded.event_rules,notify_enabled=excluded.notify_enabled,
                webhook_env=excluded.webhook_env,notification_channel=excluded.notification_channel,email_to=excluded.email_to,
                notification_channels=excluded.notification_channels,
                paused_reason=CASE WHEN excluded.enabled=1 THEN '' ELSE monitor_sites.paused_reason END ''',
                (site['id'],site['name'],site['url'],site['adapter'],site['interval_minutes'],int(site['enabled']),
                 int(site['permission_confirmed']),site['policy_note'],encode(site['adapter_config']),
                 encode(site['event_rules']),int(site['notify_enabled']),site['webhook_env'],now(),
                 site.get('notification_channel','wecom'),site.get('email_to',''),encode(entries)))

    def latest_id(self, db, site_id):
        row = db.execute('SELECT MAX(run_id) FROM monitor_run_meta WHERE site_id=? AND complete=1', (site_id,)).fetchone()
        return row[0]

    def commit(self, site_id, document):
        self.validate(document)
        site = self.site(site_id)
        if not site: raise ValueError('网站不存在')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self.latest_id(db, site_id)
            tool = 'crawl4ai:' + site_id
            existing = db.execute('SELECT run_id FROM runs WHERE tool=? AND started=?',
                (tool, document['meta']['started'])).fetchone()
            if existing: return existing[0], []
            document = {**document, 'meta': {**document['meta'], 'tool': tool}}
            rid = diff_sqlite.ingest(db, '', document=document, commit=False, quiet=True)
            db.execute('INSERT INTO monitor_run_meta VALUES(?,?,?,?)', (rid, site_id, 1, encode(document['meta'])))
            products = {p['product_id']: p for p in document['products']}
            db.executemany('INSERT INTO monitor_details VALUES(?,?,?)', [(rid,k,encode(p)) for k,p in products.items()])
            result, events = None, []
            if previous:
                # Reuse original stable-ID diff, including unchanged total-count cases.
                result = diff_sqlite.diff(db, tool, previous, rid, record=True, quiet=True, commit=False)
                seen = {r[0] for r in db.execute('''SELECT DISTINCT s.product_id FROM snapshots s JOIN monitor_run_meta m
                    ON m.run_id=s.run_id WHERE m.site_id=? AND m.run_id<? AND m.complete=1''', (site_id,rid))}
                for pid in result['new']: events.append(('RETURNED' if pid in seen else 'NEW', pid, products[pid], None))
                for pid in result['disappeared']:
                    old = db.execute('SELECT payload FROM monitor_details WHERE run_id=? AND product_id=?', (previous,pid)).fetchone()
                    events.append(('REMOVED', pid, json.loads(old[0]), None))
                for change in result['price_changes']:
                    if change['field'] != 'sale_price': continue
                    kind = 'PRICE_DROP' if Decimal(str(change['new'])) < Decimal(str(change['old'])) else 'PRICE_RISE'
                    events.append((kind,change['product_id'],products[change['product_id']],change['old']))
            emitted = []
            for kind,pid,product,old_price in events:
                payload = {'product': product, 'old_price': old_price}
                cur = db.execute('INSERT INTO monitor_events(site_id,run_id,product_id,kind,at,payload) VALUES(?,?,?,?,?,?)',
                    (site_id,rid,pid,kind,now(),encode(payload)))
                emitted.append({'id':cur.lastrowid,'kind':kind,'product_id':pid})
                if (site['notify_enabled'] and kind in site['event_rules']
                    and not (kind == 'PRICE_DROP' and Decimal(str(old_price)) > 0
                             and (Decimal(str(old_price)) - Decimal(str(product['sale_price']))) < Decimal(str(old_price)) * Decimal('0.10'))):
                    # One independent delivery per selected channel; UNIQUE(event_id,channel) dedupes.
                    db.executemany('INSERT OR IGNORE INTO monitor_outbox(event_id,site_id,channel) VALUES(?,?,?)',
                                   [(cur.lastrowid,site_id,e['channel']) for e in site['notification_channels']])
            return rid, emitted

    def acquire(self, site_id, owner, seconds=360):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT expires FROM monitor_leases WHERE site_id=?', (site_id,)).fetchone()
            if current and current[0] > time.time(): return False
            db.execute('INSERT OR REPLACE INTO monitor_leases VALUES(?,?,?)', (site_id,owner,time.time()+seconds))
            return True

    def release(self, site_id, owner):
        with self.connect() as db: db.execute('DELETE FROM monitor_leases WHERE site_id=? AND owner=?', (site_id,owner))

    def log_start(self, site_id):
        with self.connect() as db:
            return db.execute("INSERT INTO monitor_logs(site_id,started,status) VALUES(?,?,'RUNNING')", (site_id,now())).lastrowid

    def log_finish(self, log_id, status, message, rid=None):
        with self.connect() as db:
            db.execute('UPDATE monitor_logs SET ended=?,status=?,message=?,run_id=? WHERE id=?', (now(),status,message,rid,log_id))

    def pause(self, site_id, reason, log_id):
        with self.connect() as db:
            db.execute('UPDATE monitor_sites SET enabled=0,paused_reason=? WHERE id=?', (reason,site_id))
            cur = db.execute("INSERT INTO monitor_events(site_id,product_id,kind,at,payload) VALUES(?,'','ACCESS_DENIED',?,?)",
                (site_id,now(),encode({'message':reason,'log_id':log_id})))
            # Operational failures remain visible in history, without external notifications.

    def products(self, site_id):
        with self.connect() as db:
            rid = self.latest_id(db, site_id)
            if not rid: return []
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM monitor_details WHERE run_id=? ORDER BY product_id', (rid,))]

    def product_page(self, site_id, q='', limit=100, offset=0):
        with self.connect() as db:
            # SQLite lower() is ASCII-only. Preserve the existing Unicode search semantics.
            db.create_function('casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
            db.execute('BEGIN')
            rid = self.latest_id(db, site_id)
            if rid is None: return {'items':[], 'total':0}
            where, args = 'run_id=?', [rid]
            if q:
                where += " AND instr(casefold(json_extract(payload,'$.name') || ' ' || product_id),?)>0"
                args.append(q.casefold())
            total = db.execute(f'SELECT COUNT(*) FROM monitor_details WHERE {where}', args).fetchone()[0]
            rows = db.execute(f'SELECT payload FROM monitor_details WHERE {where} ORDER BY product_id LIMIT ? OFFSET ?',
                              [*args, limit, offset])
            return {'items':[json.loads(row[0]) for row in rows], 'total':total}

    def history(self, table, site_id=None, limit=100, offset=0):
        if table not in ('monitor_events','monitor_logs','monitor_outbox'): raise ValueError('无效表')
        with self.connect() as db:
            where, args = (' WHERE site_id=?', [site_id]) if site_id else ('', [])
            total = db.execute(f'SELECT COUNT(*) FROM {table}{where}', args).fetchone()[0]
            rows = [dict(r) for r in db.execute(f'SELECT * FROM {table}{where} ORDER BY id DESC LIMIT ? OFFSET ?', [*args,limit,offset])]
            for row in rows:
                if 'payload' in row: row['payload'] = json.loads(row['payload'])
            return {'items':rows,'total':total}

    def product_counts(self):
        with self.connect() as db:
            counts = {s['id']:0 for s in self.sites()}
            counts.update(dict(db.execute('''SELECT latest.site_id,COUNT(d.product_id)
                FROM (SELECT site_id,MAX(run_id) AS run_id FROM monitor_run_meta WHERE complete=1 GROUP BY site_id) latest
                LEFT JOIN monitor_details d ON d.run_id=latest.run_id GROUP BY latest.site_id''').fetchall()))
            return counts

    def overview(self):
        sites = self.sites()
        with self.connect() as db:
            counts = self.product_counts()
            runs = db.execute('SELECT COUNT(*) FROM monitor_run_meta WHERE complete=1').fetchone()[0]
            statuses = dict(db.execute('SELECT status,COUNT(*) FROM monitor_outbox GROUP BY status').fetchall())
            pending = statuses.get('pending',0) + statuses.get('sending',0)
            events = db.execute('SELECT COUNT(*) FROM monitor_events').fetchone()[0]
        return {'sites':sites,'product_counts':counts,'successful_runs':runs,'events':events,
                'pending_notifications':pending,'notification_counts':statuses,
                'attention_notifications':statuses.get('held',0)+statuses.get('uncertain',0)}
