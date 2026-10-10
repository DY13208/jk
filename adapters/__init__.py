"""Register independent adapters here; scheduling and storage stay site-agnostic."""
from crawl4ai_run.run_crawl4ai import collect, schema


async def odoo_lecomte(site):
    return await collect({**site['adapter_config'], 'url': site['url'], 'odoo': True, 'schema': schema})


async def css_catalog(site):
    if not site['adapter_config'].get('schema'):
        raise ValueError('CSS 适配器必须配置 Crawl4AI schema 和分页参数')
    return await collect({**site['adapter_config'], 'url': site['url'], 'odoo': False})


REGISTRY = {'odoo_lecomte': odoo_lecomte, 'css_catalog': css_catalog}
