"""Parse public catalogue DOM with the existing Crawl4AI schema and Odoo ID rules."""
from datetime import datetime,timezone
from crawl4ai_run.run_crawl4ai import schema,clean,pagination,access_control_page,CollectionError


def normalize(site,capture):
    from crawl4ai import JsonCssExtractionStrategy
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
