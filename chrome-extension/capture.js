// Injected only into an app-created catalogue tab. No cookies or browser credentials are read.
async function captureCatalogue(settings) {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const started = Date.now();
  const checkpoints = [];
  const check = () => {
    if (location.origin !== settings.origin || !location.pathname.includes('/shop')) throw Error('采集标签页已离开配置的商品目录');
    if (/^(just a moment|access denied|attention required|403\b|forbidden)/i.test(document.title)) throw Error('ACCESS_DENIED:网站要求访问验证，请在浏览器中手动处理');
    if (Date.now() - started > 200000) throw Error('完整采集超时；没有提交快照');
  };
  const clean = node => {
    const clone = node.cloneNode(true);
    clone.querySelectorAll('script,iframe,button,textarea,select,img').forEach(e => e.remove());
    clone.querySelectorAll('input').forEach(e => { if (e.name !== 'product_id') e.remove(); });
    const keep = new Set(['class', 'data-product-template-id', 'name', 'value', 'href', 'title', 'itemprop', 'content', 'data-page-num', 'data-page-count', 'data-total-count']);
    for (const element of [clone, ...clone.querySelectorAll('*')]) {
      for (const attribute of [...element.attributes]) if (!keep.has(attribute.name)) element.removeAttribute(attribute.name);
    }
    return clone.outerHTML;
  };
  let previous = 0, expectedTotal, expectedPages;
  for (let page = 1; page <= 200; page++) {
    for (let attempt = 0; attempt < 80; attempt++) {
      check();
      const pager = document.querySelector('.tp-product-pager');
      const count = document.querySelectorAll('.tp-product-item').length;
      if (pager && count > previous) break;
      await sleep(250);
    }
    check();
    const pager = document.querySelector('.tp-product-pager');
    const cards = [...document.querySelectorAll('.tp-product-item')];
    const total = Number(pager?.getAttribute('data-total-count'));
    const pages = Number(pager?.getAttribute('data-page-count'));
    const current = Number(pager?.getAttribute('data-page-num') || page);
    if (!pager || !Number.isInteger(total) || total < 1 || !Number.isInteger(pages) || pages < 1 || pages > 200 || current !== page || cards.length <= previous) throw Error('分页没有增长、页码不符或总数信息缺失');
    if (page === 1) { expectedTotal = total; expectedPages = pages; }
    if (total !== expectedTotal || pages !== expectedPages) throw Error('采集中目录总数变化，请下轮重试');
    checkpoints.push({page, html: cards.map(clean).join('') + clean(pager)});
    if (page === pages) {
      if (cards.length !== total) throw Error('最终商品数与网站声明不一致');
      return {checkpoints};
    }
    const button = document.querySelector('.tp-load-more-btn');
    if (!button) throw Error('尚未加载完整但缺少加载更多按钮');
    const next = new URL(button.getAttribute('href'), location.href);
    if (next.origin !== settings.origin || next.search !== settings.search) throw Error('分页链接跳转到其他目录');
    previous = cards.length;
    await sleep(3000);
    check();
    button.click();
  }
  throw Error('超过分页上限');
}
// Generic sites: injected only into an app-created tab for a configured site. Returns a sanitized DOM of the
// current listing page plus the next-page URL. Form values, scripts and cookies are never exported.
async function capturePage(settings) {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const check = () => {
    if (location.origin !== settings.origin) throw Error('采集标签页离开了配置的网站');
    if (/^(just a moment|access denied|attention required|403\b|forbidden|un instant)/i.test(document.title.trim()))
      throw Error('ACCESS_DENIED:网站要求访问验证，请在浏览器中手动处理');
  };
  for (let i = 0; i < 40 && document.readyState !== 'complete'; i++) await sleep(250);
  // Cloudflare/bot challenge: give a real browser up to ~25 s to pass it on its own, then report it explicitly.
  const challenged = () => /^(just a moment|un instant|attention required|checking your browser|vérification)/i.test(document.title.trim())
    || !!document.querySelector('#challenge-form, #challenge-running, #cf-challenge-running, .cf-turnstile, iframe[src*="challenges.cloudflare.com"], script[src*="/cdn-cgi/challenge-platform/h/"]');
  for (let i = 0; i < 50 && challenged(); i++) await sleep(500);
  if (challenged()) throw Error('ACCESS_DENIED:网站显示 Cloudflare 人机验证页面（不是空列表），请在浏览器中打开该网站手动完成验证');
  check();
  // Wait for product cards (client-rendered / lazy grids) when the site's card selector is known.
  const cardCount = () => { try { return settings.baseSelector ? document.querySelectorAll(settings.baseSelector).length : -1; } catch { return -1; } };
  for (let i = 0; i < 60 && cardCount() === 0; i++) await sleep(250);
  // Popups (cookie consent, newsletter, promo): prefer reject/close, never submit forms or sign up.
  // Product data is read from the DOM either way; this only keeps next/load-more controls clickable.
  const dismissOverlays = async () => {
    const container = /cookie|consent|gdpr|rgpd|didomi|onetrust|axeptio|cmp|tarteaucitron|modal|popup|pop-up|newsletter|overlay|lightbox|dialog|promo/i;
    const rejectText = /^\s*(tout refuser|refuser|refuser tout|continuer sans accepter|reject all|reject|decline|deny|alle ablehnen|ablehnen|nur notwendige|only necessary|necessary only|essential only)\s*$/i;
    const closeText = /^\s*(fermer|close|schließen|cerrar|chiudi|non merci|no thanks|plus tard|later|×|✕|x)\s*$/i;
    const acceptText = /^\s*(ok|j'ai compris|compris|got it|accept|accepter|tout accepter|accept all|alle akzeptieren|continuer)\s*$/i;
    const inPopup = el => { for (let n = el; n && n !== document.body; n = n.parentElement) if (container.test((n.id || '') + ' ' + (n.className?.baseVal ?? n.className ?? '') + ' ' + (n.getAttribute('role') || '') + ' ' + (n.getAttribute('aria-modal') || ''))) return true; return false; };
    const clickable = el => visible(el) && !el.disabled && el.type !== 'submit' && !(el.tagName === 'A' && /^https?:/i.test(el.getAttribute('href') || '') && sameSite(el.getAttribute('href')) !== location.href.split('#')[0]);
    const label = el => (el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
    for (let round = 0; round < 3; round++) {
      const controls = [...document.querySelectorAll('button, [role=button], a, span[class*=close], div[class*=close]')].filter(el => clickable(el) && inPopup(el));
      const target = controls.find(el => rejectText.test(label(el))) || controls.find(el => closeText.test(label(el)) || /close|dismiss|fermer/i.test((el.getAttribute('aria-label') || '') + ' ' + (el.className || '')))
        || controls.find(el => acceptText.test(label(el)) && /cookie|consent|didomi|onetrust|axeptio|cmp|tarteaucitron/i.test(el.closest('[id],[class]')?.outerHTML.slice(0, 300) || ''));
      if (!target) break;
      try { target.click(); } catch {}
      await sleep(600);
    }
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', keyCode: 27, bubbles: true}));
    // Hide remaining fixed full-screen layers that are not the catalogue itself.
    for (const el of document.querySelectorAll('body *')) {
      const style = getComputedStyle(el);
      if (style.position !== 'fixed' || style.display === 'none') continue;
      const r = el.getBoundingClientRect();
      if (r.width * r.height < 0.5 * innerWidth * innerHeight) continue;
      if (el.querySelectorAll('a[href]').length > 20) continue;
      // Never hide anything that holds (or is) product cards.
      try { if (settings.baseSelector && (el.matches(settings.baseSelector) || el.querySelector(settings.baseSelector))) continue; } catch {}
      el.style.setProperty('display', 'none', 'important');
    }
    document.documentElement.style.removeProperty('overflow'); document.body.style.removeProperty('overflow');
  };
  // Trigger lazy-loaded cards/images.
  for (let y = 0; y < 8; y++) { window.scrollTo(0, document.body.scrollHeight * (y + 1) / 8); await sleep(250); }
  const nextText = /^\s*(next|suivant|page suivante|weiter|nächste|siguiente|successivo|show more|load more|voir plus|›|»|→|>)\s*$/i;
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const sameSite = href => { try { const u = new URL(href, location.href); return u.origin === location.origin ? u.href.split('#')[0] : null; } catch { return null; } };
  const findNext = () => {
    const visited = new Set(settings.visited || []);
    const ok = el => { const raw = el.getAttribute('href') || ''; if (!raw || raw.startsWith('#') || raw.startsWith('javascript:') || el.classList.contains('disabled')) return null;
      const href = sameSite(raw); return href && href !== location.href.split('#')[0] && !visited.has(href) ? href : null; };
    const selectors = 'link[rel=next], a[rel=next], .pagination .next a, a.next, li.next a, a.pagination__next, a[class*=next], a[aria-label*=next i], a[aria-label*=suivant i], a[title*=next i], a[title*=suivant i]';
    for (const el of document.querySelectorAll(selectors)) { const href = ok(el); if (href) return href; }
    for (const el of document.querySelectorAll('a[href]')) if (nextText.test(el.textContent || '')) { const href = ok(el); if (href) return href; }
    const m = location.href.match(/(?:[?&](?:page|p|pg)=|\/page\/)(\d+)/); const want = (m ? Number(m[1]) : 1) + 1;
    const re = new RegExp('(?:[?&](?:page|p|pg)=|/page/)' + want + '(?:\\D|$)');
    for (const el of document.querySelectorAll('a[href]')) if (re.test(el.getAttribute('href'))) { const href = ok(el); if (href && new URL(href).pathname === location.pathname) return href; }
    return null;
  };
  await dismissOverlays();
  let next = findNext();
  if (!next) {
    // In-page "load more" buttons (no URL): click until the page stops growing.
    for (let clicks = 0; clicks < (settings.maxClicks || 40); clicks++) {
      const button = [...document.querySelectorAll('button, a:not([href]), [role=button]')].find(el => visible(el) && !el.disabled && nextText.test(el.textContent || ''));
      if (!button) break;
      const before = document.querySelectorAll('a[href]').length;
      button.click();
      let grew = false;
      for (let i = 0; i < 60; i++) { await sleep(250); check(); if (document.querySelectorAll('a[href]').length > before) { grew = true; break; } }
      if (!grew) break;
      await sleep(1500);
    }
  }
  check();
  const keep = /^(class|id|href|src|srcset|alt|title|rel|itemprop|content|datetime|data-(?!.*(token|csrf|secret|key|session)).*)$/i;
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('script,style,iframe,svg,noscript,textarea,select,input,form > button,template,canvas,video').forEach(e => e.remove());
  for (const element of [clone, ...clone.querySelectorAll('*')]) {
    for (const attribute of [...element.attributes]) if (!keep.test(attribute.name)) element.removeAttribute(attribute.name);
  }
  const rels = [...document.querySelectorAll('link[rel=next]')].map(l => `<link rel="next" href="${l.href.replace(/"/g, '')}">`).join('');
  const html = '<html><head><title>' + document.title.replace(/</g, '') + '</title>' + rels + '</head>' + clone.outerHTML + '</html>';
  if (html.length > 3800000) throw Error('页面过大，无法安全回传');
  return {html, url: location.href.split('#')[0], next, cards: cardCount(), title: document.title.slice(0, 200)};
}
