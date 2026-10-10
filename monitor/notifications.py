"""Persistent outbox; ambiguous deliveries are held for review, never blindly retried."""
import asyncio
import html
import json
import os
import time
import smtplib
from urllib.parse import urlsplit
import httpx
from .database import now
from .email_delivery import configuration, build_email, send_email

from .mail_templates import LABELS, message


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
    async def send(url, payload):
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            return await client.post(url, json=payload)

    async def dispatch(self):
        if self.lock.locked(): return
        async with self.lock:
            for _ in range(20):
                with self.db.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    # A crash after transmission cannot prove whether the endpoint accepted it.
                    db.execute("UPDATE monitor_outbox SET status='uncertain',last_error='发送进程中断，需人工核对' WHERE status='sending' AND claimed_at<?", (time.time()-60,))
                    # Agent Mail uses the host OS keychain and is dispatched by its host worker.
                    row = db.execute("SELECT o.* FROM monitor_outbox o JOIN monitor_sites s ON s.id=o.site_id WHERE o.status='pending' AND o.next_at<=? AND s.notification_channel!='agentmail' ORDER BY o.id LIMIT 1", (time.time(),)).fetchone()
                    if not row: return
                    row = dict(row)
                    db.execute("UPDATE monitor_outbox SET status='sending',claimed_at=?,attempts=attempts+1 WHERE id=?", (time.time(),row['id']))
                    event = dict(db.execute('SELECT * FROM monitor_events WHERE id=?', (row['event_id'],)).fetchone())
                    event['payload'] = json.loads(event['payload'])
                site = self.db.site(row['site_id'])
                try:
                    if not site['notify_enabled']: raise ValueError('通知已关闭')
                    if site.get('notification_channel') == 'email':
                        configuration(site)
                    else:
                        url = webhook(site)
                except ValueError as exc:
                    self.finish(row['id'], 'held', str(exc)); continue
                try:
                    content = message(site, event)
                    if site.get('notification_channel') == 'email':
                        await self.deliver_email(row, site, event, content)
                        continue
                    if len(content.encode('utf-8')) > 2048:
                        self.finish(row['id'], 'held', '消息超过企业微信文本长度限制'); continue
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
        config = configuration(site)
        mail = build_email(site, event, content, config)
        try:
            refused = await asyncio.to_thread(self.email_sender, config, mail)
            if refused:
                self.finish(row['id'], 'uncertain', '部分收件人被拒绝，其他收件人可能已收到；请核对后处理')
            else:
                self.finish(row['id'], 'sent', 'SMTP 服务器已接受邮件；最终收件状态请在邮箱中核对')
        except smtplib.SMTPRecipientsRefused as exc:
            if all(400 <= code < 500 for code, _ in exc.recipients.values()): self.retry(row, 'SMTP 暂时拒绝所有收件人')
            else: self.finish(row['id'], 'held', 'SMTP 拒绝收件邮箱，请核对设置')
        except smtplib.SMTPResponseException as exc:
            if 400 <= exc.smtp_code < 500: self.retry(row, f'SMTP 暂时拒绝（{exc.smtp_code}）')
            else: self.finish(row['id'], 'held', f'SMTP 明确拒绝（{exc.smtp_code}），请核对配置')
        except (ConnectionRefusedError, smtplib.SMTPNotSupportedError, ssl.SSLCertVerificationError):
            self.finish(row['id'], 'held', 'SMTP 连接、加密或证书配置错误，请核对设置')
        except Exception:
            self.finish(row['id'], 'uncertain', '邮件发送结果不确定，请核对收件箱后再重试')

    def retry(self, row, reason):
        attempts = row['attempts'] + 1
        with self.db.connect() as db:
            db.execute('UPDATE monitor_outbox SET status=?,next_at=?,last_error=? WHERE id=?',
                ('pending' if attempts < 5 else 'held',time.time()+min(3600,60*2**attempts),reason,row['id']))

    def finish(self, row_id, status, reason):
        with self.db.connect() as db:
            db.execute('UPDATE monitor_outbox SET status=?,last_error=?,sent_at=? WHERE id=?',
                (status,reason,now() if status=='sent' else None,row_id))
