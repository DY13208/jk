"""Register independent adapters here; scheduling and storage stay site-agnostic."""
from crawl4ai_run.run_crawl4ai import collect, schema


async def odoo_lecomte(site, factory=None):
    return await collect({**site['adapter_config'], 'url': site['url'], 'odoo': True, 'schema': schema},factory=factory)


async def css_catalog(site, factory=None):
    return await collect({**site['adapter_config'], 'url': site['url'], 'odoo': False},factory=factory)


def load_robots(base, ua):
    """robots.txt fetched with the same UA as the crawl (urllib's default UA is often 403'd, which robotparser treats as disallow-all)."""
    import urllib.request, urllib.robotparser
    from crawl4ai_run.run_crawl4ai import CollectionError
    parser = urllib.robotparser.RobotFileParser(base + '/robots.txt')
    try:
        with urllib.request.urlopen(urllib.request.Request(base + '/robots.txt', headers={'User-Agent': ua}), timeout=30) as response:
            parser.parse(response.read().decode('utf-8', 'replace').splitlines())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403): raise CollectionError(f'robots.txt 返回 HTTP {exc.code}；任务已暂停', exc.code)
        parser.parse([])  # 404 etc. = no restrictions (robotparser semantics)
    return parser


async def shopify_json(site, factory=None):
    """Shopify storefront /products.json for a collection URL (paginated ?limit=250&page=N)."""
    import asyncio
    return await asyncio.to_thread(collect_shopify, site['url'], site['adapter_config'])


def collect_shopify(url, config, fetch=None):
    import json, time, urllib.request, urllib.robotparser
    from datetime import datetime, timezone
    from urllib.parse import urlsplit
    from crawl4ai_run.run_crawl4ai import CollectionError, money
    parts = urlsplit(url)
    base = f'{parts.scheme}://{parts.netloc}'
    endpoint = base + parts.path.rstrip('/') + '/products.json'
    ua = config.get('user_agent') or 'Mozilla/5.0 (compatible; product-monitor)'
    def default_fetch(target):
        request = urllib.request.Request(target, headers={'User-Agent': ua, 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, b''
    fetch = fetch or default_fetch
    if fetch is default_fetch:
        robots = load_robots(base, ua)
        if not robots.can_fetch(ua, endpoint): raise CollectionError('站点 robots.txt 拒绝自动化访问；任务已暂停', 403)
    started, clock = datetime.now(timezone.utc).isoformat(), time.monotonic()
    products, seen, logs = [], set(), []
    max_pages = config.get('max_pages', 40)
    for page in range(1, max_pages + 1):
        status, body = fetch(f'{endpoint}?limit=250&page={page}')
        if status in (403, 429): raise CollectionError(f'站点拒绝访问 HTTP {status}；任务已暂停', status)
        if status != 200: raise CollectionError(f'第 {page} 页请求失败（HTTP {status}）；未提交快照', status)
        try: items = json.loads(body)['products']
        except Exception as exc: raise CollectionError('products.json 格式无效') from exc
        logs.append({'page': page, 'status': 200, 'initial_status': 200, 'items': len(items)})
        for item in items:
            pid = str(item['id'])
            if pid in seen: raise CollectionError('翻页中出现重复商品 ID，可能发生列表移动')
            variants = item.get('variants') or []
            available = [v for v in variants if v.get('available')]
            chosen = min(available or variants, key=lambda v: float(v.get('price') or 0)) if variants else {}
            compare = chosen.get('compare_at_price')
            sale = money(chosen.get('price'), '.') if chosen else None
            original = money(compare, '.') if compare else None
            if original is not None and sale is not None and float(original) <= float(sale): original = None
            images = item.get('images') or []
            seen.add(pid)
            products.append({'product_id': pid, 'product_variant_id': str(chosen.get('id') or '') or None,
                'slug_id': None, 'name': (item.get('title') or '').strip(), 'brand': item.get('vendor'),
                'sale_price': sale, 'original_price': original, 'currency': config.get('currency', ''),
                'discount': None, 'label': None, 'url': f"{base}/products/{item['handle']}",
                'listing_page': page, 'sku': chosen.get('sku'),
                'stock': 'IN_STOCK' if available else 'OUT_OF_STOCK',
                'image': (images[0].get('src') if images else None)})
        if len(items) < 250: break
        time.sleep(config.get('page_delay', 1.5))
    else:
        raise CollectionError('分页超过 max_pages 上限；未提交快照')
    if not products: raise CollectionError('products.json 没有商品；未提交快照')
    products = [p for p in products if p['name'] and p['sale_price'] is not None]
    for log in logs: log['items'] = sum(1 for p in products if p['listing_page'] == log['page'])
    return {'meta': {'tool': 'shopify_json', 'started': started, 'ended': datetime.now(timezone.utc).isoformat(),
        'duration_s': round(time.monotonic() - clock, 2), 'site_total': len(products), 'site_pages': len(logs),
        'extracted': len(products), 'pages': logs, 'complete': True}, 'products': products}


async def bergzeit_state(site, factory=None):
    """Bergzeit listing: products are embedded as window.__initialAppState (elementsList + resultCount)."""
    import asyncio
    return await asyncio.to_thread(collect_bergzeit, site['url'], site['adapter_config'])


def collect_bergzeit(url, config, fetch=None):
    import json, re, time, urllib.request, urllib.robotparser
    from datetime import datetime, timezone
    from urllib.parse import urlsplit, urlencode, parse_qsl, urlunsplit, urljoin
    from crawl4ai_run.run_crawl4ai import CollectionError, money
    parts = urlsplit(url)
    base = f'{parts.scheme}://{parts.netloc}'
    ua = config.get('user_agent') or 'Mozilla/5.0 (compatible; product-monitor)'
    def default_fetch(target):
        request = urllib.request.Request(target, headers={'User-Agent': ua, 'Accept': 'text/html'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as exc:
            return exc.code, ''
    fetch = fetch or default_fetch
    if fetch is default_fetch:
        robots = load_robots(base, ua)
        if not robots.can_fetch(ua, url): raise CollectionError('站点 robots.txt 拒绝自动化访问；任务已暂停', 403)
    def price(text):
        found = re.search(r'\d[\d.\s\u00a0]*,\d{2}|\d+', text or '')
        return money(found.group(0), ',') if found else None
    started, clock = datetime.now(timezone.utc).isoformat(), time.monotonic()
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != 'p']
    tolerance = int(config.get('total_tolerance', 1))
    def get(target):
        # Transient empty/5xx answers are retried once; 403/429 stop immediately (pause semantics).
        # 503/5xx/connection errors are usually short overloads at Bergzeit: back off 10 s, 30 s, 60 s.
        delays = config.get('retry_delays', [10, 30, 60])
        for attempt in range(len(delays) + 1):
            try: status, html = fetch(target)
            except OSError as exc: status, html = f'{type(exc).__name__}', ''
            if status in (403, 429): raise CollectionError(f'站点拒绝访问 HTTP {status}；任务已暂停', status)
            if status == 200 and 'elementsList: ' in html: return html
            if attempt < len(delays): time.sleep(delays[attempt])
        raise CollectionError(f'页面请求失败或缺少商品数据（HTTP {status}）；未提交快照', status)
    def crawl():
        products, logs, counts = {}, [], []
        for page in range(1, config.get('max_pages', 50) + 1):
            target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query + [('p', str(page))]), ''))
            html = get(target)
            try:
                counts.append(int(re.search(r'resultCount:\s*(\d+)', html).group(1)))
                start = html.index('elementsList: ') + len('elementsList: ')
                elements, _ = json.JSONDecoder().raw_decode(html, start)
            except Exception as exc:
                raise CollectionError('页面缺少商品数据（__initialAppState）；未提交快照') from exc
            items = [e['data'] for e in elements if e.get('type') == 'product']
            fresh = 0
            for d in items:
                pid = str(d.get('id') or '')
                if not pid: raise CollectionError('商品 ID 缺失；未提交快照')
                if pid in products: continue  # list shifted between pages: keep the first occurrence
                info = d.get('price') or {}
                images = d.get('images') or []
                products[pid] = {'product_id': pid, 'product_variant_id': d.get('itemId'), 'slug_id': None,
                    'name': (d.get('name') or '').strip(), 'brand': (d.get('brand') or {}).get('name'),
                    'sale_price': price(info.get('current')),
                    'original_price': price(info.get('old')) if info.get('isOldPrice') else None,
                    'currency': 'EUR', 'discount': f"-{info['savedPercentage']}%" if info.get('savedPercentage') else None,
                    'label': 'NEW' if d.get('isNew') else None,
                    'url': urljoin(base, (d.get('url') or '').split('#')[0]), 'listing_page': page, 'sku': d.get('itemId'),
                    'stock': 'UNKNOWN', 'image': images[0].get('src') if images else None}
                fresh += 1
            logs.append({'page': page, 'status': 200, 'initial_status': 200, 'items': fresh})
            if not items or len(products) >= counts[-1]: break
            time.sleep(config.get('page_delay', 1.5))
        return products, logs, counts
    problems = []
    for attempt in range(int(config.get('attempts', 3))):
        products, logs, counts = crawl()
        final = counts[-1] if counts else 0
        stable = max(counts) - min(counts) <= tolerance if counts else False
        if products and stable and abs(len(products) - final) <= tolerance: break
        problems.append(f'第 {attempt + 1} 次：采集 {len(products)} / 站点总数 {sorted(set(counts))}')
        time.sleep(max(5, config.get('page_delay', 1.5)))
    else:
        raise CollectionError('采集期间站点商品总数持续变化（' + '；'.join(problems) + '）；未提交快照，避免误报下架')
    products = list(products.values())
    return {'meta': {'tool': 'bergzeit_state', 'started': started, 'ended': datetime.now(timezone.utc).isoformat(),
        'duration_s': round(time.monotonic() - clock, 2), 'site_total': len(products), 'reported_total': final,
        'reported_totals': counts, 'attempts': len(problems) + 1, 'site_pages': len(logs),
        'extracted': len(products), 'pages': logs, 'complete': True}, 'products': products}


async def css_http(site, factory=None):
    """Server-rendered catalogue fetched over plain HTTP (no browser) + Crawl4AI CSS schema + next-page links."""
    import asyncio
    return await asyncio.to_thread(collect_css_http, site['url'], site['adapter_config'])


def collect_css_http(url, config, fetch=None):
    import json, time, urllib.request, urllib.robotparser
    from datetime import datetime, timezone
    from urllib.parse import urlsplit
    from crawl4ai import JsonCssExtractionStrategy
    from crawl4ai_run.run_crawl4ai import CollectionError, clean, next_page_link, access_control_page, auto_schema
    parts = urlsplit(url); base = f'{parts.scheme}://{parts.netloc}'
    ua = config.get('user_agent') or 'Mozilla/5.0 (compatible; product-monitor)'
    def default_fetch(target):
        request = urllib.request.Request(target, headers={'User-Agent': ua, 'Accept': 'text/html'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.geturl(), response.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as exc:
            return exc.code, target, ''
    fetch = fetch or default_fetch
    robots = None
    if fetch is default_fetch:
        robots = load_robots(base, ua)
    strategy = JsonCssExtractionStrategy(config['schema']) if config.get('schema') else None
    started, clock = datetime.now(timezone.utc).isoformat(), time.monotonic()
    products, seen, logs, visited, target = [], set(), [], set(), url
    for page in range(1, config.get('max_pages', 50) + 1):
        if robots and not robots.can_fetch(ua, target): raise CollectionError('站点 robots.txt 拒绝自动化访问；任务已暂停', 403)
        status, final, html = fetch(target); visited |= {target, final}
        if status in (403, 429): raise CollectionError(f'站点拒绝访问 HTTP {status}；监控已暂停', status)
        if status != 200: raise CollectionError(f'第 {page} 页请求失败（HTTP {status}）；未提交快照', status)
        if access_control_page(html): raise CollectionError('站点返回访问控制页面；监控已暂停', 403)
        if urlsplit(final).netloc != parts.netloc: raise CollectionError('最终重定向到其他网站')
        items = (strategy or JsonCssExtractionStrategy(auto_schema(html, final))).extract(final, html)
        if not items: raise CollectionError(f'第 {page} 页没有提取到商品；未提交快照')
        for item in items:
            product = clean(item, page, url, odoo=False, decimal_separator=config.get('decimal_separator', 'auto'))
            if product['product_id'] in seen: raise CollectionError('翻页中出现重复商品 ID，可能发生列表移动')
            if not product['name'] or product['sale_price'] is None: raise CollectionError('商品名称或价格缺失')
            seen.add(product['product_id']); products.append(product)
        logs.append({'page': page, 'status': 200, 'initial_status': 200, 'items': len(items)})
        target = next_page_link(html, final, config, visited)
        if not target: break
        time.sleep(config.get('page_delay', 1.5))
    else:
        raise CollectionError('分页超过 max_pages 上限；未提交快照')
    return {'meta': {'tool': 'css_http', 'started': started, 'ended': datetime.now(timezone.utc).isoformat(),
        'duration_s': round(time.monotonic() - clock, 2), 'site_total': len(products), 'site_pages': len(logs),
        'extracted': len(products), 'pages': logs, 'complete': True}, 'products': products}


async def flaresolverr_fetch(site, factory=None):
    """execution_mode=flaresolverr: same CSS schema/pagination/clean() as css_http and the extension, but every page
    is fetched through a FlareSolverr container (one browser session per run, destroyed in finally)."""
    import asyncio
    return await asyncio.to_thread(collect_flaresolverr, site['url'], site['adapter_config'])


def flaresolverr_challenge(html):
    import re
    from crawl4ai_run.run_crawl4ai import access_control_page
    low = (html or '').lower()
    title = re.search(r'<title[^>]*>(.*?)</title>', low, re.S)
    return (access_control_page(html) or 'challenges.cloudflare.com/turnstile' in low
            or bool(title and re.match(r'\s*(just a moment|un instant|attention required|checking your browser|verify you are human)', title.group(1))))


def collect_flaresolverr(url, config, post=None, sleep=None):
    import json, os, time, uuid, urllib.request
    from crawl4ai_run.run_crawl4ai import CollectionError
    endpoint = config.get('flaresolverr_url') or os.environ.get('FLARESOLVERR_URL') or 'http://flaresolverr:8191/v1'
    max_timeout = int(config.get('flaresolverr_timeout', 120000))
    backoff = list(config.get('flaresolverr_backoff', [15, 45]))[:2]  # 1-2 retries per page
    sleep = sleep or time.sleep
    def default_post(payload):
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode('utf-8'),
                                         headers={'Content-Type': 'application/json; charset=utf-8'})
        try:
            with urllib.request.urlopen(request, timeout=max_timeout / 1000 + 30) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:  # FlareSolverr answers errors with HTTP 500 + JSON body
            try: return json.loads(exc.read().decode('utf-8'))
            except Exception: return {'status': 'error', 'message': f'HTTP {exc.code}'}
    post = post or default_post
    def call(payload):
        try: return post(payload)
        except OSError as exc: raise CollectionError(f'FlareSolverr 服务不可用（{type(exc).__name__}）；未提交快照') from exc
    session = 'monitor-' + uuid.uuid4().hex[:12]
    created = call({'cmd': 'sessions.create', 'session': session})
    if created.get('status') != 'ok': raise CollectionError(f"FlareSolverr 会话创建失败：{str(created.get('message'))[:120]}；未提交快照")
    timing = []
    def fetch(target):
        problem = ''
        for attempt in range(len(backoff) + 1):
            clock = time.monotonic()
            answer = call({'cmd': 'request.get', 'url': target, 'session': session, 'maxTimeout': max_timeout})
            solution = answer.get('solution') or {}
            html = solution.get('response') or ''
            if answer.get('status') == 'ok' and solution.get('status') == 200 and html and not flaresolverr_challenge(html):
                timing.append(round(time.monotonic() - clock, 1))
                return 200, solution.get('url') or target, html
            if answer.get('status') == 'ok' and solution.get('status') == 429:
                return 429, solution.get('url') or target, ''
            problem = (str(answer.get('message') or '')[:120] if answer.get('status') != 'ok'
                       else f"HTTP {solution.get('status')}" + ('，仍是 Cloudflare 验证页面' if flaresolverr_challenge(html) else ''))
            if attempt < len(backoff): sleep(backoff[attempt])
        # Not a site pause: the solver failed this time; the next scheduled run tries again.
        raise CollectionError(f'FlareSolverr 未能通过验证（{problem}）；未提交快照')
    try:
        document = collect_css_http(url, config, fetch=fetch)
    finally:
        try: call({'cmd': 'sessions.destroy', 'session': session})
        except Exception: pass
    document['meta'].update(tool='flaresolverr', page_seconds=timing)
    return document


REGISTRY = {'css_http': css_http, 'odoo_lecomte': odoo_lecomte, 'css_catalog': css_catalog, 'shopify_json': shopify_json,
            'bergzeit_state': bergzeit_state}
