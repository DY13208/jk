import asyncio
from types import SimpleNamespace

from crawl4ai_run.run_crawl4ai import NativeUARobotsParser, failure_detail, native_user_agent
from monitor.service import describe


class Recorder:
    def __init__(self): self.seen=[]
    async def can_fetch(self, url, user_agent='*'):
        user_agent.split('/')  # what urllib.robotparser does; fails on None
        self.seen.append(user_agent); return True


def test_robots_check_never_receives_empty_user_agent():
    inner=Recorder();parser=NativeUARobotsParser(inner,'Mozilla/5.0 Chrome/141')
    assert asyncio.run(parser.can_fetch('https://example.test/shop',None))
    assert asyncio.run(parser.can_fetch('https://example.test/shop','  '))
    assert asyncio.run(parser.can_fetch('https://example.test/shop','Custom/1'))
    assert inner.seen==['Mozilla/5.0 Chrome/141','Mozilla/5.0 Chrome/141','Custom/1']
    assert NativeUARobotsParser(inner,None).user_agent=='*'


def test_native_user_agent_falls_back_to_wildcard():
    assert asyncio.run(native_user_agent(SimpleNamespace())) =='*'


def test_failure_detail_shows_real_cause_without_urls():
    result=SimpleNamespace(error_message='Unexpected error in _crawl_web at line 1\nTraceback...\n  File "x"\n'
        "AttributeError: 'NoneType' object has no attribute 'split' for https://secret.example/x")
    detail=failure_detail(result)
    assert detail.startswith('AttributeError') and 'secret.example' not in detail
    assert failure_detail(SimpleNamespace(error_message=None))==''


def test_service_describe_includes_exception_message():
    assert describe(RuntimeError('Browser closed unexpectedly'))=='采集失败：RuntimeError：Browser closed unexpectedly；未提交快照'
    assert '超时' in describe(asyncio.TimeoutError())


def test_passive_cloudflare_script_is_not_a_block_page():
    from crawl4ai_run.run_crawl4ai import access_control_page
    normal=('<html><head><title>All Our Products - Lecomte</title></head><body><div class="tp-product-item"></div><script>'
            "a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';</script></body></html>")
    assert not access_control_page(normal)
    assert access_control_page('<title>Just a moment...</title><script>window._cf_chl_opt={}</script>')
    assert access_control_page('<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>')
    assert access_control_page('<h1>Your request was blocked</h1>')
