"""Local Gmail settings; secrets never enter SQLite or API responses."""
import base64
import json
import os
import secrets
import smtplib
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .email_delivery import recipients, send_email

_lock = threading.Lock()


def load(db):
    folder = db.path.parent
    target = folder / 'gmail.enc'
    if not target.exists(): return None
    return json.loads(Fernet((folder/'gmail.key').read_bytes()).decrypt(target.read_bytes()))


def save(db, account, password):
    with _lock:
        previous = load(db)
        password = password.replace(' ', '')
        if not password:
            if not previous or previous['sender'] != account: raise ValueError('请输入此账号的应用专用密码')
            password = previous['password']
        if len(password) != 16 or not password.isascii() or not password.isalpha():
            raise ValueError('请输入 Google 生成的 16 位应用专用密码')
        key = db.path.parent/'gmail.key'
        if not key.exists():
            with key.open('xb') as stream: stream.write(Fernet.generate_key())
            os.chmod(key, 0o600)
        value = dict(host='smtp.gmail.com', port=587, mode='starttls', sender=account, username=account, password=password)
        target = db.path.parent/'gmail.enc'
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(Fernet(key.read_bytes()).encrypt(json.dumps(value).encode()))
        os.chmod(temporary, 0o600)
        temporary.replace(target)


class Settings(BaseModel):
    account: str = Field(max_length=254)
    password: str = Field(default='', max_length=64, repr=False)


class Attachment(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    data: str = Field(max_length=7_000_000)


class Draft(BaseModel):
    to: str = Field(max_length=2000)
    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=100000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)
    request_id: str = Field(pattern=r'^[a-zA-Z0-9-]{16,64}$')
    confirm: bool = False


def router(db):
    api = APIRouter(prefix='/api/gmail')
    def initialize(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS gmail_sent (
            request_id TEXT PRIMARY KEY, created TEXT, recipients TEXT, subject TEXT, status TEXT, message TEXT)''')

    @api.get('/status')
    def status():
        config = load(db)
        with db.connect() as conn:
            initialize(conn)
            rows = [dict(r) for r in conn.execute('SELECT * FROM gmail_sent ORDER BY created DESC LIMIT 50')]
        for row in rows:
            if row['status'] == 'sending': row.update(status='uncertain', message='发送处理中或进程中断，请先核对收件箱')
        return dict(configured=bool(config), account=config['sender'] if config else '', items=rows)

    @api.put('/settings')
    def settings(value: Settings):
        try:
            addresses = recipients(value.account)
            if len(addresses)!=1 or addresses[0].split('@')[1].lower() not in ('gmail.com','googlemail.com'):
                raise ValueError('请填写个人 Gmail 邮箱地址')
            save(db, addresses[0], value.password)
        except ValueError as exc: raise HTTPException(400, str(exc)) from None
        return {'ok':True}

    @api.post('/test')
    def test():
        config = load(db)
        if not config: raise HTTPException(400,'请先保存 Gmail 配置')
        try:
            with smtplib.SMTP(config['host'],config['port'],timeout=15) as client:
                client.ehlo(); client.starttls(context=ssl.create_default_context()); client.ehlo()
                client.login(config['username'],config['password'])
        except Exception: raise HTTPException(400,'连接或登录失败，请检查网络、两步验证和应用专用密码') from None
        return {'message':'连接与登录成功；未发送邮件'}

    @api.post('/send')
    def send(value: Draft):
        if not value.confirm: raise HTTPException(400,'请确认邮件后再发送')
        config = load(db)
        if not config: raise HTTPException(400,'请先保存 Gmail 配置')
        try:
            config['recipients'] = recipients(value.to)
            if '\r' in value.subject or '\n' in value.subject: raise ValueError('主题不能换行')
            mail = EmailMessage()
            mail['From']=config['sender']; mail['To']=', '.join(config['recipients']); mail['Subject']=value.subject
            mail['Message-ID']=f'<{secrets.token_hex(16)}@gmail.com>'
            mail.set_content(value.body)
            total=0
            for attachment in value.attachments:
                try: content=base64.b64decode(attachment.data,validate=True)
                except Exception: raise ValueError('附件编码无效') from None
                total+=len(content)
                if total>5_000_000: raise ValueError('附件总大小不能超过 5 MB')
                mail.add_attachment(content,maintype='application',subtype='octet-stream',filename=attachment.name)
        except ValueError as exc: raise HTTPException(400,str(exc)) from None
        with db.connect() as conn:
            initialize(conn)
            row=conn.execute('SELECT * FROM gmail_sent WHERE request_id=?',(value.request_id,)).fetchone()
            if row: return dict(row)
            conn.execute('INSERT INTO gmail_sent VALUES(?,?,?,?,?,?)',(value.request_id,datetime.now(timezone.utc).isoformat(),value.to,value.subject,'sending','发送处理中'))
        state, message='sent','SMTP 已接受；请在收件箱核对最终送达'
        try:
            if send_email(config,mail): state,message='uncertain','部分收件人被拒绝，请核对后处理'
        except (smtplib.SMTPAuthenticationError,smtplib.SMTPRecipientsRefused): state,message='held','登录或收件人被拒绝，请核对配置'
        except Exception: state,message='uncertain','发送结果不确定，请核对收件箱；不会自动重发'
        with db.connect() as conn:
            conn.execute('UPDATE gmail_sent SET status=?,message=? WHERE request_id=?',(state,message,value.request_id))
        return dict(status=state,message=message)
    return api
