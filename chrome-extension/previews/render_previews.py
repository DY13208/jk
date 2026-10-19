import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
ROOT = Path(r'D:\jk\chrome-extension')
STATES = {
 'idle':    {'status': '已连接后台，等待任务', 'state': 'idle', 'task': None, 'progress': None},
 'collecting': {'status': '正在完整采集：niponino_arcteryx · 第 4 页', 'state': 'running', 'task': 'niponino_arcteryx', 'progress': {'page': 4}},
 'success': {'status': '测试通过：184 个 / 11 页，未发送通知', 'state': 'success', 'task': 'niponino_arcteryx', 'progress': None},
 'failed':  {'status': '扩展尚未获得 https://www.niponino.fr 的访问授权：请打开扩展弹窗点击“授权已配置网站”', 'state': 'error', 'task': 'niponino_arcteryx', 'progress': None},
}
SITES = [{'id': 'niponino_arcteryx', 'name': "Niponino - Arc'teryx", 'url': 'https://www.niponino.fr/arcteryx'},
         {'id': 'lecomte', 'name': "Lecomte · Arc'teryx", 'url': 'https://www.lecomte-alpirando.be/shop'}]
MOCK = '''window.chrome = {storage:{local:{get:async()=>(%s)},onChanged:{addListener(){}}},
 runtime:{sendMessage:async m=>m.kind==='sites'?{items:%s}:{ok:true}},
 permissions:{contains:async()=>true,request:async()=>true}, tabs:{create(){}}};'''
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 380, 'height': 600}, device_scale_factor=2)
        for name, svg, size in [('16','logo-small.svg',16),('32','logo-small.svg',32),('48','logo.svg',48),('128','logo.svg',128)]:
            await page.set_viewport_size({'width': size, 'height': size})
            (ROOT/'icons'/'_tmp.html').write_text(f'<html><body style="margin:0;background:transparent"><img src="{svg}" width="{size}" height="{size}" style="display:block"></body></html>',encoding='utf-8'); await page.goto((ROOT/'icons'/'_tmp.html').as_uri())
            await page.wait_for_timeout(150)
            await page.screenshot(path=str(ROOT/'icons'/f'icon{name}.png'), omit_background=True, scale='css')
        await page.close()
        for name, state in STATES.items():
            ctx = await browser.new_context(viewport={'width': 380, 'height': 520}, device_scale_factor=2)
            await ctx.add_init_script(MOCK % (json.dumps({**state, 'updated': '2026-10-16T06:17:12Z'}), json.dumps(SITES)))
            pg = await ctx.new_page()
            await pg.goto((ROOT/'popup.html').as_uri()); await pg.wait_for_timeout(400)
            if name == 'collecting':
                await pg.evaluate("document.querySelector('#verify').classList.add('loading');document.querySelector('#verify .label').textContent='正在采集测试…';document.querySelectorAll('button,select').forEach(b=>{if(b.id!=='reason-toggle')b.disabled=true})")
            if name == 'failed': await pg.click('#reason-toggle'); await pg.wait_for_timeout(150)
            h = await pg.evaluate('Math.ceil(document.body.getBoundingClientRect().height)')
            await pg.set_viewport_size({'width': 380, 'height': h})
            await pg.screenshot(path=str(ROOT/'previews'/f'popup-{name}.png'), full_page=False)
            print(name, 'height', h)
            await ctx.close()
        await browser.close()
asyncio.run(main())
