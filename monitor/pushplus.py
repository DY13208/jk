"""pushplus adapter; existing persistent outbox owns retries and deduplication."""
import os
import re

ENDPOINT = 'https://www.pushplus.plus/send'


def token(site):
    name = site.get('webhook_env') or ''
    value = os.getenv(name, '').strip() if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', name) else ''
    if not value or len(value) > 256 or any(c.isspace() for c in value):
        raise ValueError('请在部署环境配置 pushplus Token，并填写对应环境变量名')
    return value
