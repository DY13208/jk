import sys,json
from playwright.sync_api import sync_playwright
route=sys.argv[1]; widths=[int(w) for w in sys.argv[2].split(',')]; out=sys.argv[3]; measure=len(sys.argv)>4
M="""()=>{const r=e=>e.getBoundingClientRect();const q=s=>[...document.querySelectorAll(s)].map(r);
const L=q('.dq-label'),S=q('.dq-field select'),I=q('.dq-icon'),B=q('.dq-actions button');
return {labelTops:L.map(x=>x.top),selTops:S.map(x=>x.top),selBottoms:S.map(x=>x.bottom),selH:S.map(x=>x.height),
icons:I.map(x=>[x.width,x.height,x.top]),btnBottoms:B.map(x=>x.bottom),btnH:B.map(x=>x.height),
overflow:document.documentElement.scrollWidth>innerWidth}}"""
with sync_playwright() as p:
    try: b=p.chromium.launch()
    except Exception: b=p.chromium.launch(channel='msedge')
    for w in widths:
        pg=b.new_page(viewport={'width':w,'height':900}); errs=[]
        pg.on('console',lambda m:errs.append(m.text) if m.type=='error' else None); pg.on('pageerror',lambda e:errs.append(str(e)))
        pg.goto('http://127.0.0.1:8080/#'+route); pg.wait_for_timeout(2500)
        pg.screenshot(path=out.replace('W',str(w)),full_page=False)
        print(w,'errors',errs, json.dumps(pg.evaluate(M)) if measure else '')
        pg.close()
    b.close()
