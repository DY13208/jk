"""Chinese admin API, separate from the retired changedetection adapter."""
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from adapters import REGISTRY
from .database import Database, EVENTS
from .service import MonitorService
from .email_delivery import recipients
from . import agentmail
from .mail_templates import render_html, LABELS

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


class AuthInput(BaseModel):
    action: str = Field(pattern=r'^(login|logout|cancel)$')


class MailTestInput(BaseModel):
    site_id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,47}$')
    to: str = Field(default='',max_length=2000)
    confirm: bool = False


def create_app(db_path=None, scheduled=True):
    db = Database(db_path or os.getenv('DB_PATH',str(ROOT/'data'/'snapshots.db')))
    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        agentmail.worker_token(db)
        app.state.service = MonitorService(db)
        if scheduled: app.state.service.start()
        yield
        if scheduled: await app.state.service.stop()
    app = FastAPI(title='多网站商品监控',lifespan=lifespan)
    app.state.db = db

    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.url.path.startswith('/api/agentmail/worker/'):
            # Host worker endpoints: only the random token from data/agentmail-worker.token is accepted.
            if not agentmail.valid_token(db, request.headers.get('x-agentmail-token','')):
                return JSONResponse({'detail':'工作进程令牌无效'},403)
            return await call_next(request)
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
    def agentmail_status(): return agentmail.status(db)

    @app.post('/api/agentmail/test',status_code=202)
    def agentmail_test(payload: MailTestInput):
        # Explicit admin click only; never triggered by schedules or page loads.
        if not payload.confirm: raise HTTPException(400,'请确认后再发送测试邮件')
        try: return agentmail.request_test(db,payload.site_id,payload.to or None)
        except LookupError as exc: raise HTTPException(404,str(exc)) from exc
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc

    @app.get('/api/mail/preview',response_class=HTMLResponse)
    def mail_preview(kind: str='NEW', site_id: str='lecomte'):
        """Render a template locally with a real product from the latest snapshot when available. Sends nothing."""
        if kind not in [*LABELS,'ACCESS_DENIED','TEST']: raise HTTPException(400,'未知邮件类型')
        site = db.site(site_id) or {'name':'示例网站'}
        items = db.products(site_id) if db.site(site_id) else []
        product = dict(items[0]) if items else {'product_id':'44892','name':'Kyanite Jacket Cloud','brand':"Arc'teryx",
            'sale_price':'143.96','original_price':'179.95','currency':'EUR','discount':'-20%','label':'Promotion',
            'url':'https://www.lecomte-alpirando.be/en/shop/kyanite-jacket-cloud-44892'}
        payload = {'product':product,'old_price':None}
        from decimal import Decimal
        price = Decimal(str(product.get('sale_price') or '0'))
        if kind=='PRICE_DROP': payload['old_price']=str((price*Decimal('1.25')).quantize(Decimal('0.01')))
        if kind=='PRICE_RISE': payload['old_price']=str((price*Decimal('0.8')).quantize(Decimal('0.01')))
        if kind=='ACCESS_DENIED': payload={'message':'站点拒绝访问 HTTP 403；监控已暂停（预览示例）'}
        if not product.get('image') and site.get('adapter')=='odoo_lecomte' and site.get('url'):
            # Snapshots taken before thumbnails were collected: use Odoo's standard public product image.
            from crawl4ai_run.run_crawl4ai import odoo_image
            product['image']=odoo_image(site['url'],product.get('product_id'))
        event = {'id':'预览','kind':kind,'at':agentmail.now_iso(),'payload':payload}
        summary = {'total':len(items),'changes':3} if items else None
        return HTMLResponse(render_html(site,event,summary),headers={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; img-src https: data:"})

    @app.post('/api/agentmail/auth',status_code=202)
    def agentmail_auth(payload: AuthInput):
        try: return {'auth':agentmail.request_auth(db,payload.action)}
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc

    @app.post('/api/agentmail/worker/command')
    def worker_command(): return {'command':agentmail.worker_command(db)}

    @app.post('/api/agentmail/worker/auth')
    async def worker_auth(request: Request):
        try: return {'auth':agentmail.worker_auth_update(db,await request.json())}
        except LookupError as exc: raise HTTPException(409,str(exc)) from exc
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc

    @app.post('/api/agentmail/worker/heartbeat')
    async def worker_heartbeat(request: Request):
        return agentmail.heartbeat(db,await request.json())

    @app.post('/api/agentmail/worker/claim')
    def worker_claim():
        return {'job':agentmail.claim(db)}

    @app.post('/api/agentmail/worker/result')
    async def worker_result(request: Request):
        body = await request.json()
        try: return agentmail.finish(db,body.get('job'),body.get('result') or {})
        except LookupError as exc: raise HTTPException(409,str(exc)) from exc

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

    @app.get('/api/backups')
    def backups():
        folder = db.path.parent/'backups'
        items = [{'filename':p.name,'size':p.stat().st_size} for p in folder.glob('*.db')
                 if p.is_file() and not p.is_symlink()] if folder.exists() else []
        return {'items':sorted(items,key=lambda p:p['filename'],reverse=True)}

    @app.get('/api/backups/{filename}')
    def download_backup(filename: str):
        folder = (db.path.parent/'backups').resolve()
        target = folder/filename
        if (not re.fullmatch(r'[A-Za-z0-9_-]+\.db',filename) or target.is_symlink()
            or target.resolve().parent != folder or not target.is_file()):
            raise HTTPException(404,'备份不存在')
        return FileResponse(target,filename=filename,media_type='application/octet-stream')

    return app


app = create_app()
