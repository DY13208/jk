const eventDefaults={site_id:'',kind:'',q:'',from:'',to:'',currency:'',sort:'time_desc'};
let eventFilters={...eventDefaults};
try{const saved=JSON.parse(sessionStorage.getItem('event-filters')||'{}');for(const key of Object.keys(eventDefaults))if(typeof saved[key]==='string')eventFilters[key]=saved[key];}catch{}
const eventSorts=[['time_desc','时间：最新在前'],['time_asc','时间：最早在前'],['site_asc','网站：升序'],['site_desc','网站：降序'],['name_asc','商品名称：升序'],['name_desc','商品名称：降序'],['price_asc','价格：从低到高'],['price_desc','价格：从高到低'],['change_asc','涨跌：从低到高'],['change_desc','涨跌：从高到低']];
let eventAdvanced=false;
// Small Tabler-style line icons used only on this page (MIT, same stroke style as icons.js).
const EV_SVG=p=>`<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="ui-icon" aria-hidden="true">${p}</svg>`;
const EV_ICONS={
 chevron:EV_SVG('<path d="m6 9 6 6 6-6"/>'),
 all:EV_SVG('<path d="M12 4l-8 4l8 4l8 -4l-8 -4"/><path d="M4 12l8 4l8 -4"/><path d="M4 16l8 4l8 -4"/>'),
 NEW:EV_SVG('<path d="M12 3l8 4.5l0 9l-8 4.5l-8 -4.5l0 -9l8 -4.5"/><path d="M12 12l8 -4.5"/><path d="M12 12l0 9"/><path d="M12 12l-8 -4.5"/>'),
 REMOVED:EV_SVG('<path d="M12 3l8 4.5v9l-8 4.5l-8 -4.5v-9z"/><path d="M10 10l4 4m0 -4l-4 4"/>'),
 RETURNED:EV_SVG('<path d="M20 11a8.1 8.1 0 0 0 -15.5 -2m-.5 -4v4h4"/><path d="M4 13a8.1 8.1 0 0 0 15.5 2m.5 4v-4h-4"/>'),
 PRICE_DROP:EV_SVG('<path d="M12 5l0 14"/><path d="M18 13l-6 6"/><path d="M6 13l6 6"/>'),
 PRICE_RISE:EV_SVG('<path d="M12 5l0 14"/><path d="M18 11l-6 -6"/><path d="M6 11l6 -6"/>'),
 ACCESS_DENIED:EV_SVG('<path d="M3 12h4l3 8l4 -16l3 8h4"/>'),
 clock:EV_SVG('<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M12 7v5l3 3"/>'),
 calendar:EV_SVG('<path d="M4 7a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2z"/><path d="M16 3v4"/><path d="M8 3v4"/><path d="M4 11h16"/>'),
 filter:EV_SVG('<path d="M4 4h16v2.172a2 2 0 0 1 -.586 1.414l-4.414 4.414v7l-6 2v-8.5l-4.48 -4.928a2 2 0 0 1 -.52 -1.345v-2.227z"/>'),
 file:EV_SVG('<path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21h-10a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2h7l5 5v11a2 2 0 0 1 -2 2z"/><path d="M9 13h6"/><path d="M9 17h6"/>'),
 dots:EV_SVG('<path d="M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/><path d="M12 19m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/><path d="M12 5m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/>'),
 copy:EV_SVG('<path d="M7 9.667a2.667 2.667 0 0 1 2.667 -2.667h8.666a2.667 2.667 0 0 1 2.667 2.667v8.666a2.667 2.667 0 0 1 -2.667 2.667h-8.666a2.667 2.667 0 0 1 -2.667 -2.667z"/><path d="M4.012 16.737a2.005 2.005 0 0 1 -1.012 -1.737v-10c0 -1.1 .9 -2 2 -2h10c.75 0 1.158 .385 1.5 1"/>')};
const evIcon=k=>EV_ICONS[k]||UI_ICONS[k]||'';
function dateBoundary(value,next=false){const date=new Date(value+'T00:00:00');if(next)date.setDate(date.getDate()+1);return date.toISOString()}
function eventHeading(key,label){const selected=eventFilters.sort.startsWith(key+'_');return `<button type="button" class="event-sort-heading" data-event-sort="${key}" aria-label="按${label}排序">${label}<span aria-hidden="true">${selected?(eventFilters.sort.endsWith('_asc')?'↑':'↓'):'↕'}</span></button>`}
function eventMoney(value,currency){return value==null||value===''?'—':`${esc(value)} ${esc(currency||'')}`}
function eventPrice(row){const product=row.payload.product||{},current=eventMoney(product.sale_price,product.currency);if(['PRICE_DROP','PRICE_RISE'].includes(row.kind))return `<span class="ev-old">${eventMoney(row.payload.old_price,product.currency)} →</span><div class="ev-new">${current}</div>`;return `<span class="ev-new">${current}</span>`}
function eventTime(v){if(!v)return '—';const d=new Date(v),p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}/${p(d.getMonth()+1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`}
function eventSite(row){const name=row.site_name||row.site_id||'—';const initial=(name.trim()[0]||'?').toUpperCase();let h=0;for(const c of String(row.site_id||name))h=(h*31+c.charCodeAt(0))%360;return `<span class="ev-site" title="${esc(name)}"><span class="ev-avatar" style="--h:${h}" aria-hidden="true">${esc(initial)}</span><span class="ev-ellipsis">${esc(name)}</span></span>`}
function eventThumb(product){const src=product.image;if(typeof src==='string'&&/^https:\/\//i.test(src))return `<span class="ev-thumb">${evIcon('package')}<img src="${esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"></span>`;return `<span class="ev-thumb ev-thumb-empty" aria-hidden="true">${evIcon('package')}</span>`}
async function eventsPage(){
 const params=new URLSearchParams({sort:eventFilters.sort,offset:String(page*100)});
 for(const key of ['site_id','kind','q','currency'])if(eventFilters[key])params.set(key,eventFilters[key]);
 if(eventFilters.from)params.set('start',dateBoundary(eventFilters.from));if(eventFilters.to)params.set('end',dateBoundary(eventFilters.to,true));
 const data=await api('events?'+params);
 const options=(items,value)=>items.map(([key,label])=>`<option value="${esc(key)}" ${key===value?'selected':''}>${esc(label)}</option>`).join('');
 const kinds=[['','全部变化'],['NEW','新品上架'],['REMOVED','商品下架'],['RETURNED','重新上架'],['PRICE_DROP','价格下降'],['PRICE_RISE','价格上涨'],['ACCESS_DENIED','访问异常']];
 const total=Object.values(data.kind_counts).reduce((a,b)=>a+b,0);
 const tabs=`<div class="event-types ev-stats" role="group" aria-label="变化类型">${kinds.map(([key,label])=>{const on=key===eventFilters.kind;return `<button type="button" data-event-kind="${key}" aria-pressed="${on}" class="ev-stat ev-k-${key||'all'} ${on?'selected':''}" title="${label}"><span class="ev-stat-icon">${evIcon(key||'all')}</span><span class="ev-stat-text"><small>${label}</small><strong>${key?(data.kind_counts[key]||0):total}</strong></span>${on?`<span class="ev-stat-chev">${evIcon('chevron-right')}</span>`:''}</button>`}).join('')}</div>`;
 const advancedOn=eventFilters.from||eventFilters.to||eventFilters.currency;
 const filters=`<form id="event-filters" class="event-filters"><div class="event-filter-main"><label>网站<span class="ev-field">${evIcon('world')}<select name="site_id" aria-label="筛选网站">${options([['','全部网站'],...sites.map(s=>[s.id,s.name])],eventFilters.site_id)}</select></span></label><label class="event-keyword">搜索记录<span class="ev-field">${evIcon('search')}<input name="q" value="${esc(eventFilters.q)}" maxlength="200" placeholder="商品名称、ID 或异常信息"></span></label><label>排序<span class="ev-field">${evIcon('clock')}<select name="sort" aria-label="排序方式">${options(eventSorts,eventFilters.sort)}</select></span></label><div class="actions"><button class="primary ev-submit" type="submit">${evIcon('filter')}筛选</button><button id="event-reset" type="button">${evIcon('refresh')}重置</button></div></div><details class="event-advanced" ${eventAdvanced||advancedOn?'open':''}><summary>${evIcon('calendar')}日期与币种${advancedOn?' · 已启用':''}<span class="ev-chevron">${evIcon('chevron')}</span></summary><div class="event-filter-more"><label>开始日期<input name="from" type="date" value="${esc(eventFilters.from)}"></label><label>结束日期<input name="to" type="date" value="${esc(eventFilters.to)}"></label><label>币种<select name="currency">${options([['','全部币种'],...data.currencies.map(c=>[c,c])],eventFilters.currency)}</select></label><span class="event-filter-hint">日期按本地时区，结束日期包含当天。不同币种不做汇率换算。</span></div></details></form>`;
 const rows=data.items.map(row=>{const product=row.payload.product||{};const name=product.name||row.payload.message||'—';const url=product.url;const safe=typeof url==='string'&&/^https?:\/\//i.test(url);
  const title=safe?`<a class="product-name ev-ellipsis" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="${esc(name)}">${esc(name)}<span class="ev-ext">${evIcon('external-link')}</span></a>`:`<span class="ev-ellipsis ev-plain" title="${esc(name)}">${esc(name)}</span>`;
  const info=`<div class="ev-product">${row.payload.product?eventThumb(product):''}<div class="ev-product-text">${title}${row.product_id?`<div class="small ev-ellipsis" title="ID ${esc(row.product_id)}">ID ${esc(row.product_id)}</div>`:''}</div></div>`;
  const delta=row.price_change;
  const menuItems=[safe?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${evIcon('external-link')}打开商品链接</a>`:'',row.product_id?`<button type="button" data-copy-id="${esc(row.product_id)}">${evIcon('copy')}复制商品 ID</button>`:''].join('');
  const menu=menuItems?`<details class="ev-menu"><summary aria-label="更多操作" title="更多操作">${evIcon('dots')}</summary><div class="ev-menu-list">${menuItems}</div></details>`:'';
  return [`<span class="ev-time">${eventTime(row.at)}</span>`,eventSite(row),`<span class="ev-tag ev-tag-${esc(row.kind)}">${esc(labels[row.kind]||row.kind)}</span>`,info,eventPrice(row),delta==null?'<span class="ev-none">—</span>':`<span class="${delta<0?'event-drop':'event-rise'}">${delta>0?'+':'−'}${esc(Math.abs(Number(delta.toFixed(2))))} ${esc(product.currency||'')}</span>`,menu]});
 const body=rows.length?table([eventHeading('time','时间'),eventHeading('site','网站'),'变化类型',eventHeading('name','商品 / 信息'),eventHeading('price','价格'),eventHeading('change','涨跌金额'),'<span class="visually-hidden">操作</span>'],rows):'<div class="empty"><strong>没有符合条件的变化</strong><span>可以切换分类、放宽日期范围或点击“重置”。</span></div>';
 return `<div class="events-workspace events-page">${tabs}${filters}${panel(`<span class="ev-panel-title">${evIcon('file')}筛选结果</span>`,body+pager(data.total),`<span class="event-result-count" role="status">共 ${data.total} 条记录</span>`)}</div>`;
}
function bindEvents(){
 const form=$('#event-filters');
 function apply(overrides={}){if(!form.reportValidity())return;const values=Object.fromEntries(new FormData(form));const from=form.elements.from,to=form.elements.to;to.setCustomValidity('');if(from.value&&to.value&&from.value>to.value){to.setCustomValidity('结束日期不能早于开始日期');to.reportValidity();return}eventAdvanced=form.querySelector('details').open;eventFilters={...eventFilters,...values,...overrides};try{sessionStorage.setItem('event-filters',JSON.stringify(eventFilters))}catch{}page=0;render()}
 form.onsubmit=e=>{e.preventDefault();apply()};form.elements.to.oninput=()=>form.elements.to.setCustomValidity('');form.elements.from.oninput=()=>form.elements.to.setCustomValidity('');
 for(const key of ['site_id','sort','currency'])form.elements[key].onchange=()=>apply();
 form.querySelector('details').ontoggle=e=>{eventAdvanced=e.target.open};
 $('#event-reset').onclick=()=>{eventFilters={...eventDefaults};eventAdvanced=false;try{sessionStorage.removeItem('event-filters')}catch{}page=0;render()};
 document.querySelectorAll('[data-event-kind]').forEach(button=>button.onclick=()=>apply({kind:button.dataset.eventKind}));
 document.querySelectorAll('[data-event-sort]').forEach(button=>{const key=button.dataset.eventSort,selected=eventFilters.sort.startsWith(key+'_');if(selected)button.closest('th').setAttribute('aria-sort',eventFilters.sort.endsWith('_asc')?'ascending':'descending');button.onclick=()=>apply({sort:key+(selected&&eventFilters.sort.endsWith('_asc')?'_desc':'_asc')})});
 document.querySelectorAll('[data-copy-id]').forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText(b.dataset.copyId);b.lastChild.textContent='已复制'}catch{b.lastChild.textContent='复制失败'}setTimeout(()=>b.closest('details')?.removeAttribute('open'),700)});
 document.querySelectorAll('.ev-menu').forEach(d=>d.ontoggle=()=>{if(d.open)document.querySelectorAll('.ev-menu[open]').forEach(o=>{if(o!==d)o.removeAttribute('open')})});
}
document.addEventListener('click',e=>{if(!e.target.closest('.ev-menu'))document.querySelectorAll('.ev-menu[open]').forEach(o=>o.removeAttribute('open'))});
