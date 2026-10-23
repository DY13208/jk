"""Server side of the Agent Mail channel.

The official agently-cli keeps its OAuth token in the Windows keychain, which a Linux container
cannot read. A host worker (scripts/agentmail_worker.py) therefore runs the CLI, but it never opens
SQLite itself: SQLite WAL files shared between Docker Desktop and Windows cause "disk I/O error".
Instead the worker talks to this module over the local admin HTTP API, authenticated with a random
token file in the bind-mounted data directory. Only the container process writes the database.
"""
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone

from .multichannel import entry, effective
from .email_delivery import recipients
from .database import run_summary
from .mail_templates import message, render_html, subject

STALE_SECONDS = 120          # a claimed job without a result after this is "uncertain", never resent
HEARTBEAT_SECONDS = 45
MAX_ATTEMPTS = 5


def now_iso(): return datetime.now(timezone.utc).isoformat()


def token_path(db):
    return db.path.parent / 'agentmail-worker.token'


def worker_token(db):
    path = token_path(db)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(32), encoding='ascii')
        try: os.chmod(path, 0o600)
        except OSError: pass
    return path.read_text(encoding='ascii').strip()


def valid_token(db, supplied):
    return bool(supplied) and secrets.compare_digest(supplied.strip(), worker_token(db))


def status(db):
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM monitor_meta WHERE key='agentmail_worker'").fetchone()
        pending = conn.execute("""SELECT COUNT(*) FROM monitor_outbox o
            WHERE o.channel='agentmail' AND o.status IN ('pending','sending')""").fetchone()[0]
        tests = [dict(r) for r in conn.execute('SELECT * FROM monitor_mail_tests ORDER BY id DESC LIMIT 10')]
    state = json.loads(row[0]) if row else {}
    heartbeat = state.get('heartbeat', 0)
    cli = state.get('cli') or {}
    running = time.time() - heartbeat < HEARTBEAT_SECONDS and not state.get('probe_only')
    if not state: text = '本机 Agent Mail 工作进程尚未启动过'
    elif state.get('stopped'): text = state.get('message') or '工作进程已停止'
    elif state.get('probe_only'): text = '已检测 CLI 状态（--status），工作进程未启动：' + (state.get('message') or '')
    elif not running: text = '本机工作进程未运行（最近一次心跳已超时）'
    else: text = state.get('message') or '工作进程运行中'
    flow = auth_flow(db)
    return {'auth': flow, 'running': running, 'last_seen': datetime.fromtimestamp(heartbeat, timezone.utc).isoformat() if heartbeat else None,
            'account': cli.get('account') or state.get('account', ''), 'cli': cli, 'message': text,
            'pending': pending, 'tests': tests,
            'ready': running and bool(cli.get('installed')) and bool(cli.get('logged_in'))}


def heartbeat(db, report):
    allowed = {k: report.get(k) for k in ('installed', 'path', 'version', 'logged_in', 'account', 'quota', 'workspace', 'error')}
    state = {'heartbeat': time.time(), 'account': allowed.get('account') or '', 'cli': allowed,
             'message': str(report.get('message') or '工作进程运行中')[:300], 'host': str(report.get('host') or '')[:100],
             'probe_only': bool(report.get('probe_only') or report.get('stopping')), 'stopped': bool(report.get('stopping'))}
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO monitor_meta(key,value) VALUES('agentmail_worker',?)", (json.dumps(state, ensure_ascii=False),))
    return {'ok': True}


def request_test(db, site_id, to=None):
    site = db.site(site_id)
    if not site: raise LookupError('网站不存在')
    selected = entry(site, 'agentmail')
    addresses = recipients(to if to else (selected['to'] if selected and selected['to'] else site.get('email_to', '')))
    with db.connect() as conn:
        busy = conn.execute("SELECT 1 FROM monitor_mail_tests WHERE status IN ('pending','sending') LIMIT 1").fetchone()
        if busy: raise ValueError('已有测试邮件在队列中，请等待处理后再试')
        cur = conn.execute("INSERT INTO monitor_mail_tests(site_id,recipients,status,created) VALUES(?,?,'pending',?)",
                           (site_id, ', '.join(addresses), now_iso()))
        return {'id': cur.lastrowid, 'recipients': addresses}


def attempt_id(claimed):
    return f'{float(claimed or 0):.6f}'


def _job(kind, row_id, site, event, addresses, summary=None, attempt=None):
    # Outbox jobs carry an attempt id (the claim timestamp, unique per claim even after a manual retry resets
    # `attempts`): a late receipt from an earlier attempt of the same row is stale and must not finish it.
    return {'job': f'{kind}:{row_id}' + (f':{attempt}' if attempt is not None else ''), 'subject': subject(site, event), 'html': render_html(site, event, summary),
            'text': message(site, event), 'to': addresses}


def claim(db):
    """Atomically hand one job to the worker. Returns None when nothing is due."""
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cutoff = time.time() - STALE_SECONDS
        conn.execute("UPDATE monitor_outbox SET status='uncertain',last_error='发送进程中断，需人工核对' WHERE status='sending' AND claimed_at<? "
                     "AND channel='agentmail'", (cutoff,))
        conn.execute("UPDATE monitor_mail_tests SET status='uncertain',last_error='发送进程中断，请查看收件箱' WHERE status='sending' AND claimed_at<?", (cutoff,))
        test = conn.execute("SELECT * FROM monitor_mail_tests WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        if test:
            site = conn.execute('SELECT * FROM monitor_sites WHERE id=?', (test['site_id'],)).fetchone()
            conn.execute("UPDATE monitor_mail_tests SET status='sending',claimed_at=? WHERE id=?", (time.time(), test['id']))
            event = {'id': f"T{test['id']}", 'kind': 'TEST', 'at': test['created'], 'payload': {}}
            return _job('test', test['id'], dict(site), event, recipients(test['recipients']))
        for _ in range(50):
            row = conn.execute("""SELECT o.* FROM monitor_outbox o
                WHERE o.status='pending' AND o.next_at<=? AND o.channel='agentmail' ORDER BY o.id LIMIT 1""",
                (time.time(),)).fetchone()
            if not row: return None
            site = conn.execute('SELECT * FROM monitor_sites WHERE id=?', (row['site_id'],)).fetchone()
            selected = entry(dict(site), 'agentmail') if site else None
            if not selected:
                conn.execute("UPDATE monitor_outbox SET status='held',last_error='站点已不再选用该通知方式，未发送' WHERE id=?", (row['id'],)); continue
            site = effective(dict(site), selected)
            if not site['notify_enabled']:
                conn.execute("UPDATE monitor_outbox SET status='held',last_error='通知已关闭' WHERE id=?", (row['id'],)); continue
            try: addresses = recipients(site['email_to'])
            except ValueError:
                conn.execute("UPDATE monitor_outbox SET status='held',last_error='Agent Mail 收件邮箱配置无效' WHERE id=?", (row['id'],)); continue
            event = dict(conn.execute('SELECT * FROM monitor_events WHERE id=?', (row['event_id'],)).fetchone())
            if event['kind'] == 'ACCESS_DENIED':
                conn.execute("UPDATE monitor_outbox SET status='suppressed',last_error='访问异常仅在后台记录，不推送' WHERE id=?", (row['id'],)); continue
            event['payload'] = json.loads(event['payload'])
            claimed = time.time()
            conn.execute("UPDATE monitor_outbox SET status='sending',claimed_at=?,attempts=attempts+1 WHERE id=?", (claimed, row['id']))
            from .mail_digest import claim_digest
            rows, event = claim_digest(conn, row, event, claimed)
            batch_key = f"mail_batch:{row['id']}:{attempt_id(claimed)}"
            conn.execute('INSERT OR REPLACE INTO monitor_meta(key,value) VALUES(?,?)', (batch_key, json.dumps([item['id'] for item in rows])))
            job=_job('outbox', row['id'], site, event, addresses, run_summary(conn, event.get('run_id')), attempt=attempt_id(claimed))
            from .notification_history import snapshot
            worker=conn.execute("SELECT value FROM monitor_meta WHERE key='agentmail_worker'").fetchone()
            worker=json.loads(worker[0]) if worker else {}
            sender=(worker.get('cli') or {}).get('account') or worker.get('account','')
            evidence=snapshot(site,job['text'],channel='agentmail',recipients=addresses,sender=sender,title=job['subject'],html=job['html'])
            for item in rows:
                conn.execute('UPDATE monitor_outbox SET delivery_snapshot=? WHERE id=?',(json.dumps(evidence,ensure_ascii=False),item['id']))
            return job
        return None


HELD = {2: 'Agent Mail 参数不合规', 3: 'Agent Mail 授权失效，请在本机重新运行 agently-cli auth login', 6: 'Agent Mail 业务拒绝'}


def outcome(result):
    """Map the CLI's documented exit code/JSON flags (never its raw output) to a queue state."""
    code = result.get('returncode')
    if code == 0 and result.get('ok') is True and result.get('queued') is True and not result.get('confirmation_required'):
        return 'sent', 'Agent Mail 已接受并排队；最终收件状态请在邮箱中核对', False
    if code == 7: return 'pending', 'Agent Mail 限频或额度用尽', True
    if code in HELD: return 'held', HELD[code], False
    if result.get('error') == 'timeout': return 'uncertain', 'Agent Mail CLI 超时，可能已发送；请核对邮箱后再重试', False
    return 'uncertain', 'Agent Mail 发送结果不确定，请核对邮箱后重试', False


def finish(db, job, result):
    kind, _, raw = str(job).partition(':')
    raw, _, attempt = raw.partition(':')
    try: row_id = int(raw)
    except ValueError: raise LookupError('任务不存在') from None
    state, reason, retry = outcome(result)
    with db.connect() as conn:
        if kind == 'test':
            row = conn.execute("SELECT status FROM monitor_mail_tests WHERE id=?", (row_id,)).fetchone()
            if not row or row[0] != 'sending': raise LookupError('任务不存在或已结束')
            if state == 'pending': state = 'held'  # a test is never retried automatically
            conn.execute('UPDATE monitor_mail_tests SET status=?,last_error=?,finished=? WHERE id=?', (state, reason, now_iso(), row_id))
            return {'status': state}
        if kind != 'outbox': raise LookupError('任务不存在')
        row = conn.execute("SELECT status,attempts,claimed_at FROM monitor_outbox WHERE id=?", (row_id,)).fetchone()
        if not row or row[0] != 'sending': raise LookupError('任务不存在或已结束')
        # Only the current attempt may finish the row. A legacy id without attempt (claimed before this upgrade)
        # is accepted only for a first attempt, the one case where it cannot be confused with a newer claim.
        current = attempt == attempt_id(row['claimed_at']) if attempt else row['attempts'] == 1
        if not current: raise LookupError('过期的发送回执（不是当前这次尝试），已忽略')
        next_at = 0
        if retry:
            if row['attempts'] < MAX_ATTEMPTS: next_at = time.time() + min(3600, 60 * 2 ** row['attempts'])
            else: state = 'held'
        batch_key = f"mail_batch:{row_id}:{attempt_id(row['claimed_at'])}"
        batch = conn.execute('SELECT value FROM monitor_meta WHERE key=?', (batch_key,)).fetchone()
        ids = json.loads(batch[0]) if batch else [row_id]
        for item_id in ids:
            conn.execute('UPDATE monitor_outbox SET status=?,last_error=?,next_at=?,sent_at=? WHERE id=? AND status="sending" AND claimed_at=?',
                         (state, reason, next_at, now_iso() if state == 'sent' else None, item_id, row['claimed_at']))
        conn.execute('DELETE FROM monitor_meta WHERE key=?', (batch_key,))
        return {'status': state}


# ---- Re-authorization requested from the admin page, executed by the host worker -------------
ACTIVE = ('requested', 'running', 'waiting')
FLOW_STATES = ('running', 'waiting', 'done', 'failed', 'expired', 'cancelled')
FLOW_TTL = 16 * 60


def _get_flow(conn):
    row = conn.execute("SELECT value FROM monitor_meta WHERE key='agentmail_auth'").fetchone()
    return json.loads(row[0]) if row else None


def _put_flow(conn, flow):
    flow['updated'] = now_iso()
    conn.execute("INSERT OR REPLACE INTO monitor_meta(key,value) VALUES('agentmail_auth',?)", (json.dumps(flow, ensure_ascii=False),))
    return flow


def _expired(flow):
    try: started = datetime.fromisoformat(flow['requested'])
    except (KeyError, ValueError): return True
    return (datetime.now(timezone.utc) - started).total_seconds() > FLOW_TTL


def auth_flow(db):
    with db.connect() as conn:
        flow = _get_flow(conn)
        if flow and flow['state'] in ACTIVE and _expired(flow):
            flow = _put_flow(conn, {**flow, 'state': 'expired', 'message': '授权请求已过期，请重新点击「重新授权 / 登录」'})
        return flow


def request_auth(db, action):
    if action not in ('login', 'logout', 'cancel'): raise ValueError('未知操作')
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        flow = _get_flow(conn)
        active = flow and flow['state'] in ACTIVE and not _expired(flow)
        if action == 'cancel':
            if not active: return flow
            if flow['state'] == 'requested':
                return _put_flow(conn, {**flow, 'state': 'cancelled', 'message': '已取消'})
            return _put_flow(conn, {**flow, 'cancel': True, 'message': '正在取消…'})
        if active and flow['action'] == action and not flow.get('cancel'): return flow  # idempotent double click
        if active and flow['state'] != 'requested': raise ValueError('已有授权流程在进行中，请先完成或取消')
        return _put_flow(conn, {'id': secrets.token_hex(8), 'action': action, 'state': 'requested', 'url': '', 'user_code': '',
                                'account': '', 'requested': now_iso(),
                                'message': '等待本机 Agent Mail 助手领取请求…' if action == 'login' else '等待本机助手退出登录…'})


def worker_command(db):
    flow = auth_flow(db)
    return flow if flow and flow['state'] in ACTIVE else None


def worker_auth_update(db, update):
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        flow = _get_flow(conn)
        if not flow or flow.get('id') != update.get('id'): raise LookupError('授权流程不存在或已被替换')
        state = update.get('state')
        if state not in FLOW_STATES: raise ValueError('无效状态')
        url = str(update.get('url') or flow.get('url') or '')
        if url and (not url.startswith('https://') or len(url) > 2000 or re.search(r'[\s"<>]', url)): url = ''
        code = str(update.get('user_code') or flow.get('user_code') or '')
        if not re.fullmatch(r'[A-Za-z0-9-]{0,20}', code): code = ''
        account = str(update.get('account') or '')[:200]
        return _put_flow(conn, {**flow, 'state': state, 'url': url, 'user_code': code, 'account': account,
                                'message': str(update.get('message') or '')[:300]})
