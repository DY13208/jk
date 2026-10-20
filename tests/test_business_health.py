import asyncio
from datetime import datetime, timedelta, timezone

from monitor.business_health import business_health
from monitor.service import MonitorService, describe
from tests.test_monitor import db, enable, document, product


def test_health_staleness_offline_and_notifications(db):
    site = enable(db)
    site['adapter_config']['execution_mode'] = 'extension'
    db.save_site(site)
    service = MonitorService(db)
    health = business_health(db, service)
    assert not health['ok']
    row = next(s for s in health['sites'] if s['site_id'] == site['id'])
    assert '采集端未连接' in row['reasons']
    assert '尚无成功采集记录' in row['reasons']
    log = db.log_start(site['id'])
    db.log_finish(log, 'SUCCESS', 'test')
    from monitor.host_collector import heartbeat
    heartbeat(db, 'extension')
    assert business_health(db, service)['ok']
    with db.connect() as conn:
        conn.execute('UPDATE monitor_logs SET ended=? WHERE id=?',
                     ((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(), log))
    assert '采集数据已过期' in next(s for s in business_health(db, service)['sites'] if s['site_id'] == site['id'])['reasons']


def test_slow_mode_does_not_block_other_modes(db):
    enable(db)
    second = enable(db, 'site2')
    second['adapter_config']['execution_mode'] = 'flaresolverr'
    db.save_site(second)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(site):
            entered.set()
            await release.wait()
            return document([product(1)])

        async def fast(site):
            return document([product(2)])

        service = MonitorService(db, {'flaresolverr_fetch':slow, 'odoo_lecomte':fast})
        task = asyncio.create_task(service.run('site2'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert (await asyncio.wait_for(service.run('lecomte'), 1))['status'] == 'SUCCESS'
            assert not task.done()
            assert service.progress['site2']['state'] == 'running'
        finally:
            release.set()
            await task
        assert not service.progress

    asyncio.run(scenario())


def test_timeout_message_matches_mode_limit():
    assert '1500 秒' in describe(TimeoutError(), 1500)
    assert '300 秒' in describe(TimeoutError())


def test_same_mode_queues_and_cancellation_cleans_progress(db):
    enable(db)
    enable(db, 'site2')

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(site):
            entered.set()
            await release.wait()
            return document([product(1)])

        service = MonitorService(db, {'odoo_lecomte':slow, 'css_catalog':slow})
        first = asyncio.create_task(service.run('lecomte'))
        second = None
        try:
            await entered.wait()
            second = asyncio.create_task(service.run('site2'))
            await asyncio.sleep(0)
            assert service.progress['site2']['state'] == 'queued'
            row = next(s for s in business_health(db, service)['sites'] if s['site_id'] == 'site2')
            assert row['task']['waiting_seconds'] >= 0
            assert (await service.run('site2'))['status'] == 'BUSY'
        finally:
            if second:
                second.cancel()
                await asyncio.gather(second, return_exceptions=True)
            release.set()
            await first
        assert not service.active and not service.progress

    asyncio.run(scenario())


def test_health_endpoint_keeps_admin_auth_and_liveness(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from monitor.web import create_app
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-only-password')
    with TestClient(create_app(tmp_path/'status.db', scheduled=False)) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/health').status_code == 401
        response = client.get('/api/health', auth=('admin', 'test-only-password'))
        assert response.status_code == 200
        assert 'workers' in response.json()
