"""Persistent outbox; ambiguous deliveries are held for review, never blindly retried."""
import asyncio
import html
import json
import os
import time
import smtplib
import ssl
from urllib.parse import urlsplit
import httpx
from .database import now, run_summary
from .email_delivery import configuration, build_email, send_email

from .mail_templates import LABELS, message
from . import pushplus
from . import push
from . import weixin
from . import product_images
from .notification_history import snapshot, record
from .multichannel import entry, effective


def webhook(site):
    url = os.getenv(site['webhook_env'], '') if site['webhook_env'] else ''
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'qyapi.weixin.qq.com'
        or parsed.path != '/cgi-bin/webhook/send' or not parsed.query or parsed.username or parsed.port):
        raise ValueError('企业微信 Webhook 环境变量未配置或地址无效')
    return url


class Notifier:
    def __init__(self, db, sender=None, email_sender=None):
        self.db, self.sender = db, sender or self.send
        self.email_sender = email_sender or send_email
        self.lock = asyncio.Lock()

    @staticmethod
    async def send(url, payload, headers=None):
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            body, merged = push.encode(payload, headers)
            return await client.post(url, content=body, headers=merged)

    async def dispatch(self):
        if self.lock.locked(): return
        async with self.lock:
            for _ in range(20):
                with self.db.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    # A crash after transmission cannot prove whether the endpoint accepted it.
                    db.execute("UPDATE monitor_outbox SET status='uncertain',last_error='发送进程中断，需人工核对' WHERE status='sending' AND claimed_at<? AND channel!='agentmail'", (time.time()-120,))
                    # Agent Mail uses the host OS keychain and is dispatched by its host worker.
                    row = db.execute("SELECT o.* FROM monitor_outbox o WHERE o.status='pending' AND o.next_at<=? AND o.channel!='agentmail' ORDER BY o.id LIMIT 1", (time.time(),)).fetchone()
                    if not row: return
                    row = dict(row)
                    db.execute("UPDATE monitor_outbox SET status='sending',claimed_at=?,attempts=attempts+1 WHERE id=?", (time.time(),row['id']))
                    event = dict(db.execute('SELECT * FROM monitor_events WHERE id=?', (row['event_id'],)).fetchone())
                    event['payload'] = json.loads(event['payload'])
                site = self.db.site(row['site_id'])
                if event['kind'] == 'ACCESS_DENIED':
                    self.finish(row['id'], 'suppressed', '访问异常仅在后台记录，不推送'); continue
                # Each row belongs to one channel; the handler sees only that channel's env name / recipients.
                selected = entry(site, row['channel']) if site else None
                if not selected:
                    self.finish(row['id'], 'held', '站点已不再选用该通知方式，未发送'); continue
                site = effective(site, selected)
                try:
                    if not site['notify_enabled']: raise ValueError('通知已关闭')
                    if site.get('notification_channel') == 'email':
                        configuration(site, self.db)
                    elif site.get('notification_channel') == 'pushplus':
                        pushplus.token(site)
                    elif site.get('notification_channel') in push.CHANNELS:
                        push.config(site)
                    elif site.get('notification_channel') == 'weixin':
                        pass  # Credentials are kept only inside the isolated official bridge.
                    else:
                        url = webhook(site)
                except ValueError as exc:
                    self.finish(row['id'], 'held', str(exc)); continue
                try:
                    if site.get('notification_channel') in push.CHANNELS:
                        await self.deliver_push(row, site, event)
                        continue
                    content = message(site, event)
                    if site.get('notification_channel') == 'weixin':
                        await self.deliver_weixin(row,content,event)
                        continue
                    if site.get('notification_channel') == 'pushplus':
                        await self.deliver_pushplus(row, site, content)
                        continue
                    if site.get('notification_channel') == 'email':
                        await self.deliver_email(row, site, event, content)
                        continue
                    if len(content.encode('utf-8')) > 2048:
                        self.finish(row['id'], 'held', '消息超过企业微信文本长度限制'); continue
                    record(self.db,row['id'],snapshot(site,content))
                    response = await self.sender(url, {'msgtype':'text','text':{'content':content}})
                    if response.status_code == 429:
                        self.retry(row, '企业微信 HTTP 429'); continue
                    if response.status_code != 200:
                        self.finish(row['id'], 'uncertain', f'企业微信 HTTP {response.status_code}，需核对是否送达'); continue
                    body = response.json()
                    if body.get('errcode') == 0: self.finish(row['id'], 'sent', '')
                    elif body.get('errcode') in (45009, 45000): self.retry(row, '企业微信限流')
                    else: self.finish(row['id'], 'held', '企业微信明确拒绝；错误码 '+str(body.get('errcode')))
                except httpx.ConnectError:
                    self.retry(row, '无法连接企业微信')
                except Exception:
                    self.finish(row['id'], 'uncertain', '发送结果不确定，需人工核对后处理')

    async def deliver_email(self, row, site, event, content):
        from .mail_digest import claim_digest
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows, event = claim_digest(conn, row, event)
        content = message(site, event)
        def finish(status, reason):
            for item in rows: self.finish(item['id'], status, reason)
        def retry(reason):
            for item in rows: self.retry(item, reason)
        config = configuration(site, self.db)
        with self.db.connect() as conn: summary = run_summary(conn, event.get('run_id'))
        mail = build_email(site, event, content, config, summary)
        for item in rows: record(self.db,item['id'],snapshot(site,content,channel='email',recipients=config['recipients'],sender=config['sender'],
                                        title=str(mail['Subject']),html=mail.get_body(preferencelist=('html',)).get_content()))
        try:
            refused = await asyncio.to_thread(self.email_sender, config, mail)
            if refused:
                finish('uncertain', '部分收件人被拒绝，其他收件人可能已收到；请核对后处理')
            else:
                finish('sent', 'SMTP 服务器已接受邮件；最终收件状态请在邮箱中核对')
        except smtplib.SMTPRecipientsRefused as exc:
            if all(400 <= code < 500 for code, _ in exc.recipients.values()): retry('SMTP 暂时拒绝所有收件人')
            else: finish('held', 'SMTP 拒绝收件邮箱，请核对设置')
        except smtplib.SMTPResponseException as exc:
            if 400 <= exc.smtp_code < 500: retry(f'SMTP 暂时拒绝（{exc.smtp_code}）')
            else: finish('held', f'SMTP 明确拒绝（{exc.smtp_code}），请核对配置')
        except (ConnectionRefusedError, smtplib.SMTPNotSupportedError, ssl.SSLCertVerificationError):
            finish('held', 'SMTP 连接、加密或证书配置错误，请核对设置')
        except Exception:
            finish('uncertain', '邮件发送结果不确定，请核对收件箱后再重试')

    async def deliver_pushplus(self, row, site, content):
        # Persistent, conservative global spacing also protects accounts shared by sites.
        with self.db.connect() as conn:
            last = conn.execute("SELECT MAX(o.claimed_at) FROM monitor_outbox o WHERE o.channel='pushplus' AND o.id!=? AND o.status IN ('sent','uncertain')", (row['id'],)).fetchone()[0]
            if last and time.time() - last < 15:
                conn.execute("UPDATE monitor_outbox SET status='pending',next_at=?,attempts=attempts-1 WHERE id=?", (last+15,row['id']))
                return
        try:
            record(self.db,row['id'],snapshot(site,content,channel='pushplus'))
            response = await self.sender(pushplus.ENDPOINT, {
                'token':pushplus.token(site), 'title':content.splitlines()[0][:100],
                'content':content, 'template':'txt', 'channel':'wechat'})
            if response.status_code == 429:
                self.retry(row, 'pushplus 请求限流'); return
            if response.status_code != 200:
                self.finish(row['id'], 'uncertain', f'pushplus HTTP {response.status_code}，请核对消息记录'); return
            code = response.json().get('code')
            if code == 200:
                self.finish(row['id'], 'sent', 'pushplus 已接受请求，非微信送达确认；最终状态请核对 pushplus 消息记录')
            else:
                # Do not persist provider text: it may echo a credential or message.
                self.finish(row['id'], 'held', 'pushplus 拒绝请求，请核对 Token、实名状态及额度')
        except httpx.ConnectError:
            self.retry(row, '无法连接 pushplus')
        except Exception:
            self.finish(row['id'], 'uncertain', 'pushplus 接收结果不确定，请核对消息记录后再重试')

    async def deliver_push(self, row, site, event):
        channel = site['notification_channel']
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Gentle global spacing per provider (ntfy.sh/Bark public servers rate-limit bursts).
            last = conn.execute("SELECT MAX(o.claimed_at) FROM monitor_outbox o WHERE o.channel=? AND o.id!=? AND o.status IN ('sent','uncertain')", (channel,row['id'])).fetchone()[0]
            if last and time.time() - last < 5:
                conn.execute("UPDATE monitor_outbox SET status='pending',next_at=?,attempts=attempts-1 WHERE id=?", (last+5,row['id']))
                return
            # Batch the site's other due events into the same push (one notification per crawl, not per product).
            others = [dict(r) for r in conn.execute("SELECT o.* FROM monitor_outbox o JOIN monitor_events e ON e.id=o.event_id WHERE o.site_id=? AND o.channel=? AND o.status='pending' AND o.next_at<=? AND o.id!=? AND e.kind!='ACCESS_DENIED' ORDER BY o.id LIMIT 49", (row['site_id'],channel,time.time(),row['id']))]
            events = [event]
            for other in others:
                conn.execute("UPDATE monitor_outbox SET status='sending',claimed_at=?,attempts=attempts+1 WHERE id=?", (time.time(),other['id']))
                e = dict(conn.execute('SELECT * FROM monitor_events WHERE id=?', (other['event_id'],)).fetchone())
                e['payload'] = json.loads(e['payload']); events.append(e)
        rows = [row] + others
        try:
            url, headers, extra = push.config(site)
            note = push.build(site, events)
            for r in rows: record(self.db, r['id'], snapshot(site, note['title']+'\n'+note['body'], channel=channel, title=note['title']))
            body = push.payload(channel, note, extra, site.get('name') or '')
            response = await (self.sender(url, body, headers) if headers else self.sender(url, body))
            state, reason = push.outcome(channel, response)
        except httpx.ConnectError:
            state, reason = 'retry', f'无法连接 {channel}'
        except Exception:
            state, reason = 'uncertain', f'{channel} 发送结果不确定，请在手机上核对'
        if len(rows) > 1 and state in ('sent', 'uncertain'): reason += f'（{len(rows)} 条事件合并为一条推送）'
        for r in rows:
            if state == 'retry': self.retry(r, reason)
            else: self.finish(r['id'], state, reason)

    async def deliver_weixin(self,row,content,event=None):
        state=await weixin.status(self.db)
        if not state.get('ready'):
            # No send was attempted: wait for login/activation without burning attempts.
            with self.db.connect() as conn:
                conn.execute("UPDATE monitor_outbox SET status='pending',next_at=?,attempts=attempts-1,last_error=? WHERE id=?",(time.time()+60,'等待微信扫码连接或主动对话激活',row['id']))
            return
        if len(content)>3500:
            self.finish(row['id'],'held','微信文本过长，请在后台查看商品详情');return
        image_url=((event or {}).get('payload') or {}).get('product',{}).get('image')
        image=await product_images.attachment(image_url)
        try:
            site=effective(self.db.site(row['site_id']),{'channel':'weixin','env':'','to':''})
            evidence=snapshot(site,content,channel='weixin',recipients=[state['recipient']] if state.get('recipient') else [])
            record(self.db,row['id'],evidence)
            result=await weixin.request(self.db,'send',{'text':content,**({'image':image} if image else {})})
            if result.get('recipient'):
                evidence.update(recipients=[result['recipient']],recipient_label=result['recipient'])
                record(self.db,row['id'],evidence)
            if result.get('ok') is True:
                picture=('；图片接口已接受' if result.get('image_status')=='accepted' else
                         '；图片未确认，不自动重发文字' if image else
                         '；图片不可用，已降级为文字' if image_url else '')
                self.finish(row['id'],'sent','腾讯微信文字接口已接受'+picture+'；最终送达请在微信核对')
            else: self.finish(row['id'],'uncertain','微信发送结果不确定，请核对后重试')
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code==409:
                with self.db.connect() as conn:
                    conn.execute("UPDATE monitor_outbox SET status='pending',next_at=?,attempts=attempts-1 WHERE id=?",(time.time()+60,row['id']))
            else: self.finish(row['id'],'uncertain','微信接口未确认发送结果，请核对后重试')
        except Exception:
            self.finish(row['id'],'uncertain','微信发送结果不确定，请核对后重试')

    def retry(self, row, reason):
        attempts = row['attempts'] + 1
        with self.db.connect() as db:
            db.execute('UPDATE monitor_outbox SET status=?,next_at=?,last_error=? WHERE id=?',
                ('pending' if attempts < 5 else 'held',time.time()+min(3600,60*2**attempts),reason,row['id']))

    def finish(self, row_id, status, reason):
        with self.db.connect() as db:
            db.execute('UPDATE monitor_outbox SET status=?,last_error=?,sent_at=? WHERE id=?',
                (status,reason,now() if status=='sent' else None,row_id))
