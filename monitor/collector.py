import asyncio
import re
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from .store import Incomplete, validate


def field(root, spec):
    if not spec:
        return None
    node = root.select_one(spec['selector']) if spec.get('selector') else root
    if node is None:
        return None
    value = node.get(spec['attribute']) if spec.get('attribute') else node.get_text(' ', strip=True)
    if value is None:
        return None
    if spec.get('regex'):
        match = re.search(spec['regex'], str(value))
        if not match:
            return None
        value = match.group(1)
    return str(value).strip() or None


def money(value, decimal_separator):
    if value is None:
        return None
    value = re.sub(r'[^\d,.\-]', '', value)
    if decimal_separator == ',':
        value = value.replace('.', '').replace(',', '.')
    else:
        value = value.replace(',', '')
    try:
        return str(Decimal(value))
    except Exception as exc:
        raise Incomplete('Unparseable price') from exc


def parse(html, url, config):
    soup = BeautifulSoup(html, 'html.parser')
    products = []
    for card in soup.select(config['card_selector']):
        p = {name: field(card, spec) for name, spec in config['fields'].items()}
        for key in ('id', 'sku', 'name', 'price', 'original_price', 'stock', 'url'):
            p.setdefault(key, None)
        p['url'] = urljoin(url, p['url']) if p['url'] else None
        if p['url'] and urlsplit(p['url']).netloc != urlsplit(config['url']).netloc:
            raise Incomplete('Product link left configured origin')
        p['price'] = money(p['price'], config.get('decimal_separator', '.'))
        p['original_price'] = money(p['original_price'], config.get('decimal_separator', '.'))
        products.append(p)
    total_text = field(soup, config['total'])
    if total_text is None or not total_text.isdigit():
        raise Incomplete('Missing authoritative product total')
    next_node = soup.select_one(config['next_selector']) if config.get('next_selector') else None
    href = next_node.get('href') if next_node else None
    next_url = urljoin(url, href) if href else None
    terminal = bool(soup.select_one(config['terminal_selector']))
    return products, int(total_text), next_url, terminal


async def collect_once(config):
    # No proxy, stealth, CAPTCHA handling, authentication bypass, or access-control evasion.
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(user_agent='CatalogMonitor/1.0 (authorized public catalog monitoring)')
            origin = urlsplit(config['url']).netloc
            url, visited, inventory, expected = config['url'], set(), {}, None
            for _ in range(config.get('max_pages', 100)):
                if url in visited or urlsplit(url).netloc != origin:
                    raise Incomplete('Pagination loop or cross-origin navigation')
                visited.add(url)
                response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                if response is None or response.status >= 400:
                    raise Incomplete(f'HTTP {response.status if response else "no response"}; stopped')
                if urlsplit(page.url).netloc != origin:
                    raise Incomplete('Redirect left configured origin')
                await page.wait_for_selector(config['ready_selector'], state='attached', timeout=15000)
                mode = config.get('dynamic', 'none')
                if mode != 'none':
                    for _ in range(config.get('max_dynamic_steps', 100)):
                        cards, count, _, terminal = parse(await page.content(), page.url, config)
                        if terminal and len(cards) == count:
                            break
                        before = await page.content()
                        if mode == 'load_more':
                            button = page.locator(config['load_more_selector'])
                            if not await button.count() or not await button.first.is_visible():
                                raise Incomplete('Load-more stopped without completion proof')
                            await button.first.click()
                        elif mode == 'scroll':
                            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                        else:
                            raise Incomplete('Unknown dynamic mode')
                        # Site-specific completion is still mandatory; stability alone never proves completeness.
                        for _ in range(20):
                            await asyncio.sleep(.25)
                            if await page.content() != before:
                                break
                    else:
                        raise Incomplete('Dynamic step limit reached')
                cards, total, next_url, terminal = parse(await page.content(), page.url, config)
                if expected is not None and total != expected:
                    raise Incomplete('Total changed during pagination')
                expected = total
                if not cards and total != 0:
                    raise Incomplete('Empty page in nonempty catalog')
                validate(cards, len(cards))
                for p in cards:
                    if p['id'] in inventory:
                        raise Incomplete('Duplicate identity across pages')
                    inventory[p['id']] = p
                if next_url:
                    url = next_url
                    continue
                if not terminal:
                    raise Incomplete('Missing terminal-page marker')
                products = list(inventory.values())
                validate(products, expected)
                return products, {'complete': True, 'expected': expected, 'pages': sorted(visited)}
            raise Incomplete('Page limit reached')
        finally:
            await browser.close()


async def collect(config):
    products, evidence = await collect_once(config)
    # Count alone cannot catch a catalog reordered while pages were fetched.
    repeated, second = await collect_once(config)
    if sorted(products, key=lambda p: p['id']) != sorted(repeated, key=lambda p: p['id']):
        raise Incomplete('Catalog changed between complete traversals')
    evidence['verified_passes'] = 2
    evidence['verification_pages'] = second['pages']
    return products, evidence
