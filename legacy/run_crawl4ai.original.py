"""Crawl4AI real crawl of Lecomte Arc'teryx listing. Uses only Crawl4AI public API
(AsyncWebCrawler + JsonCssExtractionStrategy). No stealth, no magic, robots.txt checked."""
import asyncio, json, csv, re, sys, time, datetime, os
from zoneinfo import ZoneInfo
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai import JsonCssExtractionStrategy

TZ = ZoneInfo("Asia/Shanghai")
BASE = "https://www.lecomte-alpirando.be"
START = BASE + "/shop?attribute_value=729-2672"
OUT = "/workspace/crawl-test/output"
MODE = sys.argv[1] if len(sys.argv) > 1 else "headful"   # headful | headless-default
RUN_TAG = sys.argv[2] if len(sys.argv) > 2 else "run"

schema = {
    "name": "products",
    "baseSelector": "div.tp-product-item",
    "baseFields": [{"name": "product_template_id", "type": "attribute", "attribute": "data-product-template-id"}],
    "fields": [
        {"name": "product_variant_id", "selector": "input[name=product_id]", "type": "attribute", "attribute": "value"},
        {"name": "name", "selector": "h6.tp-product-title a", "type": "attribute", "attribute": "title"},
        {"name": "url", "selector": "h6.tp-product-title a", "type": "attribute", "attribute": "href"},
        {"name": "sale_price", "selector": "span[itemprop=price]", "type": "text"},
        {"name": "currency", "selector": "span[itemprop=priceCurrency]", "type": "text"},
        {"name": "original_price", "selector": "del.compare-price .oe_currency_value", "type": "text"},
        {"name": "discount", "selector": ".tp-discount-percentage", "type": "text"},
        {"name": "brand", "selector": "[itemprop=brand]", "type": "attribute", "attribute": "content"},
        {"name": "label", "selector": ".tp-product-label span", "type": "text"},
    ],
}

def now(): return datetime.datetime.now(TZ)

def clean(p, page):
    url = p.get("url") or ""
    full = BASE + url if url.startswith("/") else url
    canon = full.split("?")[0].split("#")[0]
    slug_id = re.search(r"-(\d+)$", canon)
    f = lambda v: float(v.replace(",", "").strip()) if v and v.strip() else None
    return {
        "product_id": p.get("product_template_id"),
        "product_variant_id": p.get("product_variant_id"),
        "slug_id": slug_id.group(1) if slug_id else None,
        "name": (p.get("name") or "").strip(),
        "brand": p.get("brand"),
        "sale_price": f(p.get("sale_price")),
        "original_price": f(p.get("original_price")),
        "currency": (p.get("currency") or "").strip(),
        "discount": (p.get("discount") or "").strip() or None,
        "label": (p.get("label") or "").strip() or None,
        "url": canon,
        "listing_page": page,
    }

async def main():
    t0 = time.time(); started = now()
    bcfg = BrowserConfig(headless=(MODE != "headful"), verbose=False,
                         **({"chrome_channel": "chrome", "channel": "chrome"} if MODE == "headful" else {}))
    def rcfg():
        return CrawlerRunConfig(cache_mode=CacheMode.BYPASS, check_robots_txt=True,
                                extraction_strategy=JsonCssExtractionStrategy(schema),
                                wait_until="domcontentloaded", page_timeout=60000,
                                delay_before_return_html=1.5, session_id="lecomte")
    log = {"tool": "crawl4ai", "mode": MODE, "run_tag": RUN_TAG, "started": started.isoformat(), "pages": []}
    products, seen = [], set()
    async with AsyncWebCrawler(config=bcfg) as crawler:
        url, page_no, pages_total, total_shown = START, 1, None, None
        while url:
            ts = time.time()
            r = await crawler.arun(url=url, config=rcfg())
            entry = {"page": page_no, "url": url, "status": r.status_code, "success": r.success,
                     "error": r.error_message, "secs": round(time.time() - ts, 2),
                     "headers": {k: v for k, v in (r.response_headers or {}).items() if k.lower() in ("server", "cf-ray", "cf-mitigated", "content-type", "retry-after")}}
            if not r.success or (r.status_code and r.status_code >= 400):
                entry["body_head"] = (r.html or "")[:300]
                log["pages"].append(entry); print(json.dumps(entry)); break
            items = json.loads(r.extracted_content or "[]")
            html = r.html or ""
            if page_no == 1:
                m = re.search(r"Of\s+(\d+)\s+Results", html); total_shown = int(m.group(1)) if m else None
                pg = [int(x) for x in re.findall(r'href="[^"]*/shop/page/(\d+)\?attribute_value=729-2672"', html)]
                pages_total = max(pg) if pg else 1
            new = 0
            for it in items:
                c = clean(it, page_no)
                if c["product_id"] and c["product_id"] not in seen:
                    seen.add(c["product_id"]); products.append(c); new += 1
            entry.update(items=len(items), new_unique=new); log["pages"].append(entry); print(json.dumps(entry))
            page_no += 1
            url = f"{BASE}/en/shop/page/{page_no}?attribute_value=729-2672" if page_no <= (pages_total or 1) else None
            if url: await asyncio.sleep(1.5)
    ended = now()
    log.update(ended=ended.isoformat(), duration_s=round(time.time() - t0, 2), site_total=total_shown,
               site_pages=pages_total, extracted=len(products))
    print(json.dumps({k: v for k, v in log.items() if k != "pages"}, ensure_ascii=False))
    tag = f"crawl4ai_{RUN_TAG}"
    os.makedirs(f"{OUT}/runs", exist_ok=True)
    for path in ([f"{OUT}/crawl4ai_products"] if RUN_TAG.startswith("run") else []) + [f"{OUT}/runs/{tag}"]:
        json.dump({"meta": {k: v for k, v in log.items()}, "products": products}, open(path + ".json", "w"), ensure_ascii=False, indent=1)
        if products:
            with open(path + ".csv", "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(products[0].keys())); w.writeheader(); w.writerows(products)

asyncio.run(main())
