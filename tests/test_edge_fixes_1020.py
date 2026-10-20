"""Regression tests for the three 2026-10-20 edge bugs (each failed before its fix)."""
import asyncio

import pytest

from monitor import agentmail
from monitor.business_health import business_health
from monitor.service import MonitorService
from tests.test_agentmail import queued  # noqa: F401 (fixture)
from tests.test_monitor import db, enable, document, product  # noqa: F401

OK = {'returncode': 0, 'ok': True, 'queued': True}


# ---- 1. execution mode switched while the job is queued ------------------------------------------------
def test_mode_switch_while_queued_never_overlaps_same_mode(db):
    enable(db)                                   # lecomte: docker, holds the docker slot
    third = dict(db.site('site2'), id='site3'); db.save_site(third)
    for sid in ('site2', 'site3'):
        s = enable(db, sid); s.update(url='https://shop.test/'+sid); db.save_site(s)
    s = db.site('site3'); s['adapter_config'] = {'execution_mode': 'flaresolverr'}; db.save_site(s)

    async def scenario():
        docker_go, fs_go, fs_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        state = {'fs': 0, 'max_fs': 0, 'docker_ran': []}

        async def slow_docker(site):
            state['docker_ran'].append(site['id']); await docker_go.wait(); return document([product(1)])

        async def flaresolverr(site):
            state['fs'] += 1; state['max_fs'] = max(state['max_fs'], state['fs']); fs_entered.set()
            try: await fs_go.wait()
            finally: state['fs'] -= 1
            return document([product(2)])

        service = MonitorService(db, {'odoo_lecomte': slow_docker, 'css_catalog': slow_docker, 'flaresolverr_fetch': flaresolverr})
        a = asyncio.create_task(service.run('lecomte'))      # holds docker slot
        await asyncio.sleep(0.05)
        b = asyncio.create_task(service.run('site2'))        # queued on the docker slot
        c = asyncio.create_task(service.run('site3'))        # running in the flaresolverr slot
        await asyncio.wait_for(fs_entered.wait(), 1)
        moved = db.site('site2'); moved['adapter_config'] = {'execution_mode': 'flaresolverr'}; db.save_site(moved)
        docker_go.set()                                      # docker slot frees up while site3 still runs
        await asyncio.wait_for(a, 1)
        await asyncio.sleep(0.1)
        assert state['max_fs'] == 1, 'two flaresolverr jobs ran concurrently'
        assert 'site2' not in state['docker_ran'], 'switched job ran through the old mode'
        fs_go.set()
        results = await asyncio.wait_for(asyncio.gather(b, c), 2)
        assert [r['status'] for r in results] == ['SUCCESS', 'SUCCESS']
        assert state['max_fs'] == 1 and not service.progress

    asyncio.run(scenario())


def test_queued_job_uses_timeout_of_the_mode_it_runs(db, monkeypatch):
    """The lock/timeout must belong to the mode that runs (flaresolverr: 1500 s), not the one queued under."""
    enable(db)
    s = enable(db, 'site2'); s['url'] = 'https://shop.test/x'; db.save_site(s)
    seen = []
    original = db.acquire
    monkeypatch.setattr(db, 'acquire', lambda site_id, owner, seconds=360: (seen.append((site_id, seconds)), original(site_id, owner, seconds))[1])

    async def scenario():
        go = asyncio.Event()
        async def blocker(site): await go.wait(); return document([product(1)])
        async def fs(site): return document([product(2)])
        service = MonitorService(db, {'odoo_lecomte': blocker, 'css_catalog': blocker, 'flaresolverr_fetch': fs})
        a = asyncio.create_task(service.run('lecomte')); await asyncio.sleep(0.05)
        b = asyncio.create_task(service.run('site2')); await asyncio.sleep(0.05)
        moved = db.site('site2'); moved['adapter_config'] = {'execution_mode': 'flaresolverr'}; db.save_site(moved)
        go.set(); await asyncio.gather(a, b)
    asyncio.run(scenario())
    assert ('site2', 1560) in seen


# ---- 2. Agent Mail: a late receipt from an older attempt -----------------------------------------------
def test_stale_receipt_from_previous_attempt_is_ignored(queued):
    first = agentmail.claim(queued)
    assert agentmail.finish(queued, first['job'], {'returncode': 7})['status'] == 'pending'   # rate limited -> retry
    with queued.connect() as c: c.execute("UPDATE monitor_outbox SET next_at=0")
    second = agentmail.claim(queued)
    assert second['job'] != first['job']
    with pytest.raises(LookupError): agentmail.finish(queued, first['job'], OK)     # late receipt of attempt 1
    assert queued.history('monitor_outbox')['items'][0]['status'] == 'sending'
    assert agentmail.finish(queued, second['job'], {'returncode': 1})['status'] == 'uncertain'  # current attempt wins


def test_stale_receipt_after_manual_retry_is_ignored(queued):
    first = agentmail.claim(queued)
    agentmail.finish(queued, first['job'], {'returncode': 1})                          # uncertain
    with queued.connect() as c:                                                        # admin "retry" resets attempts
        c.execute("UPDATE monitor_outbox SET status='pending',attempts=0,next_at=0,last_error=''")
    second = agentmail.claim(queued)
    with queued.connect() as c: c.execute("UPDATE monitor_outbox SET status='sending'")  # still in flight
    with pytest.raises(LookupError): agentmail.finish(queued, first['job'], OK)
    assert agentmail.finish(queued, second['job'], OK)['status'] == 'sent'


def test_legacy_job_id_without_attempt_still_finishes_first_attempt(queued):
    job = agentmail.claim(queued)
    legacy = ':'.join(job['job'].split(':')[:2])
    assert agentmail.finish(queued, legacy, OK)['status'] == 'sent'


# ---- 3. health ignores missing notification channel configuration -------------------------------------
def healthy_site(db, **changes):
    site = enable(db, notify=True); site.update(changes); site['adapter_config'] = {**site['adapter_config'], 'execution_mode': 'docker'}
    db.save_site(site)
    log = db.log_start(site['id']); db.log_finish(log, 'SUCCESS', 'ok')
    return site


@pytest.mark.parametrize('changes', [
    {'notification_channel': 'email', 'email_to': 'me@example.test'},
    {'notification_channel': 'wecom', 'webhook_env': 'WECOM_TEST_WEBHOOK'},
    {'notification_channel': 'bark', 'webhook_env': 'BARK_KEY'},
    {'notification_channel': 'ntfy', 'webhook_env': 'NTFY_TOPIC'},
    {'notification_channel': 'pushplus', 'webhook_env': 'PUSHPLUS_LECOMTE_TOKEN'},
    {'notification_channel': 'agentmail', 'email_to': 'me@example.test'},
])
def test_missing_channel_config_is_an_issue(db, monkeypatch, changes):
    for name in ('SMTP_HOST', 'SMTP_FROM', 'WECOM_TEST_WEBHOOK', 'BARK_KEY', 'NTFY_TOPIC', 'PUSHPLUS_LECOMTE_TOKEN'):
        monkeypatch.delenv(name, raising=False)
    healthy_site(db, **changes)
    health = business_health(db, MonitorService(db))
    assert health['ok'] is False
    reasons = next(s for s in health['sites'] if s['site_id'] == 'lecomte')['reasons']
    assert any(r.startswith('通知未配置') for r in reasons), reasons


def test_configured_channel_is_healthy_and_secret_not_reported(db, monkeypatch):
    monkeypatch.setenv('BARK_KEY', 'SecretDeviceKey123')
    healthy_site(db, notification_channel='bark', webhook_env='BARK_KEY')
    health = business_health(db, MonitorService(db))
    assert health['ok'] is True and 'SecretDeviceKey123' not in str(health)


def test_weixin_not_ready_is_an_issue(db):
    healthy_site(db, notification_channel='weixin')
    assert business_health(db, MonitorService(db), {'ready': False})['ok'] is False
    assert business_health(db, MonitorService(db), {'ready': True})['ok'] is True


def test_notify_disabled_site_is_not_flagged(db, monkeypatch):
    monkeypatch.delenv('SMTP_HOST', raising=False)
    healthy_site(db, notification_channel='email', email_to='me@example.test', notify_enabled=False)
    assert business_health(db, MonitorService(db))['ok'] is True


def test_overview_api_reports_channel_issue(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from monitor.web import create_app
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False); monkeypatch.delenv('SMTP_HOST', raising=False)
    with TestClient(create_app(tmp_path/'o.db', scheduled=False)) as client:
        healthy_site(client.app.state.db, notification_channel='email', email_to='me@example.test')
        assert client.get('/api/overview').json()['business_health']['ok'] is False
