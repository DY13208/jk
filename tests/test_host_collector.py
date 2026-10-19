import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from monitor import host_collector as host
from monitor.web import create_app
from tests.test_monitor import db, enable, document, product
from crawl4ai_run.run_crawl4ai import CollectionError


def test_host_round_trip_and_duplicate_result_rejected(db):
    site=enable(db);host.heartbeat(db)
    async def run():
        task=asyncio.create_task(host.collect(db,site))
        await asyncio.sleep(0)
        job=host.claim(db)
        assert job['site']['url']==site['url']
        assert host.claim(db) is None
        body={**job,'result':{'document':document([product(1)])}}
        with pytest.raises(LookupError): host.finish(db,{**body,'claim':'incorrect'})
        assert host.finish(db,body)=={'ok':True}
        with pytest.raises(LookupError): host.finish(db,body)
        result=await task
        assert len(result['products'])==1
    asyncio.run(run())
    assert db.history('monitor_events')['total']==0  # transport never commits snapshots itself


def test_host_failure_and_cancellation_do_not_commit(db):
    site=enable(db);host.heartbeat(db)
    async def run():
        task=asyncio.create_task(host.collect(db,site));await asyncio.sleep(0)
        job=host.claim(db)
        host.finish(db,{**job,'result':{'error':'HTTP 403','status':403}})
        with pytest.raises(CollectionError) as error: await task
        assert error.value.status==403
        task=asyncio.create_task(host.collect(db,site));await asyncio.sleep(0)
        job=host.claim(db);task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        with pytest.raises(LookupError): host.finish(db,{**job,'result':{'document':document([product(1)])}})
    asyncio.run(run())
    assert db.products('lecomte')==[]


def test_host_stale_heartbeat_and_claim_nonce(db):
    with pytest.raises(CollectionError): asyncio.run(host.collect(db,db.site('lecomte')))
    host.heartbeat(db)
    assert host.status(db)['running']
    with db.connect() as c: c.execute("UPDATE monitor_meta SET value='0' WHERE key='collector_heartbeat:host'")
    assert not host.status(db)['running']


def test_host_worker_api_authentication_and_invalid_results(tmp_path):
    with TestClient(create_app(tmp_path/'db.sqlite',scheduled=False)) as client:
        assert client.post('/api/collector/worker/claim').status_code==403
        token=(tmp_path/'collector-worker.token').read_text().strip()
        headers={'x-collector-token':token}
        assert client.post('/api/collector/worker/heartbeat',headers=headers).status_code==200
        assert client.get('/api/collector/status').json()['running']
        assert client.post('/api/collector/worker/result',headers=headers,json={}).status_code==400
        assert client.post('/api/collector/worker/result',headers=headers,json={'id':'old','claim':'wrong','result':{'error':'failed'}}).status_code==409


def test_paused_and_expired_host_jobs_never_claimed(db):
    with db.connect() as c:
        for job,created in [('paused',time.time()),('expired',0)]:
            c.execute('INSERT INTO monitor_host_jobs(id,site_id,created,status,payload) VALUES(?,?,?,\'pending\',?)',
                      (job,'lecomte',created,json.dumps({})))
    assert host.claim(db) is None
    enable(db)
    with db.connect() as c: c.execute("DELETE FROM monitor_host_jobs WHERE id='paused'")
    assert host.claim(db) is None

@pytest.mark.parametrize('mode',['host','extension'])
def test_invalid_snapshot_is_terminal_not_delivery_retry(db,mode):
    site=enable(db);site['adapter_config']['execution_mode']=mode;db.save_site(site)
    db.commit(site['id'],document([product(1)]));host.heartbeat(db,mode)
    async def run():
        task=asyncio.create_task(host.collect(db,site));await asyncio.sleep(0)
        job=host.claim(db,mode)
        invalid={'capture':{'checkpoints':[]}} if mode=='extension' else {'document':{'meta':{},'products':[]}}
        result=host.finish(db,{**job,'result':invalid},mode)
        assert result['ok'] and result['rejected'] and result['error']
        with pytest.raises(CollectionError):await task
        with pytest.raises(LookupError):host.finish(db,{**job,'result':invalid},mode)
        assert len(db.products(site['id']))==1
        assert db.history('monitor_events')['total']==0
        assert db.history('monitor_outbox')['total']==0
    asyncio.run(run())
