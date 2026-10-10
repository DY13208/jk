import json
import sqlite3
from types import SimpleNamespace
import pytest
from monitor.database import Database
from monitor.notifications import Notifier
from monitor.mail_templates import render_html
from scripts.agentmail_worker import deliver_one
from tests.test_monitor import product, document


@pytest.fixture
def queued(tmp_path):
    db=Database(tmp_path/'test.db');db.migrate()
    site=db.site('lecomte');site.update(notification_channel='agentmail',email_to='one@example.test',notify_enabled=True)
    db.save_site(site);db.commit('lecomte',document([product(1)]));db.commit('lecomte',document([product(1),product(2)]))
    conn=sqlite3.connect(db.path);conn.row_factory=sqlite3.Row
    yield db,conn
    conn.close()


def test_agentmail_cli_mock_delivery_and_dedup(queued):
    db,conn=queued;calls=[]
    def runner(command,args,cwd):
        from pathlib import Path
        calls.append(args)
        assert Path(cwd,'body.html').read_text(encoding='utf-8').find('商品 2')>=0
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'queued':True}))
    assert deliver_one(conn,['agently-cli'],runner)
    assert not deliver_one(conn,['agently-cli'],runner)
    assert len(calls)==1 and '--confirmed' in calls[0] and '--body-format' in calls[0]
    assert db.history('monitor_outbox')['items'][0]['status']=='sent'


@pytest.mark.parametrize('code,state',[(1,'uncertain'),(2,'held'),(3,'held'),(4,'uncertain'),(6,'held'),(7,'pending'),(8,'uncertain')])
def test_agentmail_exit_codes_and_secret_redaction(queued,code,state):
    db,conn=queued
    def runner(*args,**kw):return SimpleNamespace(returncode=code,stdout='SECRET_DO_NOT_LOG')
    deliver_one(conn,['fake'],runner)
    row=db.history('monitor_outbox')['items'][0]
    assert row['status']==state and 'SECRET' not in row['last_error']


def test_confirmation_response_is_not_success(queued):
    db,conn=queued
    def runner(*a,**kw):return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'data':{'confirmation_required':True}}))
    deliver_one(conn,['fake'],runner)
    assert db.history('monitor_outbox')['items'][0]['status']=='uncertain'


def test_docker_notifier_leaves_agentmail_for_host(queued):
    import asyncio
    db,conn=queued
    asyncio.run(Notifier(db).dispatch())
    assert db.history('monitor_outbox')['items'][0]['status']=='pending'


def test_disabled_notification_never_invokes_cli(queued):
    db,conn=queued;site=db.site('lecomte');site['notify_enabled']=False;db.save_site(site)
    def runner(*a,**kw):raise AssertionError('must not send')
    deliver_one(conn,['fake'],runner)
    assert db.history('monitor_outbox')['items'][0]['status']=='held'


def test_html_escape_and_unsafe_links():
    event={'id':1,'kind':'NEW','payload':{'product':dict(product(1),name='<script>alert(1)</script>',url='javascript:alert(1)')}}
    output=render_html({'name':'<img onerror=x>'},event)
    assert '<script>' not in output and '<img' not in output and 'href="javascript:' not in output
