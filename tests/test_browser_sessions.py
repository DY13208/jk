import asyncio
import json
from types import SimpleNamespace

import pytest
from monitor.web import SiteInput
from crawl4ai_run.run_crawl4ai import session_state_path, save_session_state
from tests.test_monitor import db, document, product


def test_session_paths_are_isolated_and_app_owned(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'snapshots.db'))
    first=session_state_path('https://example.test/shop?brand=1')
    assert first.parent==tmp_path/'browser-sessions'
    assert first==session_state_path('https://example.test/shop?brand=1')
    assert first!=session_state_path('https://example.test/shop?brand=2')
    assert len(first.stem)==64


def test_session_state_saved_atomically(tmp_path):
    class Context:
        async def storage_state(self): return {'cookies':[],'origins':[]}
    crawler=SimpleNamespace(crawler_strategy=SimpleNamespace(browser_manager=SimpleNamespace(
        sessions={'catalog':(Context(),None,0)})))
    target=tmp_path/'sessions'/'state.json'
    asyncio.run(save_session_state(crawler,target))
    assert json.loads(target.read_text())=={'cookies':[],'origins':[]}
    assert list(target.parent.glob('*.tmp'))==[]


def test_operational_session_options_preserve_product_baseline(db):
    db.commit('lecomte',document([product(1)]))
    site=db.site('lecomte')
    site['adapter_config']={'persistent_session':True,'page_delay':10}
    db.save_site(site)
    assert len(db.products('lecomte'))==1
    site['adapter_config']['schema']={'changed':True}
    with pytest.raises(ValueError): db.save_site(site)


@pytest.mark.parametrize('settings',[{'persistent_session':'false'},{'page_delay':False},
    {'page_delay':0},{'page_delay':31},{'page_delay':float('nan')}])
def test_session_settings_validation(settings):
    with pytest.raises(ValueError): SiteInput(id='example',name='Example',adapter_config=settings)
