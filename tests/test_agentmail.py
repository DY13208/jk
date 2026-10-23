import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from monitor import agentmail
from monitor.database import Database
from monitor.notifications import Notifier
from monitor.mail_templates import render_html
from monitor.web import create_app
from scripts import agentmail_worker as worker
from tests.test_monitor import product, document


@pytest.fixture
def queued(tmp_path):
    db=Database(tmp_path/'test.db');db.migrate()
    site=db.site('lecomte');site.update(notification_channel='agentmail',email_to='one@example.test',notify_enabled=True)
    db.save_site(site);db.commit('lecomte',document([product(1)]));db.commit('lecomte',document([product(1),product(2)]))
    return db


class LocalApi:
    """Worker Api backed directly by the server functions (same code paths as the HTTP routes)."""
    def __init__(self, db): self.db=db
    def post(self, path, payload=None):
        if path=='claim': return {'job':agentmail.claim(self.db)}
        if path=='result': return agentmail.finish(self.db,payload['job'],payload['result'])
        if path=='heartbeat': return agentmail.heartbeat(self.db,payload)


def cli(code=0, stdout=None, calls=None):
    def runner(command,args,cwd=None,timeout=None):
        from pathlib import Path
        if calls is not None: calls.append((args,Path(cwd,'body.html').read_text(encoding='utf-8') if cwd else ''))
        return SimpleNamespace(returncode=code,stdout=json.dumps({'ok':True,'queued':True}) if stdout is None else stdout)
    return runner


def test_agentmail_cli_mock_delivery_and_dedup(queued):
    calls=[]
    assert worker.tick(LocalApi(queued),['agently-cli'],{'logged_in':True},cli(calls=calls))==1
    assert worker.tick(LocalApi(queued),['agently-cli'],{'logged_in':True},cli(calls=calls))==0
    assert len(calls)==1
    args,body=calls[0]
    assert '--confirmed' in args and args[args.index('--body-format')+1]=='html' and args[args.index('--to')+1]=='one@example.test'
    assert '商品 2' in body
    assert queued.history('monitor_outbox')['items'][0]['status']=='sent'


@pytest.mark.parametrize('code,state',[(1,'uncertain'),(2,'held'),(3,'held'),(4,'uncertain'),(6,'held'),(7,'pending'),(8,'uncertain')])
def test_agentmail_exit_codes_and_secret_redaction(queued,code,state):
    worker.tick(LocalApi(queued),['fake'],{'logged_in':True},cli(code,'SECRET_DO_NOT_LOG'))
    row=queued.history('monitor_outbox')['items'][0]
    assert row['status']==state and 'SECRET' not in row['last_error']


def test_rate_limit_backs_off_then_holds(queued):
    for _ in range(6):
        with queued.connect() as c: c.execute("UPDATE monitor_outbox SET next_at=0 WHERE status='pending'")
        worker.tick(LocalApi(queued),['fake'],{'logged_in':True},cli(7,'{}'))
    assert queued.history('monitor_outbox')['items'][0]['status']=='held'


def test_confirmation_response_is_not_success(queued):
    out=json.dumps({'ok':True,'data':{'confirmation_required':True}})
    worker.tick(LocalApi(queued),['fake'],{'logged_in':True},cli(0,out))
    assert queued.history('monitor_outbox')['items'][0]['status']=='uncertain'


def test_cli_timeout_is_uncertain_never_resent(queued):
    def runner(*a,**kw): raise subprocess.TimeoutExpired('agently-cli',60)
    worker.tick(LocalApi(queued),['fake'],{'logged_in':True},runner)
    assert queued.history('monitor_outbox')['items'][0]['status']=='uncertain'
    assert worker.tick(LocalApi(queued),['fake'],{'logged_in':True},cli())==0


def test_stale_claim_becomes_uncertain(queued):
    job=agentmail.claim(queued);assert job
    with queued.connect() as c: c.execute('UPDATE monitor_outbox SET claimed_at=0')
    assert agentmail.claim(queued) is None
    assert queued.history('monitor_outbox')['items'][0]['status']=='uncertain'
    with pytest.raises(LookupError): agentmail.finish(queued,job['job'],{'returncode':0,'ok':True,'queued':True})


def test_docker_notifier_leaves_agentmail_for_host(queued):
    import asyncio
    asyncio.run(Notifier(queued).dispatch())
    assert queued.history('monitor_outbox')['items'][0]['status']=='pending'


def test_disabled_notification_never_invokes_cli(queued):
    site=queued.site('lecomte');site['notify_enabled']=False;queued.save_site(site)
    def runner(*a,**kw):raise AssertionError('must not send')
    worker.tick(LocalApi(queued),['fake'],{'logged_in':True},runner)
    assert queued.history('monitor_outbox')['items'][0]['status']=='held'


def test_html_escape_and_unsafe_links():
    event={'id':1,'kind':'NEW','payload':{'product':dict(product(1),name='<script>alert(1)</script>',url='javascript:alert(1)')}}
    output=render_html({'name':'<img onerror=x>'},event)
    assert '<script>' not in output and '<img' not in output and 'href="javascript:' not in output


def test_probe_reports_install_and_auth_without_sending():
    calls=[]
    def runner(command,args,cwd=None,timeout=None):
        calls.append(args)
        if args==['--version']: return SimpleNamespace(returncode=0,stdout='agently-cli version 1.0.18')
        if args==['auth','status']: return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'data':{'logged_in':True,'workspace':'default'}}))
        if args==['+me']: return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'data':{'daily_send_quota':50,'aliases':[{'email':'x@agent.qq.com','is_primary':True}]}}))
        raise AssertionError(args)
    report,command=worker.probe(runner,lambda:['cli.exe'])
    assert report['installed'] and report['logged_in'] and report['account']=='x@agent.qq.com' and report['quota']==50
    assert report['version']=='1.0.18' and all(a[0]!='message' for a in calls)


def test_probe_not_logged_in_and_missing_cli():
    def runner(command,args,cwd=None,timeout=None):
        if args==['auth','status']: return SimpleNamespace(returncode=0,stdout='tip: x\n'+json.dumps({'ok':True,'data':{'logged_in':False}}))
        return SimpleNamespace(returncode=0,stdout='agently-cli version 1.0.18')
    report,_=worker.probe(runner,lambda:['cli.exe'])
    assert report['installed'] and not report['logged_in'] and '重新授权 / 登录' in report['error']
    def missing(): raise FileNotFoundError('未找到 agently-cli')
    report,command=worker.probe(runner,missing)
    assert not report['installed'] and command is None


def test_http_api_token_status_test_mail_and_preview(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD','')
    app=create_app(tmp_path/'api.db',scheduled=False)
    with TestClient(app) as client:
        db=app.state.db
        site=db.site('lecomte');site.update(notification_channel='agentmail',email_to='a@example.test')
        db.save_site(site)
        assert client.post('/api/agentmail/worker/claim').status_code==403
        assert client.post('/api/agentmail/worker/claim',headers={'X-Agentmail-Token':'wrong'}).status_code==403
        token=agentmail.worker_token(db);h={'X-Agentmail-Token':token}
        assert client.get('/api/agentmail/status').json()['running'] is False
        client.post('/api/agentmail/worker/heartbeat',headers=h,json={'installed':True,'logged_in':True,'account':'s@agent.qq.com','version':'1.0.18','quota':50,'message':'ok'})
        status=client.get('/api/agentmail/status').json()
        assert status['running'] and status['ready'] and status['account']=='s@agent.qq.com' and status['cli']['quota']==50
        client.post('/api/agentmail/worker/heartbeat',headers=h,json={'installed':True,'logged_in':True,'probe_only':True})
        probed=client.get('/api/agentmail/status').json()
        assert probed['running'] is False and probed['last_seen'] and probed['cli']['installed']
        client.post('/api/agentmail/worker/heartbeat',headers=h,json={'installed':True,'logged_in':True,'account':'s@agent.qq.com'})
        # Nothing is queued until the admin explicitly confirms a test.
        assert client.post('/api/agentmail/worker/claim',headers=h).json()['job'] is None
        assert client.post('/api/agentmail/test',json={'site_id':'lecomte'}).status_code==400
        assert client.post('/api/agentmail/test',json={'site_id':'lecomte','confirm':True}).status_code==202
        assert client.post('/api/agentmail/test',json={'site_id':'lecomte','confirm':True}).status_code==409
        job=client.post('/api/agentmail/worker/claim',headers=h).json()['job']
        assert job['to']==['a@example.test'] and job['subject'].startswith('【测试】') and '测试邮件' in job['html']
        assert client.post('/api/agentmail/worker/result',headers=h,json={'job':job['job'],'result':{'returncode':0,'ok':True,'queued':True}}).json()['status']=='sent'
        assert client.get('/api/agentmail/status').json()['tests'][0]['status']=='sent'
        for kind in ['NEW','REMOVED','RETURNED','PRICE_DROP','PRICE_RISE','ACCESS_DENIED','TEST']:
            r=client.get(f'/api/mail/preview?kind={kind}&site_id=lecomte')
            assert r.status_code==200 and '<html' in r.text
        assert client.get('/api/mail/preview?kind=BAD').status_code==400


def test_admin_has_no_credits_link_and_agentmail_panel():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    assert 'credits.html' not in (root/'admin/index.html').read_text(encoding='utf-8')
    js=(root/'admin/app.js').read_text(encoding='utf-8')
    assert 'agentmail/test' in js and 'agentmail/auth' in js and '重新授权 / 登录' in js and 'mail/preview' in js


# ---- Re-authorization from the admin page -------------------------------------------------
class FakeProcess:
    def __init__(self, lines, code=None):
        import io
        self.stdout=io.BytesIO(''.join(lines).encode('utf-8')); self.code=code; self.killed=False
    def poll(self): return self.code
    def kill(self): self.killed=True; self.code=-9


def test_auth_flow_api_link_cancel_and_done(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD','')
    app=create_app(tmp_path/'auth.db',scheduled=False)
    with TestClient(app) as client:
        db=app.state.db;h={'X-Agentmail-Token':agentmail.worker_token(db)}
        assert client.post('/api/agentmail/worker/command',headers=h).json()['command'] is None
        flow=client.post('/api/agentmail/auth',json={'action':'login'}).json()['auth']
        assert flow['state']=='requested'
        assert client.post('/api/agentmail/auth',json={'action':'login'}).json()['auth']['id']==flow['id']  # double click
        assert client.post('/api/agentmail/worker/command',headers=h).json()['command']['id']==flow['id']
        assert client.post('/api/agentmail/worker/auth',headers={'X-Agentmail-Token':'bad'},json={}).status_code==403
        bad=client.post('/api/agentmail/worker/auth',headers=h,json={'id':flow['id'],'state':'waiting','url':'javascript:alert(1)','user_code':'<x>'}).json()['auth']
        assert bad['url']=='' and bad['user_code']==''
        client.post('/api/agentmail/worker/auth',headers=h,json={'id':flow['id'],'state':'waiting','url':'https://agent.qq.com/device?code=AB12','user_code':'AB12-CD34','message':'请点击'})
        status=client.get('/api/agentmail/status').json()
        assert status['auth']['url']=='https://agent.qq.com/device?code=AB12' and status['auth']['user_code']=='AB12-CD34'
        assert client.post('/api/agentmail/auth',json={'action':'logout'}).status_code==409
        assert client.post('/api/agentmail/auth',json={'action':'cancel'}).json()['auth']['cancel'] is True
        client.post('/api/agentmail/worker/auth',headers=h,json={'id':flow['id'],'state':'cancelled'})
        assert client.post('/api/agentmail/worker/command',headers=h).json()['command'] is None
        assert client.post('/api/agentmail/auth',json={'action':'bogus'}).status_code==422
        assert client.post('/api/agentmail/worker/auth',headers=h,json={'id':'old','state':'done'}).status_code==409


def test_worker_handle_auth_login_extracts_link_and_finishes(queued,monkeypatch):
    api=LocalApi(queued)
    api.post=lambda path,payload=None,_orig=api.post:(
        {'command':agentmail.worker_command(queued)} if path=='command' else
        {'auth':agentmail.worker_auth_update(queued,payload)} if path=='auth' else _orig(path,payload))
    agentmail.request_auth(queued,'login')
    procs=[]
    def popen(cmd,**kw):
        assert cmd[-2:]==['auth','login'] and not any(k.upper().startswith('CURSOR') for k in kw['env'])
        procs.append(FakeProcess(['请点击或复制以下链接在浏览器中完成授权：\n','https://agent.qq.com/oauth/device?user_code=WXYZ-1234\n','user code: WXYZ-1234\n']))
        return procs[-1]
    monkeypatch.setenv('CURSOR_AGENT','1')
    monkeypatch.setattr(worker.subprocess,'Popen',popen)
    monkeypatch.setattr(worker.AuthFlow.__init__,'__defaults__',(popen,))
    flow,changed=worker.handle_auth(api,['cli.exe'],None,{'logged_in':False})
    flow.reader.join(1)
    flow,changed=worker.handle_auth(api,['cli.exe'],flow,{'logged_in':False})
    state=agentmail.auth_flow(queued)
    assert state['state']=='waiting' and state['url']=='https://agent.qq.com/oauth/device?user_code=WXYZ-1234' and state['user_code']=='WXYZ-1234'
    procs[0].code=0
    flow,changed=worker.handle_auth(api,['cli.exe'],flow,{'logged_in':False})
    assert changed and flow  # main loop then re-probes and reports done with the account
    agentmail.worker_auth_update(queued,{'id':flow.id,'state':'done','account':'me@agent.qq.com','message':'已授权'})
    assert agentmail.auth_flow(queued)['account']=='me@agent.qq.com'


def test_worker_logout_and_env_cleanup(queued,monkeypatch):
    api=LocalApi(queued)
    api.post=lambda path,payload=None:({'command':agentmail.worker_command(queued)} if path=='command' else {'auth':agentmail.worker_auth_update(queued,payload)})
    agentmail.request_auth(queued,'logout')
    calls=[]
    monkeypatch.setenv('CODEX_CI','1')
    def fake_invoke(command,args,cwd=None,timeout=None):
        calls.append(args);assert 'CODEX_CI' not in worker.cli_env();return SimpleNamespace(returncode=0,stdout='{}')
    monkeypatch.setattr(worker,'invoke',fake_invoke)
    flow,changed=worker.handle_auth(api,['cli.exe'],None,{'logged_in':True})
    assert calls==[['auth','logout']] and changed and agentmail.auth_flow(queued)['state']=='done'


# ---- Email markup -------------------------------------------------------------------------
@pytest.mark.parametrize('kind',['NEW','REMOVED','RETURNED','PRICE_DROP','PRICE_RISE','ACCESS_DENIED','TEST'])
def test_email_markup_is_client_safe(kind):
    p=dict(product(7),brand="Arc'teryx",image='https://example.test/web/image/product.template/7/image_256')
    payload={'message':'站点拒绝访问 HTTP 403；监控已暂停'} if kind=='ACCESS_DENIED' else {} if kind=='TEST' else {'product':p,'old_price':{'PRICE_DROP':'12.50','PRICE_RISE':'8.00'}.get(kind)}
    out=render_html({'name':'站点'},{'id':9,'kind':kind,'at':'2026-10-10T08:00:00+00:00','payload':payload},{'total':119,'changes':3})
    assert '<style' not in out and '<script' not in out and '<link' not in out and 'max-width:600px' in out
    assert 'role="presentation"' in out and '#notifications' in out and '管理通知设置' in out
    if kind in ('NEW','RETURNED','PRICE_DROP','PRICE_RISE','REMOVED'):
        assert 'image_256' in out and '本次完整采集 119 件' in out
        assert '本封邮件共 1 条变化' in out
    else:
        assert '条变化' not in out
    assert '共 3 条变化' not in out
    if kind=='PRICE_DROP': assert '-20.0%' in out and 'line-through' in out and '降价' in out
    if kind=='PRICE_RISE': assert '涨价' in out


def test_digest_counts_only_events_in_this_email():
    events = [{'id':i,'kind':'NEW','payload':{'product':product(i)}} for i in (7, 8)]
    event = {'id':9,'kind':'DIGEST','payload':{'events':events}}
    out = render_html({'name':'站点'}, event, {'total':109,'changes':5})
    assert '本次完整采集 109 件' in out
    assert '本封邮件共 2 条变化' in out
    assert '本封邮件有 2 条变动' in out
    assert '共 5 条变化' not in out


def test_agentmail_digest_finishes_all_members(queued):
    queued.commit('lecomte', document([product(i) for i in range(1, 6)]))
    first = agentmail.claim(queued)
    agentmail.finish(queued, first['job'], {'returncode': 0, 'ok': True, 'queued': True})
    batch = agentmail.claim(queued)
    assert '3 条变动' in batch['subject']
    assert all(f'商品 {i}' in batch['html'] for i in range(3, 6))
    assert agentmail.claim(queued) is None
    agentmail.finish(queued, batch['job'], {'returncode': 0, 'ok': True, 'queued': True})
    assert {item['status'] for item in queued.history('monitor_outbox')['items']} == {'sent'}
