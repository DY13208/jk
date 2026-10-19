"""Authenticated internal bridge to Tencent's pinned OpenClaw Weixin modules."""
import os
import secrets
import httpx


def token(db):
    path=db.path.parent/'weixin-bridge.token'
    if not path.exists():
        path.write_text(secrets.token_urlsafe(32),encoding='ascii')
        try: path.chmod(0o600)
        except OSError: pass
    return path.read_text(encoding='ascii').strip()


async def request(db,path,body=None):
    async with httpx.AsyncClient(timeout=3 if path=='status' else 90 if path=='send' else 25,follow_redirects=False,trust_env=False) as client:
        url='http://weixin-bridge:8091/'+path
        response=await client.request('GET' if body is None else 'POST',url,
            headers={'x-weixin-token':token(db)},json=body)
        response.raise_for_status()
        return response.json()


async def status(db):
    try: return await request(db,'status')
    except Exception: return {'running':False,'bound':False,'ready':False,'auth':{},'message':'微信连接服务未运行或尚在启动'}
