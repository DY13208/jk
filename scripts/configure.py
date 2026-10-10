"""Idempotently provision changedetection watches; no independent scheduler."""
import os
from pathlib import Path

import requests
import yaml


def payload(site, cfg, catalog):
    urls = []
    for variable in cfg.get('notification_env', []):
        value = os.getenv(variable)
        if not value:
            raise ValueError(f'Missing notification environment variable: {variable}')
        urls.append(value)
    return {
        'url': f'{catalog}/sites/{site}/feed',
        'title': cfg.get('title', site),
        'paused': not cfg.get('enabled', False),
        'fetch_backend': 'html_requests',
        'ignore_status_codes': False,
        'time_between_check_use_default': False,
        'time_between_check': {'minutes': cfg.get('interval_minutes', 15)},
        'notification_urls': urls,
        'notification_muted': not bool(urls),
        'notification_title': '{{ watch_title }} 商品变更',
        'notification_body': '{{ diff_added }}',
        'notification_format': 'text',
        'filter_text_added': True,
        'filter_text_removed': False,
        'filter_text_replaced': True,
    }


def main():
    base = os.getenv('CD_URL', 'http://localhost:5000').rstrip('/')
    key = os.getenv('CD_API_KEY')
    if not key:
        raise SystemExit('Set CD_API_KEY from changedetection Settings > API')
    session = requests.Session()
    session.headers['x-api-key'] = key
    response = session.get(f'{base}/api/v1/watch', timeout=20)
    response.raise_for_status()
    watches = response.json()
    sites = yaml.safe_load(Path(os.getenv('CONFIG_PATH', 'config/sites.yaml')).read_text(encoding='utf-8'))['sites']
    for site, cfg in sites.items():
        data = payload(site, cfg, os.getenv('CATALOG_URL', 'http://catalog:8080'))
        matches = [uuid for uuid, watch in watches.items() if watch.get('url') == data['url']]
        if len(matches) > 1:
            raise SystemExit(f'Duplicate watches for {site}; resolve in existing UI')
        if matches:
            response = session.put(f'{base}/api/v1/watch/{matches[0]}', json=data, timeout=20)
        else:
            response = session.post(f'{base}/api/v1/watch', json=data, timeout=20)
        if not response.ok:
            # API error bodies may echo notification URLs; never print them.
            raise SystemExit(f'Configuration failed for {site}: HTTP {response.status_code}')
        print(f'{site}: configured ({"enabled" if cfg.get("enabled") else "paused"})')


if __name__ == '__main__':
    main()
