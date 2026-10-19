import asyncio
import copy
import json
import shutil
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from crawl4ai_run.run_crawl4ai import CollectionError, collect, safe_launch_args, clean, money
from monitor.database import Database, EVENTS
from monitor.notifications import Notifier, message
from monitor.service import MonitorService
from monitor.web import create_app

ROOT = Path(__file__).resolve().parents[1]


def product(i, price='10.00'):
    return {'product_id':str(i),'name':f'商品 {i}','sale_price':price,'original_price':'20',
        'discount':'-50%','currency':'EUR','url':f'https://example.test/p-{i}'}


def document(products, started=None):
    return {'meta':{'tool':'crawl4ai','started':started or str(time.time_ns()),'ended':'now','run_tag':'test',
        'complete':True,'site_total':len(products),'site_pages':1,'extracted':len(products),
        'pages':[{'page':1,'status':200,'items':len(products)}]},'products':products}


@pytest.fixture
def db(tmp_path):
    value=Database(tmp_path/'snapshots.db'); value.migrate()
    # A second, disabled site for multi-site tests (no longer seeded by migrate()).
    with value.connect() as c:
        c.execute('INSERT INTO monitor_sites(id,name,url,adapter,event_rules,created_at) VALUES(?,?,?,?,?,?)',
            ('site2','Site 2','','css_catalog',json.dumps(EVENTS),'2026-01-01T00:00:00+00:00'))
    return value


def enable(db, site_id='lecomte', notify=False):
    site=db.site(site_id)
    site.update(enabled=True,permission_confirmed=True,policy_note='测试环境已授权',
        notify_enabled=notify,webhook_env='WECOM_TEST_WEBHOOK')
    db.save_site(site)
    return site


def test_same_ninety_two_new_two_removed(db):
    enable(db,notify=True)
    first=document([product(i) for i in range(90)])
    assert db.commit('lecomte',first)[1]==[]
    assert db.history('monitor_outbox')['total']==0
    second=document([product(i) for i in range(2,92)])
    rid,events=db.commit('lecomte',second)
    assert {(e['kind'],e['product_id']) for e in events}=={('NEW','90'),('NEW','91'),('REMOVED','0'),('REMOVED','1')}
    assert len(db.products('lecomte'))==90
    assert db.history('monitor_outbox')['total']==4
    assert db.commit('lecomte',second)==(rid,[])
    assert db.history('monitor_outbox')['total']==4


def test_return_price_direction_and_site_identity(db):
    db.commit('lecomte',document([product(1),product(2)]))
    db.commit('site2',document([product(1,'99')]))
    assert db.commit('lecomte',document([product(2,'9')]))[1][1]['kind']=='PRICE_DROP'
    events=db.commit('lecomte',document([product(1),product(2,'11')]))[1]
    assert {e['kind'] for e in events}=={'RETURNED','PRICE_RISE'}
    assert db.products('site2')[0]['sale_price']=='99.00'
    assert not db.commit('lecomte',document([product(2,'11.0'),product(1,'10.0')]))[1]


@pytest.mark.parametrize('bad',['403','429','count','duplicate','missingpage','incomplete','missingprice','nan'])
def test_invalid_snapshot_never_changes_baseline(db,bad):
    db.commit('lecomte',document([product(1),product(2)]))
    d=document([product(1)])
    if bad in ('403','429'): d['meta']['pages'][0]['status']=int(bad)
    elif bad=='count': d['meta']['site_total']=2
    elif bad=='duplicate': d=document([product(1),product(1)])
    elif bad=='missingpage': d['meta']['site_pages']=2
    elif bad=='incomplete': d['meta']['complete']=False
    elif bad=='missingprice': d['products'][0]['sale_price']=None
    elif bad=='nan': d['products'][0]['sale_price']='NaN'
    with pytest.raises(CollectionError): db.commit('lecomte',d)
    assert len(db.products('lecomte'))==2
    assert db.history('monitor_events')['total']==0


def test_migrate_real_legacy_keeps_all_records(tmp_path):
    source=ROOT/'legacy'/'snapshots.original.db'
    target=tmp_path/'data'/'snapshots.db';target.parent.mkdir()
    shutil.copy2(source,target)
    shutil.copytree(ROOT/'legacy'/'runs',tmp_path/'legacy'/'runs')
    before=sqlite3.connect(source)
    old_runs=before.execute('SELECT * FROM runs').fetchall()
    old_snapshots=before.execute('SELECT * FROM snapshots').fetchall()
    old_changes=before.execute('SELECT * FROM changes').fetchall();before.close()
    db=Database(target);db.migrate();db.migrate()
    with db.connect() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM runs')]==old_runs
        assert [tuple(r) for r in c.execute('SELECT * FROM snapshots')]==old_snapshots
        assert [tuple(r) for r in c.execute('SELECT * FROM changes')]==old_changes
        assert c.execute('SELECT COUNT(*) FROM monitor_run_meta').fetchone()[0]==3
    assert len(db.products('lecomte'))==119
    assert db.history('monitor_events')['total']==0
    assert len(list(target.parent.glob('backups/before-migration-*.db')))==1
    backup=db.backup('manual')
    c=sqlite3.connect(target.parent/'backups'/backup)
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok';c.close()


def test_database_lease_nonreentrant_and_expired(db):
    assert db.acquire('lecomte','a')
    assert not db.acquire('lecomte','b')
    db.release('lecomte','b')
    assert not db.acquire('lecomte','b')
    db.release('lecomte','a')
    assert db.acquire('lecomte','b',seconds=-1)
    assert db.acquire('lecomte','c')


def test_windows_mount_chmod_unsupported(tmp_path,monkeypatch):
    def denied(*args): raise PermissionError('POSIX chmod unsupported')
    monkeypatch.setattr('monitor.database.os.chmod',denied)
    db=Database(tmp_path/'test.db');db.migrate()
    db.commit('lecomte',document([product(1)]))
    assert db.products('lecomte')[0]['product_id']=='1'
    assert db.backup('manual')


@pytest.mark.parametrize('status',[403,429])
def test_denied_pauses_and_alerts_without_removal(db,status):
    enable(db,notify=True)
    db.commit('lecomte',document([product(1)]))
    async def denied(site): raise CollectionError(f'HTTP {status}',status)
    service=MonitorService(db,{'odoo_lecomte':denied})
    result=asyncio.run(service.run('lecomte'))
    assert result['status']=='FAILED'
    assert not db.site('lecomte')['enabled']
    assert len(db.products('lecomte'))==1
    events=db.history('monitor_events')['items']
    assert [e['kind'] for e in events]==['ACCESS_DENIED']
    assert db.history('monitor_outbox')['total']==0


def test_task_timeout_and_overlap(db):
    enable(db)
    async def check():
        gate=asyncio.Event()
        async def adapter(site):
            await gate.wait();return document([product(1)])
        service=MonitorService(db,{'odoo_lecomte':adapter})
        first=asyncio.create_task(service.run('lecomte'))
        await asyncio.sleep(.01)
        assert (await service.run('lecomte'))['status']=='BUSY'
        gate.set();assert (await first)['status']=='SUCCESS'
        assert not service.active
    asyncio.run(check())


def test_notification_mock_and_dedup(db,monkeypatch):
    enable(db,notify=True)
    monkeypatch.setenv('WECOM_TEST_WEBHOOK','https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=TEST_ONLY')
    db.commit('lecomte',document([product(1)]));db.commit('lecomte',document([product(1),product(2)]))
    calls=[]
    async def sender(url,payload):
        calls.append(payload);return httpx.Response(200,json={'errcode':0})
    notifier=Notifier(db,sender)
    asyncio.run(notifier.dispatch());asyncio.run(notifier.dispatch())
    assert len(calls)==1
    content=calls[0]['text']['content']
    assert all(v in content for v in ('新品上架','商品 2','10.00','-50%','https://example.test/p-2'))
    assert db.history('monitor_outbox')['items'][0]['status']=='sent'


@pytest.mark.parametrize('scenario',['timeout','rate','missing','disabled','invalid_response'])
def test_notification_failure_safe_states(db,monkeypatch,scenario):
    enable(db,notify=True)
    if scenario!='missing': monkeypatch.setenv('WECOM_TEST_WEBHOOK','https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=TEST_ONLY')
    db.commit('lecomte',document([]));db.commit('lecomte',document([product(1)]))
    if scenario=='disabled':
        site=db.site('lecomte');site['notify_enabled']=False;db.save_site(site)
    calls=[]
    async def sender(url,payload):
        calls.append(1)
        if scenario=='timeout': raise httpx.ReadTimeout('no response')
        return httpx.Response(429 if scenario=='rate' else 200,json={} if scenario=='invalid_response' else {'errcode':0})
    n=Notifier(db,sender);asyncio.run(n.dispatch());asyncio.run(n.dispatch())
    expected='uncertain' if scenario=='timeout' else 'pending' if scenario=='rate' else 'held'
    assert db.history('monitor_outbox')['items'][0]['status']==expected
    assert len(calls)==(0 if scenario in ('missing','disabled') else 1)


class FakeCrawler:
    def __init__(self,pages): self.pages=iter(pages)
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def arun(self,**kwargs): return next(self.pages)


def page(ids,status=200,total=4,links=True):
    html='<div class="tp-product-pager" data-page-count="2" data-total-count="%s"></div>'%total
    if links: html+='<a href="/shop/page/2?brand=x">2</a>'
    items=[{'product_template_id':str(i),'name':f'P{i}','sale_price':'10.00','url':f'/p-{i}'} for i in ids]
    return SimpleNamespace(status_code=status,success=status==200,html=html,extracted_content=json.dumps(items))


@pytest.mark.parametrize('scenario',['complete','403','429','partial','duplicates','missing_links','changing_total'])
def test_crawler_pagination(scenario):
    pages=[page([1,2]),page([3,4])]
    if scenario in ('403','429'): pages[1]=page([],int(scenario))
    elif scenario=='partial': pages[1]=page([3])
    elif scenario=='duplicates': pages[1]=page([2,3])
    elif scenario=='missing_links': pages[0]=page([1,2],links=False)
    elif scenario=='changing_total': pages[1]=page([3,4],total=5)
    async def run(): return await collect({'url':'https://example.test/shop?brand=x','page_delay':0},
        factory=lambda _:FakeCrawler(pages),run_config_factory=lambda:None)
    if scenario=='complete':
        d=asyncio.run(run());assert len(d['products'])==4 and d['meta']['complete']
    else:
        with pytest.raises(CollectionError): asyncio.run(run())


def test_no_stealth_flags_and_odoo_identity():
    args=safe_launch_args({'headless':False,'args':['--disable-blink-features=AutomationControlled','--ignore-certificate-errors','--window-size=1440,1000']})
    assert args['args']==['--window-size=1440,1000']
    with pytest.raises(CollectionError): clean({'product_template_id':'2','url':'/p-3'},1)


def test_redirect_requires_final_success():
    first=page([1,2]);first.status_code=303;first.redirected_status_code=200
    first.redirected_url='https://example.test/en/shop?brand=x'
    async def run():
        return await collect({'url':'https://example.test/shop?brand=x','page_delay':0},
            factory=lambda _:FakeCrawler([first,page([3,4])]),run_config_factory=lambda:None)
    assert len(asyncio.run(run())['products'])==4
    first.redirected_status_code=None
    with pytest.raises(CollectionError): asyncio.run(run())


def test_robots_denial_pauses_without_traversal():
    denied=page([]);denied.success=False;denied.status_code=None;denied.error_message='Access denied by robots.txt'
    async def run():
        await collect({'url':'https://example.test/shop?brand=x'},factory=lambda _:FakeCrawler([denied]),run_config_factory=lambda:None)
    with pytest.raises(CollectionError) as error: asyncio.run(run())
    assert error.value.status==403


@pytest.mark.parametrize('value,expected',[('143,96','143.96'),('1,299.95','1299.95'),('1.299,95','1299.95'),('1 299,95','1299.95'),('143.96','143.96')])
def test_locale_prices(value,expected):
    assert money(value)==expected


def test_admin_routes_permission_csrf_and_config(tmp_path,monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD',raising=False)
    with TestClient(create_app(tmp_path/'test.db',scheduled=False)) as client:
        assert client.get('/').status_code==200
        for path in ('overview','sites','products','events','logs','notifications'):
            assert client.get('/api/'+path).status_code==200
        site=client.get('/api/sites').json()['items'][0]
        site['enabled']=True
        assert client.put('/api/sites/lecomte',json=site).status_code==422
        site.update(permission_confirmed=True,policy_note='')
        assert client.put('/api/sites/lecomte',json=site,headers={'Origin':'https://evil.test'}).status_code==403
        assert client.put('/api/sites/lecomte',json=site).status_code==200
        assert client.post('/api/backups').status_code==200
        assert client.put('/api/sites/site2',json={'id':'site2','name':'Site 2','adapter':'css_catalog'}).status_code==200
        assert client.post('/api/sites/site2/run').status_code==409


def test_admin_auth(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD','test-password')
    with TestClient(create_app(tmp_path/'test.db',scheduled=False)) as client:
        assert client.get('/api/sites').status_code==401
        assert client.get('/api/sites',auth=('admin','test-password')).status_code==200
        assert client.get('/health').status_code==200


def test_existing_catalog_scope_cannot_silently_change(db):
    db.commit('lecomte',document([product(1)]))
    site=db.site('lecomte');site['url']='https://example.test/other'
    with pytest.raises(ValueError): db.save_site(site)


def test_rule_filter_preserves_history_and_old_diff(db):
    site=enable(db,notify=True);site['event_rules']=['NEW'];db.save_site(site)
    db.commit('lecomte',document([product(1),product(2)]))
    db.commit('lecomte',document([product(2,'9'),product(3)]))
    assert db.history('monitor_events')['total']==3
    assert db.history('monitor_outbox')['total']==1
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM changes').fetchone()[0]==3


def test_pause_during_capture_discards_result(db):
    enable(db)
    async def adapter(site):
        site['enabled']=False;db.save_site(site)
        return document([product(1)])
    s=MonitorService(db,{'odoo_lecomte':adapter})
    assert asyncio.run(s.run('lecomte'))['status']=='CANCELLED'
    assert db.products('lecomte')==[]


def test_scheduler_independent_intervals_and_no_reset(db):
    enable(db)
    second=enable(db,'site2');second.update(url='https://example.test/shop',adapter='odoo_lecomte',interval_minutes=30)
    db.save_site(second)
    async def run():
        from datetime import datetime,timezone,timedelta
        async def adapter(site): return document([product(1)])
        service=MonitorService(db,{'odoo_lecomte':adapter})
        service.start()
        a=service.scheduler.get_job('site:lecomte');b=service.scheduler.get_job('site:site2')
        assert a.trigger.interval.total_seconds()==900
        assert b.trigger.interval.total_seconds()==1800
        assert a.max_instances==1 and a.coalesce
        next_at=a.next_run_time;service.sync_jobs()
        assert service.scheduler.get_job(a.id).next_run_time==next_at
        service.scheduler.modify_job(a.id,next_run_time=datetime.now(timezone.utc)+timedelta(milliseconds=10))
        await asyncio.sleep(.15)
        assert len(db.products('lecomte'))==1
        assert db.products('site2')==[]
        await service.stop()
    asyncio.run(run())
