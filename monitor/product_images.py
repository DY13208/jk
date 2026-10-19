"""Bounded public-image fetch, pinned DNS and Pillow normalization; no cookies or redirects."""
import asyncio
import base64
import http.client
import io
import ipaddress
import socket
import ssl
import time
import warnings
from urllib.parse import urlsplit, quote
from PIL import Image

LIMIT=2*1024*1024


def target(value):
    if not isinstance(value,str) or len(value)>4096 or any(ord(c)<32 for c in value):
        raise ValueError('invalid image URL')
    url=urlsplit(value)
    if url.scheme not in ('http','https') or not url.hostname or url.username is not None or url.password is not None:
        raise ValueError('invalid image URL')
    port=url.port or (443 if url.scheme=='https' else 80)
    if port!=(443 if url.scheme=='https' else 80): raise ValueError('image port not allowed')
    host=url.hostname.encode('idna').decode('ascii')
    addresses=list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('image must use public addresses')
    return url,host,port,addresses[0]


def normalize(data):
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('JPEG','PNG','WEBP','GIF') or image.width*image.height>20_000_000:
                raise ValueError('unsupported image')
            image.thumbnail((1200,1200))
            output=io.BytesIO()
            image.convert('RGB').save(output,format='JPEG',quality=85)
            result=output.getvalue()
    if len(result)>LIMIT: raise ValueError('image too large')
    return {'mime':'image/jpeg','data':base64.b64encode(result).decode('ascii')}


def download(value):
    url,host,port,ip=target(value)
    connection=http.client.HTTPConnection(host,port,timeout=8)
    try:
        # Connect to the validated address, never resolve the hostname a second time.
        sock=socket.create_connection((ip,port),timeout=8)
        connection.sock=sock
        if url.scheme=='https':
            connection.sock=ssl.create_default_context().wrap_socket(sock,server_hostname=host)
        path=quote(url.path or '/',safe="/%:@-._~!$&'()*+,;=")
        if url.query: path+='?'+quote(url.query,safe="/?%:@-._~!$&'()*+,;=")
        connection.request('GET',path,headers={'Accept':'image/*','User-Agent':'CatalogMonitor/1.0 product-image'})
        response=connection.getresponse()
        if response.status!=200: raise ValueError('image unavailable')
        if int(response.getheader('Content-Length','0'))>LIMIT: raise ValueError('image too large')
        data=bytearray();deadline=time.monotonic()+10
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('image timeout')
            if connection.sock: connection.sock.settimeout(min(remaining,5))
            chunk=response.read1(min(65536,LIMIT+1-len(data)))
            if not chunk: break
            data.extend(chunk)
            if len(data)>LIMIT: raise ValueError('image too large')
        return normalize(bytes(data))
    finally: connection.close()


async def attachment(url):
    if not url:return None
    try:return await asyncio.wait_for(asyncio.to_thread(download,url),timeout=15)
    except Exception:return None  # An optional picture never prevents the main notification.
