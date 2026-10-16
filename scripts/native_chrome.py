"""Start only an app-owned Chrome instance and attach Crawl4AI through loopback CDP."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import socket
import urllib.request
import json

ROOT=Path(__file__).resolve().parents[1]


def chrome_executable():
    candidates=[os.getenv('MONITOR_CHROME_PATH',''),
        str(Path(os.getenv('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'),
        str(Path(os.getenv('LOCALAPPDATA',''))/'Google/Chrome/Application/chrome.exe'),
        shutil.which('google-chrome') or '',shutil.which('chromium') or '']
    for path in candidates:
        if path and Path(path).is_file(): return path
    raise FileNotFoundError('未找到本机 Google Chrome；可设置 MONITOR_CHROME_PATH')


def profile_path(site):
    return ROOT/'data'/'chrome-profiles'/hashlib.sha256(site['url'].encode()).hexdigest()


def stop_browser(site):
    """Recover only this app's catalogue profile after a killed collection subprocess."""
    import psutil
    expected=os.path.normcase('--user-data-dir='+str(profile_path(site).resolve()))
    for process in psutil.process_iter(['name','cmdline']):
        try:
            if (process.info['name'] or '').lower() not in ('chrome.exe','chrome','google-chrome','chromium'): continue
            if not any(os.path.normcase(arg)==expected for arg in (process.info['cmdline'] or [])): continue
            process.terminate()
            try: process.wait(timeout=5)
            except psutil.TimeoutExpired: process.kill()
        except (psutil.NoSuchProcess,psutil.AccessDenied): pass


def browser_factory(site):
    @asynccontextmanager
    async def factory(headless=False):
        from crawl4ai_run.run_crawl4ai import crawler_factory,CollectionError
        profile=profile_path(site)
        profile.mkdir(parents=True,exist_ok=True)
        # Never reuse an old endpoint or attach to a user's unrelated browser.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        command=[chrome_executable(),'--user-data-dir='+str(profile.resolve()),'--remote-debugging-port='+str(port),
                 '--remote-debugging-address=127.0.0.1','--no-first-run','--no-default-browser-check','about:blank']
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            ready=False
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(120):
                if process.poll() is not None: raise CollectionError('独立 Chrome 未能启动，可能存在资料目录占用')
                try:
                    def probe():
                        with opener.open(f'http://127.0.0.1:{port}/json/version',timeout=1) as response:
                            return json.load(response)
                    value=await asyncio.to_thread(probe)
                    if str(value.get('webSocketDebuggerUrl','')).startswith(f'ws://127.0.0.1:{port}/'):
                        ready=True;break
                except (OSError,ValueError): pass
                await asyncio.sleep(.25)
            if not ready: raise CollectionError('独立 Chrome 启动超时')
            async with crawler_factory(False,cdp_url=f'http://127.0.0.1:{port}') as crawler:
                yield crawler
        finally:
            if process.poll() is None:
                process.terminate()
                try: await asyncio.to_thread(process.wait,10)
                except subprocess.TimeoutExpired: process.kill();await asyncio.to_thread(process.wait)
    return factory
