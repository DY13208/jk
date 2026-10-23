"""Chinese admin API, separate from the retired changedetection adapter."""
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Query, Body
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from adapters import REGISTRY
from .database import Database, EVENTS
from .service import MonitorService
from .email_delivery import recipients
from . import agentmail
from . import host_collector
from . import weixin
from . import push
import httpx
from .mail_templates import render_html, LABELS

ROOT = Path(__file__).resolve().parents[1]


class ChannelInput(BaseModel):
    channel: str = Field(pattern=r'^(wecom|email|agentmail|pushplus|weixin|bark|ntfy)$')
    env: str = Field(default='',pattern=r'^$|^[A-Z][A-Z0-9_]{0,79}$')
    to: str = Field(default='',max_length=2000)


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
    notification_channel: str = Field(default='wecom',pattern=r'^(wecom|email|agentmail|pushplus|weixin|bark|ntfy)$')
    email_to: str = Field(default='',max_length=2000)
    # Additive: several channels per site. Absent/empty = the legacy single fields above (old clients).
    notification_channels: list[ChannelInput] = Field(default_factory=list,max_length=7)

    @model_validator(mode='after')
    def valid(self):
        from .multichannel import reconcile, mirror
        entries = reconcile(self.model_dump())
        for e in entries:
            if e['to']: recipients(e['to'])
            if self.notify_enabled and e['channel'] in ('email','agentmail') and not e['to']:
                raise ValueError('启用邮件推送前请填写收件邮箱')
            if self.notify_enabled and e['channel'] in ('bark','ntfy') and not e['env']:
                raise ValueError('启用 Bark/ntfy 推送前请填写保存 Key/主题 的环境变量名（如 BARK_KEY、NTFY_TOPIC）')
            if self.notify_enabled and e['channel'] == 'pushplus' and not e['env']:
                raise ValueError('启用个人微信推送前请填写 pushplus Token 环境变量名')
        if self.email_to: recipients(self.email_to)
        for key, value in mirror(self.model_dump(), entries).items(): setattr(self, key, value)
        self.notification_channels = [ChannelInput(**e) for e in entries]
        parsed = urlsplit(self.url)
        if self.url and (parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password):
            raise ValueError('URL 必须是无用户名密码的 HTTP/HTTPS 地址')
        if self.adapter not in REGISTRY: raise ValueError('未知采集适配器')
        if not set(self.event_rules).issubset(EVENTS): raise ValueError('事件规则无效')
        if self.enabled and not self.url:
            raise ValueError('启用采集前请填写商品列表 URL')
        if self.enabled and not self.permission_confirmed:
            raise ValueError('启用采集前请勾选“已核对站点政策及自动化访问许可”')
        if 'url' in self.adapter_config: raise ValueError('请在网站 URL 字段配置入口')
        if 'persistent_session' in self.adapter_config and type(self.adapter_config['persistent_session']) is not bool:
            raise ValueError('persistent_session 必须为 true 或 false')
        delay = self.adapter_config.get('page_delay',1.5)
        if type(delay) not in (int,float) or not 1 <= delay <= 30:
            raise ValueError('page_delay 必须在 1 到 30 秒之间')
        if self.adapter_config.get('pagination_mode','auto') not in ('auto','links','load_more','next_link'):
            raise ValueError('pagination_mode 必须为 links 或 load_more')
        if self.adapter_config.get('execution_mode','docker') not in ('docker','host','extension','flaresolverr'):
            raise ValueError('execution_mode 必须为 docker、host、extension 或 flaresolverr')
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
        host_collector.token(db)
        host_collector.token(db,'extension')
        weixin.token(db)
        app.state.service = MonitorService(db)
        if scheduled: app.state.service.start()
        yield
        if scheduled: await app.state.service.stop()
    app = FastAPI(title='多网站商品监控',lifespan=lifespan)
    app.state.db = db
    from .notifications import Notifier
    app.state.push_sender = Notifier.send
    from .email_delivery import send_email
    app.state.email_sender = send_email
    from .gmail import router as gmail_router
    app.include_router(gmail_router(db))
    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler

    @app.exception_handler(RequestValidationError)
    async def safe_validation(request, exc):
        if request.url.path.startswith('/api/gmail/'):
            return JSONResponse({'detail':'填写内容无效，请检查邮箱、密码、主题及附件大小'}, status_code=422)
        return await request_validation_exception_handler(request, exc)


    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.url.path.startswith('/api/collector/extension/worker/'):
            origin=request.headers.get('origin','')
            extension_origin=bool(re.fullmatch(r'chrome-extension://[a-p]{32}',origin))
            if request.method=='OPTIONS':
                if not extension_origin: return JSONResponse({'detail':'扩展来源无效'},403)
                return JSONResponse({},headers={'Access-Control-Allow-Origin':origin,
                    'Access-Control-Allow-Methods':'POST','Access-Control-Allow-Headers':'content-type,x-collector-token'})
            if not host_collector.valid_token(db,request.headers.get('x-collector-token',''),'extension'):
                return JSONResponse({'detail':'扩展令牌无效'},403)
            if request.headers.get('content-length','0').isdigit() and int(request.headers.get('content-length','0'))>12_000_000:
                return JSONResponse({'detail':'结果过大'},413)
            response=await call_next(request)
            if extension_origin: response.headers['Access-Control-Allow-Origin']=origin
            return response
        if request.url.path.startswith('/api/collector/worker/'):
            if not host_collector.valid_token(db,request.headers.get('x-collector-token','')):
                return JSONResponse({'detail':'采集工作进程令牌无效'},403)
            return await call_next(request)
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

    @app.get('/api/health')
    async def business_status():
        from .business_health import business_health
        wx = await weixin_for_health()
        import asyncio
        return await asyncio.to_thread(business_health, db, app.state.service, wx)

    @app.get('/')
    def index(): return FileResponse(ROOT/'admin'/'index.html')

    app.mount('/assets',StaticFiles(directory=ROOT/'admin'),name='assets')

    async def weixin_for_health():
        # Only ask the bridge when a site actually notifies through it (status() never raises).
        if any(s['notify_enabled'] and s['enabled'] and any(e['channel']=='weixin' for e in s['notification_channels']) for s in db.sites()):
            return await weixin.status(db)
        return None

    @app.get('/api/overview')
    async def overview():
        from .business_health import business_health
        wx = await weixin_for_health()
        import asyncio
        return await asyncio.to_thread(lambda: {**db.overview(), 'business_health': business_health(db, app.state.service, wx)})

    @app.get('/api/overview/trends')
    def overview_trends(days: int=Query(90,ge=1,le=90), offset_minutes: int=Query(480,ge=-840,le=840)):
        from .overview_trends import trends
        return trends(db, days, offset_minutes)

    @app.get('/api/notifications/diagnostics')
    def notification_diagnostics():
        from .notification_history import diagnostics
        return diagnostics(db)

    @app.get('/api/storage')
    def storage_status():
        from .storage import storage_status
        return storage_status(db)

    @app.get('/api/collector/status')
    def collector_status(): return {**host_collector.status(db),'extension':host_collector.status(db,'extension')}

    @app.post('/api/collector/extension/worker/heartbeat')
    def extension_heartbeat(): return host_collector.heartbeat(db,'extension')

    @app.post('/api/collector/extension/worker/claim')
    def extension_claim(): return {'job':host_collector.claim(db,'extension')}

    @app.post('/api/collector/extension/worker/sites')
    def extension_sites():
        return {'items':[{k:site[k] for k in ('id','name','url','adapter','adapter_config')}
                         for site in db.sites() if site['adapter_config'].get('execution_mode')=='extension']}

    @app.post('/api/collector/extension/worker/verify')
    async def extension_verify(request: Request):
        from .extension_capture import normalize
        body=await request.json()
        site=db.site(body.get('site_id'))
        if not site or site['adapter_config'].get('execution_mode')!='extension':
            raise HTTPException(409,'请先选择扩展采集模式')
        try:
            document=normalize(site,body.get('capture'))
            db.validate(document)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        result={'products':len(document['products']),'pages':document['meta']['site_pages']}
        return result

    @app.post('/api/collector/extension/worker/result')
    async def extension_result(request: Request):
        try: return host_collector.finish(db,await request.json(),'extension')
        except LookupError as exc: raise HTTPException(409,str(exc)) from exc
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc

    @app.post('/api/collector/worker/heartbeat')
    def collector_heartbeat(): return host_collector.heartbeat(db)

    @app.post('/api/collector/worker/claim')
    def collector_claim(): return {'job':host_collector.claim(db)}

    @app.post('/api/collector/worker/result')
    async def collector_result(request: Request):
        try: return host_collector.finish(db,await request.json())
        except LookupError as exc: raise HTTPException(409,str(exc)) from exc
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc

    @app.get('/api/sites')
    def sites():
        items = db.sites()
        counts = db.product_counts()
        for site in items: site['product_count'] = counts.get(site['id'], 0)
        return {'items':items,'adapters':list(REGISTRY)}

    @app.put('/api/sites/{site_id}')
    def save(site_id: str, request: Request, body: dict = Body(...)):
        from .multichannel import reconcile
        from fastapi.exceptions import RequestValidationError
        from pydantic import ValidationError
        stored = db.site(site_id)
        if body.get('notification_channels') and stored:
            # Old clients may edit only the single legacy fields while echoing back the stored list.
            body = {**body, 'notification_channels': reconcile(body, stored)}
        try: payload = SiteInput(**body)
        except ValidationError as exc: raise RequestValidationError(exc.errors()) from exc
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
    def products(site_id: str='lecomte', q: str=Query('',max_length=200),limit: int=Query(100,ge=1,le=500),offset: int=Query(0,ge=0)):
        return db.product_page(site_id, q, limit, offset)

    @app.get('/api/events')
    def events(site_id: str|None=None, kind: str=Query('',pattern=r'^$|^(NEW|REMOVED|RETURNED|PRICE_DROP|PRICE_RISE|ACCESS_DENIED)$'),
               q: str=Query('',max_length=200), start: str='', end: str='', currency: str=Query('',max_length=12),
               sort: str=Query('time_desc',pattern=r'^(time|site|name|price|change)_(asc|desc)$'),
               limit: int=Query(100,ge=1,le=500), offset: int=Query(0,ge=0)):
        from datetime import datetime, timezone
        from .event_history import query_events
        dates = []
        for value in (start,end):
            try:
                parsed = datetime.fromisoformat(value.replace('Z','+00:00')) if value else None
                if parsed and (parsed.tzinfo is None or parsed.utcoffset() is None): raise ValueError()
                dates.append(parsed.astimezone(timezone.utc) if parsed else None)
            except ValueError: raise HTTPException(400,'日期需要包含时区的有效时间') from None
        if all(dates) and dates[0]>=dates[1]: raise HTTPException(400,'结束日期不能早于开始日期')
        return query_events(db,site_id=site_id,kind=kind,q=q.strip(),start=dates[0].isoformat() if dates[0] else None,
                            end=dates[1].isoformat() if dates[1] else None,currency=currency.strip(),sort=sort,limit=limit,offset=offset)

    @app.get('/api/logs')
    def logs(site_id: str|None=None,limit: int=Query(100,ge=1,le=500),offset: int=Query(0,ge=0)):
        return db.history('monitor_logs',site_id,limit,offset)

    @app.get('/api/notifications')
    def notifications(site_id: str='', status: str=Query('',pattern=r'^$|^(pending|sending|sent|held|uncertain|suppressed)$'),
                      channel: str=Query('',pattern=r'^$|^(weixin|wecom|pushplus|bark|ntfy|email|agentmail|unknown)$'),
                      limit: int=Query(100,ge=1,le=500),offset: int=Query(0,ge=0)):
        from .notification_history import listing
        return listing(db,site_id=site_id,status=status,channel=channel,limit=limit,offset=offset)

    @app.get('/api/notifications/{row_id}')
    def notification_detail(row_id: int):
        from .notification_history import detail
        try: return detail(db,row_id)
        except LookupError: raise HTTPException(404,'通知不存在') from None

    @app.get('/api/notifications/{row_id}/preview',response_class=HTMLResponse)
    def notification_preview(row_id: int):
        data=notification_detail(row_id)
        return HTMLResponse(data['preview']['html'],headers={
            'Cache-Control':'no-store',
            'Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
            'Referrer-Policy':'no-referrer'})

    @app.get('/api/agentmail/status')
    def agentmail_status(): return agentmail.status(db)

    @app.get('/api/weixin/status')
    async def weixin_status():
        response=JSONResponse(await weixin.status(db))
        response.headers['Cache-Control']='no-store'
        return response

    @app.post('/api/weixin/{action}')
    async def weixin_action(action: str, request: Request):
        if action not in ('login','cancel','verify'): raise HTTPException(404,'操作不存在')
        body=await request.json()
        if action=='verify' and not re.fullmatch(r'\d{4,12}',str(body.get('code',''))):
            raise HTTPException(400,'请填写微信要求的数字配对码')
        try: return await weixin.request(db,action,{'code':body.get('code')} if action=='verify' else {})
        except Exception: raise HTTPException(503,'微信连接服务暂不可用，请检查服务状态') from None

    @app.get('/api/notify/channels')
    async def notify_channels():
        # Presence and variable names only; secret values never leave the environment.
        from . import channels
        import asyncio
        wx = await weixin.status(db)
        agent = await asyncio.to_thread(agentmail.status, db)
        items = await asyncio.to_thread(channels.overview, db, wx, agent)
        return {'channels': items}

    @app.post('/api/notify/channels/{channel}/test')
    async def notify_channel_test(channel: str, payload: dict, request: Request):
        """Explicit admin click only: one real test message through a channel, independent of any site."""
        from . import channels, pushplus
        from .notifications import webhook
        import asyncio
        if channel not in channels.NAMES: raise HTTPException(404,'未知通知方式')
        if not payload.get('confirm'): raise HTTPException(400,'请确认后再发送测试')
        env = str(payload.get('env') or channels.DEFAULT_ENV.get(channel,''))
        to = str(payload.get('to') or '')
        if channel in ('bark','ntfy','wecom','pushplus') and env not in channels.env_names(db, channel):
            raise HTTPException(400,'请选择通知设置页列出的环境变量')
        probe = {'id':'_channel_test','name':'通知设置','url':'','notification_channel':channel,'webhook_env':env,'email_to':to}
        title, body = '【测试】商品监控通知', '这是一条从“通知设置”页手动发送的测试消息；收到即说明该通知方式配置正确。'
        sender = request.app.state.push_sender
        try:
            if channel in push.CHANNELS:
                url, headers, extra = push.config(probe)
                note = push.build(probe,[{'id':0,'kind':'TEST','payload':{}}]); note.update(title=title, body=body)
                response = await sender(url, push.payload(channel,note,extra,'通知设置'), headers)
                state, reason = push.outcome(channel, response)
            elif channel == 'wecom':
                response = await sender(webhook(probe), {'msgtype':'text','text':{'content':title+'\n'+body}})
                ok = response.status_code == 200 and response.json().get('errcode') == 0
                state, reason = ('sent','企业微信已接受') if ok else ('held',f'企业微信未接受（HTTP {response.status_code}）')
            elif channel == 'pushplus':
                response = await sender(pushplus.ENDPOINT, {'token':pushplus.token(probe),'title':title,'content':body,'template':'txt','channel':'wechat'})
                ok = response.status_code == 200 and response.json().get('code') == 200
                state, reason = ('sent','pushplus 已接受请求') if ok else ('held','pushplus 拒绝请求，请核对 Token、实名状态及额度')
            elif channel == 'email':
                from .email_delivery import configuration
                from email.message import EmailMessage
                config = configuration(probe, db)
                mail = EmailMessage(); mail['Subject'], mail['From'], mail['To'] = title, config['sender'], ', '.join(config['recipients'])
                mail.set_content(body)
                refused = await asyncio.to_thread(request.app.state.email_sender, config, mail)
                state, reason = ('uncertain','部分收件人被拒绝') if refused else ('sent','SMTP 服务器已接受邮件；请在邮箱中核对')
            elif channel == 'agentmail':
                first = next(iter(db.sites()), None)
                if not first: raise ValueError('请先添加至少一个网站')
                queued = agentmail.request_test(db, first['id'], to)
                state, reason = 'queued', f"测试邮件已排队（{', '.join(queued['recipients'])}），本机工作进程处理后见 Agent Mail 测试记录"
            else:
                wx = await weixin.status(db)
                if not wx.get('ready'): raise ValueError('微信扫码直连未就绪')
                result = await weixin.request(db,'send',{'text':title+'\n'+body})
                state, reason = ('sent','腾讯微信接口已接受') if result.get('ok') is True else ('uncertain','微信发送结果不确定')
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        except httpx.HTTPError: raise HTTPException(502,'无法连接通知服务') from None
        except Exception: raise HTTPException(502,'发送结果不确定，请在接收端核对') from None
        return {'status':state,'message':reason}

    @app.post('/api/sites/{site_id}/push-test')
    async def push_test(site_id: str, payload: dict, request: Request):
        # Explicit admin click only: one test through one of the site's selected Bark/ntfy channels
        # (payload.channel picks which; default = the first selected Bark/ntfy).
        from .multichannel import effective
        if not payload.get('confirm'): raise HTTPException(400,'请确认后再发送测试推送')
        site = db.site(site_id)
        if not site: raise HTTPException(404,'站点不存在')
        wanted = str(payload.get('channel') or '')
        choice = next((e for e in site['notification_channels'] if e['channel'] in push.CHANNELS and (not wanted or e['channel']==wanted)), None)
        if not choice: raise HTTPException(409,'该站点未选择 Bark 或 ntfy')
        site = effective(site, choice)
        try: url, headers, extra = push.config(site)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        note = push.build(site,[{'id':0,'kind':'TEST','payload':{}}])
        note['title'] = f"【测试】商品监控推送 · {site['name']}"
        note['body'] = '这是一条从后台手动发送的测试推送；收到即说明配置正确。'
        try: response = await request.app.state.push_sender(url, push.payload(site['notification_channel'],note,extra,site.get('name') or ''), headers)
        except httpx.HTTPError: raise HTTPException(502,'无法连接推送服务器') from None
        state, reason = push.outcome(site['notification_channel'], response)
        return {'status':state,'message':reason}

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
            row = conn.execute('SELECT o.status,e.kind FROM monitor_outbox o JOIN monitor_events e ON e.id=o.event_id WHERE o.id=?',(row_id,)).fetchone()
            if not row: raise HTTPException(404,'通知不存在')
            if row['kind']=='ACCESS_DENIED': raise HTTPException(409,'访问异常按规则不投递，无需重试')
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
