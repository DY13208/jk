"""Register independent adapters here; scheduling and storage stay site-agnostic."""
from crawl4ai_run.run_crawl4ai import collect, schema


async def odoo_lecomte(site, factory=None):
    return await collect({**site['adapter_config'], 'url': site['url'], 'odoo': True, 'schema': schema},factory=factory)


async def css_catalog(site, factory=None):
    if not site['adapter_config'].get('schema'):
        raise ValueError('CSS 适配器必须配置 Crawl4AI schema 和分页参数')
    return await collect({**site['adapter_config'], 'url': site['url'], 'odoo': False},factory=factory)


REGISTRY = {'odoo_lecomte': odoo_lecomte, 'css_catalog': css_catalog}
