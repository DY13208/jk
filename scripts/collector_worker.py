"""Host Chrome worker. HTTP transport only; never opens the live SQLite database."""
import argparse
import asyncio
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def api(base,path,body=None):
    parts=urlsplit(base)
    if parts.scheme!='http' or parts.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('采集工作进程只能连接本机 HTTP 后台')
    token=(ROOT/'data'/'collector-worker.token').read_text(encoding='ascii').strip()
    request=urllib.request.Request(base.rstrip('/')+'/api/collector/worker/'+path,
        data=json.dumps(body or {}).encode(),headers={'Content-Type':'application/json','x-collector-token':token},method='POST')
    # Never send the token through an environment proxy or follow HTTP redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args): return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=15) as response:
        return json.load(response)


def child(job_path,result_path):
    from adapters import REGISTRY
    from scripts.native_chrome import browser_factory
    from crawl4ai_run.run_crawl4ai import CollectionError
    site=json.loads(Path(job_path).read_text(encoding='utf-8'))
    try:
        document=asyncio.run(asyncio.wait_for(REGISTRY[site['adapter']](site,factory=browser_factory(site)),timeout=270))
        result={'document':document}
    except CollectionError as exc: result={'error':str(exc),'status':exc.status}
    except Exception as exc:
        detail=re.sub(r'https?://\S+','<url>',' '.join(str(exc).split()))[:180]
        result={'error':'本机采集失败：'+type(exc).__name__+('：'+detail if detail else '')+'；未提交快照'}
    Path(result_path).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')


def execute(site):
    folder=ROOT/'data'/'collector-tmp';folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as work:
        job=Path(work)/'job.json';result=Path(work)/'result.json'
        job.write_text(json.dumps(site),encoding='utf-8')
        try:
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child',str(job),str(result)],
                cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                timeout=280,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return json.loads(result.read_text(encoding='utf-8'))
        except subprocess.TimeoutExpired:
            from scripts.native_chrome import stop_browser
            stop_browser(site)
            return {'error':'本机 Chrome 采集超时；未提交快照'}
        except Exception: return {'error':'本机采集子进程中断；未提交快照'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--server',default='http://127.0.0.1:8080')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--child',nargs=2)
    args=parser.parse_args()
    if args.child: child(*args.child);return
    lock_path=ROOT/'data'/'collector-worker.lock';lock_path.parent.mkdir(exist_ok=True)
    lock=lock_path.open('a+b')
    if os.name=='nt':
        import msvcrt
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError: return
    stop=threading.Event()
    def heartbeat():
        while not stop.is_set():
            try: api(args.server,'heartbeat')
            except Exception: pass
            stop.wait(10)
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    try:
        while True:
            try:
                job=api(args.server,'claim')['job']
                if job:
                    result=execute(job['site'])
                    api(args.server,'result',{'id':job['id'],'claim':job['claim'],'result':result})
            except Exception: pass  # Failed delivery expires on server; never blindly rerun collection.
            if args.once: break
            stop.wait(3)
    finally: stop.set();thread.join(timeout=16);lock.close()


if __name__=='__main__': main()
