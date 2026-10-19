"""Authenticated host collection queue. SQLite is written exclusively by the server."""
import asyncio
import json
import os
import secrets
import time
import uuid
from crawl4ai_run.run_crawl4ai import CollectionError


def token(db,mode='host'):
    path=db.path.parent/('extension-worker.token' if mode=='extension' else 'collector-worker.token')
    if not path.exists():
        path.write_text(secrets.token_urlsafe(32),encoding='ascii')
        try: os.chmod(path,0o600)
        except OSError: pass
    return path.read_text(encoding='ascii').strip()


def valid_token(db,value,mode='host'): return bool(value) and secrets.compare_digest(token(db,mode),value)


def heartbeat(db,mode='host'):
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO monitor_meta VALUES(?,?)",('collector_heartbeat:'+mode,str(time.time())))
    return {'ok':True}


def status(db,mode='host'):
    with db.connect() as c:
        row=c.execute("SELECT value FROM monitor_meta WHERE key=?",('collector_heartbeat:'+mode,)).fetchone()
    return {'running':bool(row and time.time()-float(row[0])<(120 if mode=='extension' else 45)),
            'heartbeat':float(row[0]) if row else None}


def cancel_interrupted(db):
    with db.connect() as c:
        c.execute("UPDATE monitor_host_jobs SET status='cancelled',result=NULL WHERE status IN ('pending','running')")


async def collect(db,site):
    mode=site['adapter_config'].get('execution_mode','host')
    if not status(db,mode)['running']: raise CollectionError('日常 Chrome 扩展未连接' if mode=='extension' else '本机 Chrome 采集进程未连接，请运行 Start-Collector.ps1')
    job=uuid.uuid4().hex
    payload={k:site[k] for k in ('id','adapter','url','adapter_config')}
    with db.connect() as c:
        # Completed jobs are transient transport records, not collection history.
        c.execute("DELETE FROM monitor_host_jobs WHERE created<? AND status NOT IN ('pending','running')",(time.time()-86400,))
        c.execute('INSERT INTO monitor_host_jobs(id,site_id,created,status,payload) VALUES(?,?,?,\'pending\',?)',
                  (job,site['id'],time.time(),json.dumps(payload)))
    try:
        while True:
            with db.connect() as c: row=c.execute('SELECT status,result FROM monitor_host_jobs WHERE id=?',(job,)).fetchone()
            if row['status']=='done':
                result=json.loads(row['result'])
                if 'error' in result: raise CollectionError(result['error'],result.get('status'))
                return result['document']
            if row['status']=='cancelled': raise CollectionError('本机采集任务已取消；未提交快照')
            await asyncio.sleep(1)
    finally:
        with db.connect() as c:
            c.execute("UPDATE monitor_host_jobs SET status='cancelled',result=NULL WHERE id=? AND status IN ('pending','running')",(job,))


def claim(db,mode='host'):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT j.* FROM monitor_host_jobs j JOIN monitor_sites s ON j.site_id=s.id WHERE j.status='pending' AND j.created>? AND s.enabled=1 AND s.permission_confirmed=1 AND COALESCE(json_extract(j.payload,'$.adapter_config.execution_mode'),'host')=? ORDER BY j.created LIMIT 1",(time.time()-280,mode)).fetchone()
        if not row: return None
        nonce=uuid.uuid4().hex
        c.execute("UPDATE monitor_host_jobs SET status='running',claim=? WHERE id=?",(nonce,row['id']))
        return {'id':row['id'],'claim':nonce,'site':json.loads(row['payload'])}


def finish(db,body,mode='host'):
    result=body.get('result') or {}
    if not isinstance(result,dict) or not any(k in result for k in ('document','error','capture')): raise ValueError('采集结果无效')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM monitor_host_jobs WHERE id=? AND claim=? AND status='running' AND created>?",(body.get('id'),body.get('claim'),time.time()-300)).fetchone()
        if not row: raise LookupError('任务已过期、完成或领取凭据无效')
        if json.loads(row['payload'])['adapter_config'].get('execution_mode','host')!=mode:
            raise LookupError('任务运行方式不一致')
        site=db.site(row['site_id'])
        if not site or not site['enabled'] or not site['permission_confirmed']: raise LookupError('网站已暂停')
        try:
            if 'capture' in result:
                from .extension_capture import normalize
                result={'document':normalize(json.loads(row['payload']),result['capture'])}
            if 'document' in result: db.validate(result['document'])
        except ValueError as exc:
            # The delivery reached us. A failed completeness check is a terminal collection
            # failure, not a transport error that can be fixed by resending identical HTML.
            result={'error':str(exc)[:250],'status':getattr(exc,'status',None)}
        c.execute("UPDATE monitor_host_jobs SET status='done',result=? WHERE id=?",(json.dumps(result,ensure_ascii=False),row['id']))
    return {'ok':True,**({'rejected':True,'error':result['error']} if 'error' in result else {})}
