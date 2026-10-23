from fastapi.testclient import TestClient

from monitor.web import create_app


def test_admin_html_is_no_cache_but_assets_and_api_are_untouched(tmp_path, monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    with TestClient(create_app(tmp_path/'c.db', scheduled=False)) as c:
        index = c.get('/')
        assert index.status_code == 200 and index.headers['cache-control'] == 'no-cache'
        assert '/assets/app.js?v=' in index.text
        js = c.get('/assets/app.js')
        assert js.status_code == 200 and 'cache-control' not in js.headers
        assert 'cache-control' not in c.get('/health').headers
