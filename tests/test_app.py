import yaml
from fastapi.testclient import TestClient
from monitor import app as module
from monitor.store import Incomplete, Store
from tests.test_store import product


def test_success_failure_and_disabled(tmp_path, monkeypatch):
    config = tmp_path / 'sites.yaml'
    config.write_text(yaml.safe_dump({'sites': {'one': {'enabled': True}, 'two': {'enabled': False}}}))
    monkeypatch.setenv('CONFIG_PATH', str(config))
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'db.sqlite'))

    async def complete(_):
        return [product(1), product(2)], {'complete': True, 'expected': 2}

    monkeypatch.setattr(module, 'collect', complete)
    with TestClient(module.app) as client:
        response = client.get('/sites/one/feed')
        assert response.status_code == 200 and response.text == 'catalog-events-v1\n'
        assert client.get('/sites/two/feed').status_code == 503
        assert client.get('/sites/missing/feed').status_code == 404

        async def failed(_):
            raise Incomplete('HTTP 403; stopped')

        monkeypatch.setattr(module, 'collect', failed)
        assert client.get('/sites/one/feed').status_code == 503
        assert len(client.get('/sites/one/latest').json()['products']) == 2
        db = Store(str(tmp_path / 'db.sqlite'))
        assert db.feed('one') == 'catalog-events-v1\n'
        with db.connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 1
            assert conn.execute('SELECT reason FROM failures').fetchone()[0] == 'HTTP 403; stopped'
