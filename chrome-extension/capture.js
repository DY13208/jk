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
