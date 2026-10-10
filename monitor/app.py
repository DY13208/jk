import asyncio
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from .collector import collect
from .store import Incomplete, Store

app = FastAPI(title='Catalog adapter (changedetection.io extension)')
locks = {}


def settings():
    return yaml.safe_load(Path(os.getenv('CONFIG_PATH', 'config/sites.yaml')).read_text(encoding='utf-8'))['sites']


def store():
    return Store(os.getenv('DB_PATH', 'data/catalog.sqlite'))


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/sites/{site}/feed', response_class=PlainTextResponse)
async def feed(site: str):
    cfg = settings().get(site)
    if cfg is None:
        raise HTTPException(404, 'Unknown site')
    if not cfg.get('enabled'):
        raise HTTPException(503, 'Site disabled pending verified configuration')
    async with locks.setdefault(site, asyncio.Lock()):
        db = store()
        try:
            products, evidence = await asyncio.wait_for(collect(cfg), timeout=cfg.get('run_timeout', 240))
            db.commit(site, products, evidence, cfg.get('events', ['added', 'removed', 'relisted', 'price_changed']))
        except Exception as exc:
            # Avoid recording URLs, HTML or credentials from browser exceptions.
            db.failure(site, str(exc) if isinstance(exc, Incomplete) else type(exc).__name__)
            raise HTTPException(503, 'Collection incomplete; previous successful snapshot retained') from exc
        return db.feed(site)


@app.get('/sites/{site}/latest')
def latest(site: str):
    return {'site': site, 'products': store().latest(site)}
