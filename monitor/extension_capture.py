"""Parse public catalogue DOM with the existing Crawl4AI schema and Odoo ID rules."""
import re
from datetime import datetime,timezone
from urllib.parse import urlsplit
from crawl4ai_run.run_crawl4ai import schema,clean,pagination,access_control_page,CollectionError,auto_schema


def normalize(site,capture):
    from crawl4ai import JsonCssExtractionStrategy
    if site['adapter']!='odoo_lecomte': return normalize_pages(site,capture)
    checkpoints=capture.get('checkpoints',[]) if isinstance(capture,dict) else []
    if not 1<=len(checkpoints)<=200: raise CollectionError('扩展缺少完整分页检查点')
    options=site['adapter_config']
    extraction=JsonCssExtractionStrategy(schema if site['adapter']=='odoo_lecomte' else options['schema'])
    previous={};logs=[];total=pages=None
    for index,checkpoint in enumerate(checkpoints,1):
        html=checkpoint.get('html','')
        if not isinstance(html,str) or len(html)>2_000_000: raise CollectionError('扩展检查点格式或大小无效')
        if access_control_page(html): raise CollectionError('扩展遇到访问验证页面；监控已暂停',403)
        count,page_count,_=pagination(html,site['url'],{**options,'pagination_mode':'load_more'})
        if index==1: total,pages=count,page_count
        elif (count,page_count)!=(total,pages): raise CollectionError('扩展采集期间目录总数或分页变化')
        if checkpoint.get('page')!=index: raise CollectionError('扩展分页顺序或页码不完整')
        products={}
        for raw in extraction.extract(site['url'],html):
            product=clean(raw,index,site['url'],odoo=site['adapter']=='odoo_lecomte',
                          decimal_separator=options.get('decimal_separator','auto'))
            pid=product['product_id']
            if pid in products: raise CollectionError('扩展页面存在重复商品 ID')
            if pid in previous:
                old=previous[pid]
                if any(old[k]!=product[k] for k in ('name','sale_price','original_price','url')):
                    raise CollectionError('扩展采集期间商品发生变化')
                product['listing_page']=old['listing_page']
            products[pid]=product
        if not set(previous).issubset(products) or not len(products)>len(previous):
            raise CollectionError('扩展加载没有增长或丢失已有商品')
        logs.append({'page':index,'status':200,'items':len(products)-len(previous)})
        previous=products
    if len(checkpoints)!=pages or len(previous)!=total: raise CollectionError('扩展未加载全部分页或全部商品')
    stamp=datetime.now(timezone.utc).isoformat()
    return {'meta':{'tool':'crawl4ai-extension','started':stamp,'ended':stamp,'complete':True,
                    'site_total':total,'site_pages':pages,'extracted':len(previous),'pages':logs},
            'products':list(previous.values())}


def normalize_pages(site,capture):
    """Generic sites: one sanitized page DOM per listing page (next-link / ?page=N / load-more handled by the
    extension). Uses the site's schema, or a heuristic schema derived from page 1."""
    from crawl4ai import JsonCssExtractionStrategy
    checkpoints=capture.get('checkpoints',[]) if isinstance(capture,dict) else []
    if not 1<=len(checkpoints)<=200: raise CollectionError('扩展缺少完整分页快照')
    options=site['adapter_config'];host=urlsplit(site['url']).netloc
    extraction=None;products={};logs=[]
    for index,checkpoint in enumerate(checkpoints,1):
        html=checkpoint.get('html','');page_url=str(checkpoint.get('url') or site['url'])
        if not isinstance(html,str) or len(html)>4_000_000: raise CollectionError('扩展结果格式或大小无效')
        if checkpoint.get('page')!=index: raise CollectionError('扩展分页顺序或页码不连续')
        if urlsplit(page_url).netloc!=host: raise CollectionError('扩展分页离开了配置的网站')
        if access_control_page(html): raise CollectionError('扩展遇到访问验证页面；任务已暂停',403)
        title=str(checkpoint.get('title') or '')
        if re.search(r'^(just a moment|un instant|attention required|checking your browser|verify you are human|vérification)',title.strip(),re.I):
            raise CollectionError(f'扩展第 {index} 页是 Cloudflare 人机验证页面（标题：{title[:60]}）；请在 Chrome 中打开该网站手动完成验证',403)
        # Auto schema is derived per page so sale-only fields (old price) are found wherever they appear.
        extraction=JsonCssExtractionStrategy(options.get('schema') or auto_schema(html,page_url))
        items=extraction.extract(page_url,html)
        if not items:
            cards=checkpoint.get('cards')
            detail=f'页面已加载（标题：{title[:60]}），商品卡片选择器匹配 {cards} 个' if cards is not None else '页面已加载但未识别到商品卡片'
            raise CollectionError(f'扩展第 {index} 页没有提取到商品：{detail}；可能是网站改版或页面未渲染完成')
        for raw in items:
            product=clean(raw,index,site['url'],odoo=False,decimal_separator=options.get('decimal_separator','auto'))
            if product['product_id'] in products: raise CollectionError('扩展翻页中出现重复商品 ID')
            if not product['name'] or product['sale_price'] is None: raise CollectionError('商品名称或价格缺失')
            products[product['product_id']]=product
        logs.append({'page':index,'status':200,'items':len(items)})
    stamp=datetime.now(timezone.utc).isoformat()
    return {'meta':{'tool':'crawl4ai-extension','started':stamp,'ended':stamp,'complete':True,
                    'site_total':len(products),'site_pages':len(logs),'extracted':len(products),'pages':logs},
            'products':list(products.values())}
