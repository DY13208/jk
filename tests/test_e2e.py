"""Opt-in full changedetection -> collector -> snapshot -> webhook test.

RUN_CD_INTEGRATION=1, CD_API_KEY and CD_URL required; run on host.
Docker Desktop reaches host fixture via host.docker.internal.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
import requests
import uvicorn
import yaml

from scripts.configure import payload
from monitor.store import Store
from tests.test_collector import card


@pytest.mark.skipif(os.getenv('RUN_CD_INTEGRATION') != '1', reason='Opt-in: requires live changedetection')
def test_real_changedetection_same_count_webhook(tmp_path, monkeypatch):
    state = {'start': 0, 'blocked': False, 'messages': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            n = int(parse_qs(urlsplit(self.path).query).get('page', ['1'])[0])
            self.send_response(403 if state['blocked'] else 200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            ids = range(state['start'] + (n - 1) * 30, state['start'] + n * 30)
            html = '<div id="total" data-count="90"></div>' + ''.join(card(i) for i in ids)
            html += f'<a class="next" href="/shop?page={n+1}">Next</a>' if n < 3 else '<div id="end"></div>'
            self.wfile.write(html.encode())

        def do_POST(self):
            state['messages'].append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

    fixture = ThreadingHTTPServer(('0.0.0.0', 8091), Handler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    cfg = yaml.safe_load(Path('config/example.yaml').read_text())['sites']['example']
    cfg['url'] = 'http://127.0.0.1:8091/shop?page=1'
    config_path = tmp_path / 'sites.yaml'
    config_path.write_text(yaml.safe_dump({'sites': {'integration': cfg}}))
    monkeypatch.setenv('CONFIG_PATH', str(config_path))
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'catalog.sqlite'))
    server = uvicorn.Server(uvicorn.Config('monitor.app:app', host='0.0.0.0', port=8092, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    session = requests.Session()
    session.headers['x-api-key'] = os.environ['CD_API_KEY']
    base = os.getenv('CD_URL', 'http://localhost:5000') + '/api/v1/watch'
    uuid = None

    def wait(predicate, timeout=50):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.5)
        raise AssertionError('Timed out waiting for integration checkpoint')

    try:
        wait(lambda: server.started)
        data = payload('integration', cfg, 'http://host.docker.internal:8092')
        data.update(paused=True, notification_muted=False,
                    notification_urls=['json://host.docker.internal:8091/notify'])
        response = session.post(base, json=data, timeout=20)
        assert response.status_code == 201, f'Create watch HTTP {response.status_code}'
        uuid = response.json()['uuid']

        def info():
            return session.get(f'{base}/{uuid}', timeout=15).json()

        def recheck():
            response = session.get(f'{base}/{uuid}', params={'recheck': '1'}, timeout=15)
            assert response.ok

        recheck()
        wait(lambda: info().get('history_n') == 1)
        assert not state['messages'], 'Baseline unexpectedly notified'
        assert len(Store(str(tmp_path / 'catalog.sqlite')).latest('integration')) == 90
        state['start'] = 2
        time.sleep(1.1) # upstream snapshot keys use seconds
        recheck()
        wait(lambda: info().get('history_n') == 2)
        wait(lambda: len(state['messages']) >= 1)
        text = json.dumps(state['messages'])
        assert text.count('added') == 2 and text.count('removed') == 2
        assert 'SKU-90' in text and 'SKU-91' in text and 'SKU-0' in text and 'SKU-1' in text
        db = Store(str(tmp_path / 'catalog.sqlite'))
        before = db.latest('integration')
        messages = len(state['messages'])
        checks = info()['check_count']
        state['blocked'] = True
        recheck()
        wait(lambda: info()['check_count'] > checks)
        assert info()['last_error']
        assert info()['history_n'] == 2
        assert db.latest('integration') == before
        assert len(state['messages']) == messages
        print('Real changedetection: baseline silent, 90->90 four changes delivered to local webhook, 403 retained baseline')
    finally:
        if uuid:
            session.delete(f'{base}/{uuid}', timeout=15)
        server.should_exit = True
        thread.join(timeout=10)
        fixture.shutdown()
        fixture.server_close()
