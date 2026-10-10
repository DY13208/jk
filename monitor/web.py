"""Chinese admin API, separate from the retired changedetection adapter."""
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from adapters import REGISTRY
from .database import Database, EVENTS
from .service import MonitorService
from .email_delivery import recipients

ROOT = Path(__file__).resolve().parents[1]


class SiteInput(BaseModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,47}$')
    name: str = Field(min_length=1,max_length=100)
    url: str = Field(default='',max_length=2000)
    adapter: str = 'odoo_lecomte'
    interval_minutes: int = Field(default=15,ge=1,le=10080)
    enabled: bool = False
    permission_confirmed: bool = False
    policy_note: str = Field(default='',max_length=2000)
    adapter_config: dict = Field(default_factory=dict)
    event_rules: list[str] = Field(default_factory=lambda:EVENTS.copy())
    notify_enabled: bool = False
    webhook_env: str = Field(default='',pattern=r'^$|^[A-Z][A-Z0-9_]{0,79}$')
    notification_channel: str = Field(default='wecom',pattern=r'^(wecom|email|agentmail)$')
    email_to: str = Field(default='',max_length=2000)

    @model_validator(mode='after')
    def valid(self):
        if self.email_to: recipients(self.email_to)
        if self.notify_enabled and self.notification_channel in ('email','agentmail') and not self.email_to:
            raise ValueError('启用邮件推送前请填写收件邮箱')
        parsed = urlsplit(self.url)
        if self.url and (parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password):
            raise ValueError('URL 必须是无用户名密码的 HTTP/HTTPS 地址')
        if self.adapter not in REGISTRY: raise ValueError('未知采集适配器')
        if not set(self.event_rules).issubset(EVENTS): raise ValueError('事件规则无效')
        if self.enabled and not self.url:
            raise ValueError('启用采集前请填写商品列表 URL')
        if self.enabled and not self.permission_confirmed:
            raise ValueError('启用采集前请勾选“已核对站点政策及自动化访问许可”')
        if self.enabled and self.adapter=='css_catalog' and not self.adapter_config.get('schema'):
            raise ValueError('CSS 适配器缺少提取 schema')
        if 'url' in self.adapter_config: raise ValueError('请在网站 URL 字段配置入口')
        return self


def create_app(db_path=None, scheduled=True):
    db = Database(db_path or os.getenv('DB_PATH',str(ROOT/'data'/'snapshots.db')))
    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        app.state.service = MonitorService(db)
        if scheduled: app.state.service.start()
        yield
        if scheduled: await app.state.service.stop()
    app = FastAPI(title='多网站商品监控',lifespan=lifespan)
    app.state.db = db

    @app.middleware('http')
    async def guard(request: Request, call_next):
        password = os.getenv('ADMIN_PASSWORD','')
        if request.url.path != '/health' and password:
            import base64
            try:
                mode, encoded = request.headers.get('authorization','').split(' ',1)
                username, supplied = base64.b64decode(encoded,validate=True).decode().split(':',1)
                valid = mode.lower()=='basic' and secrets.compare_digest(username,os.getenv('ADMIN_USERNAME','admin')) and secrets.compare_digest(supplied,password)
            except Exception: valid=False
            if not valid: return JSONResponse({'detail':'需要管理员登录'},401,headers={'WWW-Authenticate':'Basic realm="Monitor"'})
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'禁止跨站修改'},403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail':'禁止跨站修改'},403)
        return await call_next(request)

    @app.get('/health')
    def health(): return {'ok':True,'crawler':'Crawl4AI 0.9.4'}

    @app.get('/')
    def index(): return FileResponse(ROOT/'admin'/'index.html')

    app.mount('/assets',StaticFiles(directory=ROOT/'admin'),name='assets')

    @app.get('/api/overview')
    def overview(): return db.overview()

    @app.get('/api/sites')
    def sites():
        items = db.sites()
        for site in items: site['product_count'] = len(db.products(site['id']))
        return {'items':items,'adapters':list(REGISTRY)}

    @app.put('/api/sites/{site_id}')
    def save(site_id: str, payload: SiteInput, request: Request):
        if site_id != payload.id: raise HTTPException(400,'网站 ID 不一致')
        try: db.save_site(payload.model_dump())
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        if scheduled: request.app.state.service.sync_jobs()
        return db.site(site_id)

    @app.post('/api/sites/{site_id}/run',status_code=202)
    async def run(site_id: str, request: Request):
        site = db.site(site_id)
        if not site: raise HTTPException(404,'网站不存在')
        if not site['enabled'] or not site['permission_confirmed']: raise HTTPException(409,'网站暂停或未核对访问许可')
        service = request.app.state.service
        if site_id in service.active: raise HTTPException(409,'任务已在执行或等待浏览器')
        service.launch(site_id)
        return {'status':'QUEUED'}

    @app.get('/api/products')
    def products(site_id: str='lecomte', q: str='',limit: int=Query(100,ge=1,le=500),offset: int=Query(0,ge=0)):
        items = [p for p in db.products(site_id) if q.casefold() in (p['name']+' '+p['product_id']).casefold()]
        return {'items':items[offset:offset+limit],'total':len(items)}

    @app.get('/api/events')
    def events(site_id: str|None=None,limit: int=Query(100,ge=1,le=500),offset: int=Query(0,ge=0)):
        return db.history('monitor_events',site_id,limit,offset)

    @app.get('/api/logs')
    def logs(site_id: str|None=None,limit: int=Query(100,ge=1,le=500),offset: int=Query(0,ge=0)):
        return db.history('monitor_logs',site_id,limit,offset)

    @app.get('/api/notifications')
    def notifications(limit: int=Query(100,ge=1,le=500),offset: int=Query(0,ge=0)):
        return db.history('monitor_outbox',limit=limit,offset=offset)

    @app.get('/api/agentmail/status')
    def agentmail_status():
        import time, json
        with db.connect() as conn:
            row = conn.execute("SELECT value FROM monitor_meta WHERE key='agentmail_worker'").fetchone()
        state = json.loads(row[0]) if row else {}
        return {'running':time.time()-state.get('heartbeat',0)<45, 'account':state.get('account',''),
                'message':state.get('message','本机 Agent Mail 工作进程尚未启动')}

    @app.post('/api/notifications/{row_id}/retry')
    def retry(row_id: int):
        with db.connect() as conn:
            row = conn.execute('SELECT status FROM monitor_outbox WHERE id=?',(row_id,)).fetchone()
            if not row: raise HTTPException(404,'通知不存在')
            if row[0] not in ('held','uncertain'): raise HTTPException(409,'仅允许人工重试待核对或暂停的通知')
            conn.execute("UPDATE monitor_outbox SET status='pending',attempts=0,next_at=0,last_error='' WHERE id=?",(row_id,))
        return {'ok':True}

    @app.post('/api/backups')
    async def backup(request: Request):
        import asyncio
        return {'filename':await asyncio.to_thread(db.backup,'manual')}

    return app


app = create_app()
