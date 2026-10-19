"""Reuse the tested JsonCssExtractionStrategy/Odoo rule, with production safety gates."""
import asyncio
import json
import os
import re
import time
import hashlib
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup

BASE = 'https://www.lecomte-alpirando.be'
START = BASE + '/shop?attribute_value=729-2672'
OUT = Path(os.getenv('OUTPUT_DIR', str(Path(__file__).resolve().parents[1] / 'data' / 'runs')))
schema = {
    'name': 'products', 'baseSelector': 'div.tp-product-item',
    'baseFields': [{'name': 'product_template_id', 'type': 'attribute', 'attribute': 'data-product-template-id'}],
    'fields': [
        {'name': 'product_variant_id', 'selector': 'input[name=product_id]', 'type': 'attribute', 'attribute': 'value'},
        {'name': 'name', 'selector': 'h6.tp-product-title a', 'type': 'attribute', 'attribute': 'title'},
        {'name': 'url', 'selector': 'h6.tp-product-title a', 'type': 'attribute', 'attribute': 'href'},
        {'name': 'sale_price', 'selector': 'span[itemprop=price]', 'type': 'text'},
        {'name': 'currency', 'selector': 'span[itemprop=priceCurrency]', 'type': 'text'},
        {'name': 'original_price', 'selector': 'del.compare-price .oe_currency_value', 'type': 'text'},
        {'name': 'discount', 'selector': '.tp-discount-percentage', 'type': 'text'},
        {'name': 'brand', 'selector': '[itemprop=brand]', 'type': 'attribute', 'attribute': 'content'},
        {'name': 'label', 'selector': '.tp-product-label span', 'type': 'text'},
        # Optional: Odoo product image for email thumbnails; missing images never fail a crawl.
        {'name': 'image', 'selector': 'img[src*="/web/image/"]', 'type': 'attribute', 'attribute': 'src'},
    ],
}


class CollectionError(ValueError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def money(value, decimal_separator='auto'):
    if value is None or not str(value).strip(): return None
    text = str(value).replace('\u00a0', '').replace(' ', '').strip()
    if decimal_separator == 'auto':
        decimal_separator = ',' if ',' in text and text.rfind(',') > text.rfind('.') and len(text.rsplit(',',1)[1]) == 2 else '.'
    text = text.replace('.', '').replace(',', '.') if decimal_separator == ',' else text.replace(',', '')
    try:
        number = Decimal(text)
        if not number.is_finite() or number < 0: raise ValueError()
        return str(number.quantize(Decimal('0.01')))
    except Exception as exc:
        raise CollectionError('商品价格格式无效') from exc


def clean(p, page, base=BASE, *, odoo=True, decimal_separator='auto'):
    canon = urljoin(base, p.get('url') or '').split('?')[0].split('#')[0]
    slug = re.search(r'-(\d+)$', canon)
    pid = str(p.get('product_template_id') or '').strip()
    if not pid or (odoo and (not slug or slug.group(1) != pid)):
        raise CollectionError('稳定商品 ID 缺失或与 Odoo URL 不一致')
    if urlsplit(canon).netloc != urlsplit(base).netloc or not p.get('url'):
        raise CollectionError('商品链接不在配置的网站内')
    return {'product_id': pid, 'product_variant_id': p.get('product_variant_id'),
        'slug_id': slug.group(1) if slug else None, 'name': (p.get('name') or '').strip(),
        'brand': p.get('brand'), 'sale_price': money(p.get('sale_price'), decimal_separator),
        'original_price': money(p.get('original_price'), decimal_separator),
        'currency': (p.get('currency') or '').strip(), 'discount': (p.get('discount') or '').strip() or None,
        'label': (p.get('label') or '').strip() or None, 'url': canon, 'listing_page': page,
        'sku': p.get('sku'), 'stock': p.get('stock') or 'UNKNOWN',
        'image': image_url(p.get('image'), base) or (odoo_image(base, pid) if odoo else None)}


def odoo_image(base, pid):
    """Odoo's standard public thumbnail for a product template (used only as an email image)."""
    if not str(pid or '').isdigit(): return None
    parts = urlsplit(base)
    return f'{parts.scheme}://{parts.netloc}/web/image/product.template/{pid}/image_256'


def image_url(value, base):
    """Absolute same-site http(s) image URL, or None."""
    if not value or not str(value).strip(): return None
    url = urljoin(base, str(value).strip())
    parts = urlsplit(url)
    return url if parts.scheme in ('http', 'https') and parts.netloc == urlsplit(base).netloc else None


def safe_launch_args(args):
    result = dict(args)
    result['args'] = [a for a in args.get('args', [])
        if 'AutomationControlled' not in a and not a.startswith('--ignore-certificate-errors')]
    return result


class NativeUARobotsParser:
    """Delegate to Crawl4AI's RobotsParser, but never hand it an empty User-Agent."""
    def __init__(self, parser, user_agent):
        self.parser, self.user_agent = parser, (user_agent or '').strip() or '*'

    async def can_fetch(self, url, user_agent=None):
        return await self.parser.can_fetch(url, (user_agent or '').strip() or self.user_agent)

    def __getattr__(self, name):
        return getattr(self.parser, name)


async def native_user_agent(crawler):
    """Read the UA Chrome really sends (no spoofing); fall back to the robots wildcard."""
    try:
        browser = crawler.crawler_strategy.browser_manager.browser
        probe = await browser.new_page()
        try: return await probe.evaluate('navigator.userAgent')
        finally: await probe.close()
    except Exception:
        return '*'


def access_control_page(html):
    """True only for real block/challenge interstitials.

    Normal Cloudflare-fronted pages (including Lecomte's catalogue) embed the passive bot-detection
    script /cdn-cgi/challenge-platform/scripts/jsd/main.js; that alone is not an access-control page.
    """
    low = (html or '').lower()
    if any(marker in low for marker in ('your request was blocked', 'cf-chl-', '_cf_chl_opt', 'cf-browser-verification')):
        return True
    if re.search(r'/cdn-cgi/challenge-platform/(?:h/|orchestrate/)', low):
        return True
    title = re.search(r'<title[^>]*>(.*?)</title>', low, re.S)
    return bool(title and title.group(1).strip() in ('just a moment...', 'attention required! | cloudflare', 'access denied'))


def failure_detail(result):
    """First meaningful line of Crawl4AI's error, without URLs/HTML, so logs show the real cause."""
    text = str(getattr(result, 'error_message', '') or '')
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # Crawl4AI appends a traceback; the last line holds the exception type and message.
    line = next((l for l in reversed(lines) if re.match(r'^[A-Za-z_.]+(Error|Exception)\b', l)), lines[0] if lines else '')
    line = re.sub(r'https?://\S+', '<url>', line)
    return line[:200]


def session_state_path(url):
    """App-owned state only: never accept an arbitrary profile path from site configuration."""
    root = Path(os.getenv('DB_PATH', str(Path(__file__).resolve().parents[1]/'data'/'snapshots.db'))).parent
    return root/'browser-sessions'/(hashlib.sha256(url.encode()).hexdigest()+'.json')


async def save_session_state(crawler, path):
    # Crawl4AI 0.9.4 stores (context, page, timestamp) for the configured session.
    session = crawler.crawler_strategy.browser_manager.sessions.get('catalog')
    if not session: raise CollectionError('浏览器会话缺失；未保存会话状态')
    state = await session[0].storage_state()
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(state,stream,ensure_ascii=False)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def crawler_factory(headless=False, state_path=None, cdp_url=None):
    from importlib.metadata import version
    if version('crawl4ai') != '0.9.4':
        raise CollectionError('浏览器安全扩展仅支持已核查的 Crawl4AI 0.9.4')
    from crawl4ai import AsyncWebCrawler, BrowserConfig
    from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
    from crawl4ai.browser_manager import BrowserManager
    class PolicyBrowserManager(BrowserManager):
        def _build_browser_args(self): return safe_launch_args(super()._build_browser_args())
    class PolicyWebCrawler(AsyncWebCrawler):
        async def start(self):
            await super().start()
            # Crawl4AI 0.9.4 passes browser_config.user_agent straight to urllib's RobotFileParser,
            # which calls .split() on it. We keep the native Chrome UA (user_agent=None), so give the
            # robots check the real UA string instead of None. robots.txt checking stays enabled.
            self.robots_parser = NativeUARobotsParser(self.robots_parser, await native_user_agent(self))
            return self
    cfg = BrowserConfig(headless=headless, chrome_channel='chrome', channel='chrome',
        enable_stealth=False, ignore_https_errors=False, verbose=False,
        cdp_url=cdp_url,
        storage_state=str(state_path) if state_path and state_path.is_file() else None)
    cfg.user_agent = None  # Keep native Chrome UA, not BrowserConfig's fixed default.
    cfg.browser_hint = None
    strategy = AsyncPlaywrightCrawlerStrategy(browser_config=cfg)
    strategy.browser_manager = PolicyBrowserManager(browser_config=cfg, logger=strategy.logger)
    return PolicyWebCrawler(config=cfg, crawler_strategy=strategy)


def pagination(html, current, options):
    soup = BeautifulSoup(html, 'html.parser')
    pager = soup.select_one(options.get('pager_selector', '.tp-product-pager, [data-total-count][data-page-count]'))
    if pager is None: raise CollectionError('缺少站点声明的总数/分页信息')
    try:
        total = int(pager[options.get('total_attribute', 'data-total-count')])
        pages = int(pager[options.get('pages_attribute', 'data-page-count')])
        if total < 0 or pages < 1 or pages > options.get('max_pages', 200): raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise CollectionError('站点分页信息无效') from exc
    links = {1: current}
    pattern = options.get('page_pattern', r'/shop/page/(\d+)')
    for a in soup.select(options.get('page_link_selector', 'a[href]')):
        href = urljoin(current, a.get('href', ''))
        match = re.search(pattern, urlsplit(href).path)
        if match:
            if urlsplit(href).netloc != urlsplit(current).netloc: raise CollectionError('分页跳转至其他网站')
            if urlsplit(href).query != urlsplit(current).query: continue
            links[int(match.group(1))] = href
    if options.get('pagination_mode') != 'load_more' and set(range(1, pages + 1)) - links.keys():
        raise CollectionError('分页链接不完整；禁止遗漏页面')
    return total, pages, links


NEXT_SELECTORS = ('link[rel=next], a[rel=next], .pagination .next a, a.next, li.next a, a.pagination__next, '
                  'a[class*=next], a[aria-label*=next i], a[aria-label*=suivant i], a[title*=next i], a[title*=suivant i]')
NEXT_TEXT = re.compile(r'^\s*(next|suivant|page suivante|weiter|nächste|siguiente|successivo|show more|load more|voir plus|›|»|→|>)\s*$', re.I)


def next_page_link(html, current, options, visited):
    """Follow the site's own "next page" link: rel=next / common next selectors / "next" text / ?page=N+1 links.
    Same host only; never returns an already visited URL."""
    soup = BeautifulSoup(html, 'html.parser')
    here = urlsplit(current)
    def usable(a):
        raw = (a.get('href') or '').strip()
        if not raw or raw.startswith(('#', 'javascript:')) or 'disabled' in (a.get('class') or []): return None
        href = urljoin(current, raw).split('#')[0]
        if href in visited or href == current: return None
        if urlsplit(href).netloc != here.netloc: raise CollectionError('分页跳转到其他网站')
        return href
    candidates = list(soup.select(options.get('next_selector') or NEXT_SELECTORS))
    candidates += [a for a in soup.select('a[href]') if NEXT_TEXT.match(a.get_text(' ', strip=True) or '')]
    for a in candidates:
        href = usable(a)
        if href: return href
    # ?page=N / &p=N / /page/N: link to the page right after the current one.
    match = re.search(r'(?:[?&](?:page|p|pg)=|/page/)(\d+)', current)
    want = (int(match.group(1)) if match else 1) + 1
    for a in soup.select('a[href]'):
        raw = a.get('href') or ''
        if re.search(r'(?:[?&](?:page|p|pg)=|/page/)%d(?:\D|$)' % want, raw):
            href = usable(a)
            if href and urlsplit(href).path == here.path: return href
    return None


PRICE_RE = re.compile(r'(?:[€$£]|EUR|USD|CAD|CHF|GBP)\s*\d|\d[\d.,\s\u00a0]*[.,]\d{2}\s*(?:[€$£]|EUR|USD|CAD|CHF|GBP|kr)?', re.I)
PRICE_PATTERN = r'(\d[\d.,\s\u00a0]*\d|\d)'


def _css(element):
    classes = [c for c in (element.get('class') or []) if re.fullmatch(r'[A-Za-z_][\w-]*', c)]
    return element.name + ''.join('.' + c for c in classes[:2])


def auto_schema(html, url):
    """Heuristic Crawl4AI JsonCss schema for a product listing: the largest group of repeated, same-class
    elements that each contain a same-site link and a price. Used when a site has no configured schema."""
    soup = BeautifulSoup(html, 'html.parser')
    for junk in soup.select('script,style,noscript,svg,header,footer,nav'): junk.decompose()
    host = urlsplit(url).netloc
    def has_link(el):
        for a in el.select('a[href]'):
            h = urljoin(url, a['href'])
            if urlsplit(h).netloc == host and not a['href'].startswith('#') and urlsplit(h).path not in ('', '/'): return True
        return False
    groups = {}
    for el in soup.find_all(class_=True):
        groups.setdefault(_css(el), []).append(el)
    best = None
    for selector, items in groups.items():
        if len(items) < 3 or '.' not in selector: continue
        good = [el for el in items if has_link(el) and PRICE_RE.search(el.get_text(' ', strip=True))]
        if len(good) < max(3, 0.8 * len(items)): continue
        depth = len(list(good[0].parents))
        key = (len(good), -depth)
        if not best or key > best[0]: best = (key, selector, good)
    if not best: raise CollectionError('无法自动识别商品列表；请在站点配置中提供 schema')
    _, base, cards = best
    def rel(el):
        """Selector for el inside a card; class-less elements are anchored to their parent's classes."""
        if el.get('class') or el.parent is None or el.parent in cards: return _css(el)
        return _css(el.parent) + ' > ' + el.name
    def pick(candidates_of, unique=False, reject=None):
        counts, values = {}, {}
        for card in cards:
            for sel in dict.fromkeys(rel(el) for el in candidates_of(card)):
                el = card.select_one(sel)
                if el is None: continue
                counts[sel] = counts.get(sel, 0) + 1
                values.setdefault(sel, []).append(el.get('href'))
        ranked = []
        for sel, n in counts.items():
            if n < 0.8 * len(cards): continue
            if unique and len(set(values[sel])) < 0.8 * n: continue
            if reject and any(reject(v or '') for v in values[sel]): continue
            ranked.append(sel)
        return ranked[0] if ranked else None
    def links(card):
        return [a for a in card.select('a[href]') if not a['href'].startswith(('#', 'javascript:'))]
    def headings(card):
        return card.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']) + card.select('[class*=title], [class*=name]')
    def prices(card, old=False):
        found = []
        for el in card.find_all(True):
            text = el.get_text(' ', strip=True)
            if not text or len(text) > 30 or not PRICE_RE.search(text): continue
            if any(PRICE_RE.search(c.get_text(' ', strip=True) or '') for c in el.find_all(True)): continue  # not a leaf price
            cls = ' '.join(el.get('class') or []) + ' ' + ' '.join(el.parent.get('class') or [])
            is_old = el.name in ('del', 's', 'strike') or el.parent.name in ('del', 's', 'strike') or \
                re.search(r'old|compare|was-|strike|before|crossed', cls, re.I)
            if bool(is_old) == old: found.append(el)
        return sorted(found, key=lambda el: 0 if re.search(r'sale|special|current|now', ' '.join(el.get('class') or []), re.I) else 1)
    def image(card): return card.find_all('img')[:1]
    junk_link = lambda href: bool(re.search(r'/(account|login|cart|basket|panier|wishlist|compare)\b', href, re.I))
    url_sel = pick(lambda c: sorted(links(c), key=lambda a: 0 if a.get_text(strip=True) else 1), unique=True, reject=junk_link)
    name_sel, price_sel = pick(headings), pick(prices)
    if price_sel:  # cards without the main price element (e.g. sale cards) may use another price element
        extra = []
        for card in cards:
            if card.select_one(', '.join([price_sel] + extra)) is None:
                found = prices(card)
                if found: extra.append(rel(found[0]))
        price_sel = ', '.join(dict.fromkeys([price_sel] + extra))
    if not url_sel or not price_sel: raise CollectionError('无法自动识别商品链接或价格；请在站点配置中提供 schema')
    img_sel = pick(image)
    # Old/compare-at prices exist only on discounted cards, so any card is enough evidence.
    old_sel = ', '.join(dict.fromkeys(rel(found[0]) for found in (prices(c, True) for c in cards) if found)) or None
    fields = [{'name': 'product_template_id', 'selector': url_sel, 'type': 'attribute', 'attribute': 'href'},
              {'name': 'url', 'selector': url_sel, 'type': 'attribute', 'attribute': 'href'},
              {'name': 'name', 'selector': name_sel or url_sel, 'type': 'text'},
              {'name': 'sale_price', 'selector': price_sel, 'type': 'regex', 'pattern': PRICE_PATTERN}]
    if old_sel and old_sel != price_sel:
        fields.append({'name': 'original_price', 'selector': old_sel, 'type': 'regex', 'pattern': PRICE_PATTERN})
    if img_sel:
        sample = cards[0].select_one(img_sel)
        attribute = next((a for a in ('data-src', 'data-original', 'src') if sample is not None and sample.get(a)
                          and not str(sample.get(a)).startswith('data:')), 'src')
        fields.append({'name': 'image', 'selector': img_sel, 'type': 'attribute', 'attribute': attribute})
    return {'name': 'products', 'baseSelector': base, 'fields': fields, 'auto_generated': True}


async def collect(config, factory=None, run_config_factory=None):
    if run_config_factory is None:
        from crawl4ai import CrawlerRunConfig, CacheMode, JsonCssExtractionStrategy
        def run_config_factory():
            return CrawlerRunConfig(cache_mode=CacheMode.BYPASS, check_robots_txt=True,
                extraction_strategy=JsonCssExtractionStrategy(config.get('schema', schema)),
                wait_until='domcontentloaded', page_timeout=60000, delay_before_return_html=1.5,
                session_id='catalog', override_navigator=False, simulate_user=False, magic=False)
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    products, seen, logs = [], set(), []
    load_more = config.get('pagination_mode') == 'load_more'
    # CSS sites default to following next-page links; Odoo/Lecomte keeps its pager/load-more behaviour.
    next_link = config.get('pagination_mode') == 'next_link' or (
        config.get('pagination_mode') in (None, '', 'auto') and config.get('odoo', True) is False)
    generated = None
    state_path = session_state_path(config['url']) if config.get('persistent_session') else None
    browser = factory(False) if factory else crawler_factory(False,state_path=state_path)
    async with browser as crawler:
        denied = []
        if load_more and hasattr(crawler,'crawler_strategy'):
            async def observe(page,context,**kwargs):
                def response_received(response):
                    if (response.status in (403,429) and response.request.resource_type in ('document','xhr','fetch')
                        and urlsplit(response.url).netloc == urlsplit(config['url']).netloc):
                        denied.append((response.status,response.request.resource_type,urlsplit(response.url).path))
                page.on('response',response_received)
            crawler.crawler_strategy.set_hook('on_page_context_created',observe)
        urls, total, pages, index = {1: config['url']}, None, 1, 1
        while index <= pages:
            run_config = run_config_factory()
            if load_more and index > 1:
                selector = json.dumps(config.get('load_more_selector','.tp-load-more-btn'))
                base_selector = json.dumps(config.get('schema',schema)['baseSelector'])
                session = (crawler.crawler_strategy.browser_manager.sessions.get('catalog')
                           if hasattr(crawler,'crawler_strategy') else None)
                click_script=f'document.querySelector({selector})?.click();'
                if session:
                    # Use a real input event on the existing Crawl4AI page (same as a UI click).
                    await session[1].locator(json.loads(selector)).click(timeout=15000)
                    click_script=None
                run_config = run_config.clone(js_only=True,
                    js_code_before_wait=click_script,
                    wait_for=f'js:() => document.querySelectorAll({base_selector}).length > {len(products)}',
                    wait_for_timeout=20000)
            result = await crawler.arun(url=config['url'] if load_more else urls[index], config=run_config)
            if denied:
                code, resource, path = denied[0]
                raise CollectionError(f'加载商品时站点拒绝访问 HTTP {code}（{resource} {path[:120]}）；监控已暂停',code)
            initial_status, html = result.status_code, result.html or ''
            if not result.success and 'robots.txt' in (getattr(result,'error_message','') or '').lower():
                raise CollectionError('站点 robots.txt 拒绝自动化访问；监控已暂停',403)
            status = getattr(result, 'redirected_status_code', None) or initial_status
            if initial_status in (403, 429): status = initial_status
            if status in (403, 429): raise CollectionError(f'站点拒绝访问 HTTP {status}；监控已暂停', status)
            if not result.success or status != 200:
                detail = failure_detail(result)
                if 'robotparser' in str(getattr(result, 'error_message', '') or '') and 'NoneType' in detail:
                    raise CollectionError('robots.txt 检查发生 User-Agent 兼容性错误；未提交快照', status)
                where = f'第 {index} 页访问失败（HTTP {status if status is not None else "未知"}）'
                raise CollectionError(where + (f'：{detail}' if detail else '') + '；未提交快照', status)
            if access_control_page(html):
                raise CollectionError('站点返回访问控制页面；监控已暂停', 403)
            final_url = getattr(result, 'redirected_url', None) or (config['url'] if load_more else urls[index])
            if urlsplit(final_url).netloc != urlsplit(config['url']).netloc:
                raise CollectionError('入口重定向至其他网站')
            if next_link:
                page_total = page_count = None
            else: page_total, page_count, page_urls = pagination(html, final_url, config)
            if next_link: pass
            elif index == 1: total, pages, urls = page_total, page_count, page_urls
            elif (page_total, page_count) != (total, pages): raise CollectionError('采集期间站点总数或分页发生变化')
            if config.get('odoo', True) is False and not config.get('schema'):
                from crawl4ai import JsonCssExtractionStrategy
                generated = auto_schema(html, final_url)  # per page: sale-only fields vary by page
                items = JsonCssExtractionStrategy(generated).extract(final_url, html)
            else:
                try: items = json.loads(result.extracted_content or '[]')
                except ValueError as exc: raise CollectionError('商品提取结果无效') from exc
            previous = {p['product_id']:p for p in products} if load_more else {}
            before = len(products)
            if load_more: products, seen = [], set()
            for item in items:
                product = clean(item, index, config['url'], odoo=config.get('odoo', True),
                    decimal_separator=config.get('decimal_separator', 'auto'))
                if product['product_id'] in seen: raise CollectionError('分页中出现重复商品 ID；可能发生列表移动')
                if not product['name'] or product['sale_price'] is None: raise CollectionError('商品名称或价格缺失')
                if product['product_id'] in previous:
                    old = previous[product['product_id']]
                    if any(old[k] != product[k] for k in ('name','sale_price','original_price','url')):
                        raise CollectionError('动态加载期间已有商品发生变化；未提交快照')
                    product['listing_page'] = old['listing_page']
                seen.add(product['product_id']); products.append(product)
            if load_more and (not set(previous).issubset(seen) or (index>1 and len(products)<=before)):
                raise CollectionError('动态加载没有增长或丢失已有商品；未提交快照')
            logs.append({'page': index, 'status': status, 'initial_status':initial_status,
                         'items': len(products)-before if load_more else len(items)})
            if next_link:
                if not items: raise CollectionError(f'第 {index} 页没有提取到商品；未提交快照')
                following = next_page_link(html, final_url, config, set(urls.values()) | {final_url})
                if following:
                    if index >= config.get('max_pages', 50): raise CollectionError('分页超过 max_pages 上限；未提交快照')
                    urls[index + 1], pages = following, index + 1
                total = len(products)
            index += 1
            if index <= pages: await asyncio.sleep(config.get('page_delay', 1.5))
        if len(products) != total: raise CollectionError('商品 ID 集合与站点声明总数不一致')
        if state_path: await save_session_state(crawler,state_path)
    if len(products) != total: raise CollectionError('商品 ID 集合与站点声明总数不一致')
    return {'meta': {'tool': 'crawl4ai', 'started': started, 'ended': datetime.now(timezone.utc).isoformat(),
        'duration_s': round(time.monotonic() - clock, 2), 'site_total': total, 'site_pages': pages,
        'extracted': len(products), 'pages': logs, 'complete': True}, 'products': products}


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default=START)
    parser.add_argument('--permission-confirmed', action='store_true')
    args = parser.parse_args()
    if not args.permission_confirmed: parser.error('请先核对站点政策及访问许可，再使用 --permission-confirmed')
    document = await collect({'url': args.url})
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / ('crawl4ai_' + str(time.time_ns()) + '.json')
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)


if __name__ == '__main__': asyncio.run(main())
