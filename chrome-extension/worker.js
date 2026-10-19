importScripts('capture.js');
let busy = false;
async function config() {
  const response = await fetch(chrome.runtime.getURL('config.local.json'));
  if (!response.ok) throw Error('缺少本机配置，请运行 scripts/prepare_extension.py');
  const value = await response.json();
  if (value.server !== 'http://127.0.0.1:8080' || !value.token) throw Error('扩展配置无效');
  return value;
}
async function api(path, body = {}) {
  const cfg = await config();
  const response = await fetch(cfg.server + '/api/collector/extension/worker/' + path, {
    method: 'POST', redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(15000),
    headers: {'Content-Type': 'application/json', 'x-collector-token': cfg.token}, body: JSON.stringify(body)
  });
  if (!response.ok) { const e = Error('后台请求失败 HTTP ' + response.status); e.status = response.status; throw e; }
  return response.json();
}
// state: idle | running | success | error (UI only); task: site id; progress: real counters only (e.g. {page}).
async function message(text, state = 'idle', task = null, progress = null) {
  await chrome.storage.local.set({status: text, state, task, progress, updated: new Date().toISOString()});
}
async function closeOwnedTab(id) {
  try { await chrome.tabs.get(id); }
  catch (e) { return /no tab with id|invalid tab id/i.test(e.message) ? 'missing' : 'retry'; }
  try {
    // IDs originate only from tabs.create or the ownership registry, never a URL sweep.
    // Redirects and unavailable URL metadata do not change task ownership.
    await chrome.tabs.remove(id);
    return 'closed';
  } catch { return 'retry'; }
}
async function ownedTabs() {
  const saved = await chrome.storage.session.get(['ownedTabs','tab']);
  const records = saved.ownedTabs || {};
  // Migrate the old single-tab record without losing a tab that still needs cleanup.
  if (saved.tab?.id) {
    records[saved.tab.id] = saved.tab;
    await chrome.storage.session.set({ownedTabs:records});
    await chrome.storage.session.remove('tab');
  }
  return records;
}
async function cleanTrackedTab(id, target) {
  const outcome = await closeOwnedTab(id,target);
  const records = await ownedTabs();
  if (outcome === 'retry') {
    records[id] = {...records[id],id,url:target};
    await chrome.alarms.create('tab-cleanup:'+id,{when:Date.now()+30000});
  } else {
    delete records[id];
    await chrome.alarms.clear('tab-cleanup:'+id);
  }
  await chrome.storage.session.set({ownedTabs:records});
  return outcome;
}
async function createOwnedTab(site) {
  const tab=await chrome.tabs.create({url:site.url,active:false});
  try {
    const records=await ownedTabs();
    records[tab.id]={id:tab.id,url:site.url,expires:Date.now()+240000};
    await chrome.storage.session.set({ownedTabs:records});
    await chrome.alarms.create('tab-cleanup:'+tab.id,{when:records[tab.id].expires});
    return tab;
  } catch(error) { await closeOwnedTab(tab.id); throw error; }
}
async function cleanupExpiredTabs() {
  // Must run even while poll() is busy waiting on a stuck executeScript call.
  const records=await ownedTabs();
  for(const record of Object.values(records))
    if(record.expires && Date.now()>=record.expires)await cleanTrackedTab(record.id,record.url);
}
async function executeCapture(options) {
  let timer;
  try {
    return await Promise.race([chrome.scripting.executeScript(options),new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(Error('采集页面执行超时；没有提交快照')),210000);
    })]);
  } finally { clearTimeout(timer); }
}
async function cleanupIdleTabs() {
  const {active} = await chrome.storage.session.get('active');
  if (active && Date.now() - active.started < 250000) return {busy:true,closed:0};
  const records = await ownedTabs();
  let closed = 0;
  for (const record of Object.values(records)) if (await cleanTrackedTab(record.id,record.url) === 'closed') closed++;
  return {closed};
}
function siteOrigin(site) {
  const url = new URL(site.url);
  if (!['https:','http:'].includes(url.protocol) || url.username || url.password) throw Error('站点 URL 无效');
  return url.origin;
}
async function waitComplete(tabId) {
  for (let i = 0; i < 120; i++) {
    const current = await chrome.tabs.get(tabId);
    if (current.status === 'complete') return;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
}
// Any site configured in the backend (server only dispatches configured sites). Host access is granted per
// configured origin from the popup ("授权已配置网站"); nothing outside those origins is ever opened.
async function collectGeneric(site) {
  const origin = siteOrigin(site);
  if (!await chrome.permissions.contains({origins: [origin + '/*']}))
    throw Error('扩展尚未获得 ' + origin + ' 的访问授权：请打开扩展弹窗点击“授权已配置网站”');
  const cfg = site.adapter_config || {};
  const maxPages = Math.min(Number(cfg.max_pages) || 50, 200);
  const delay = Math.max(1, Math.min(Number(cfg.page_delay) || 3, 30)) * 1000;
  const tab = await createOwnedTab(site);
  try {
    const checkpoints = [], visited = [site.url];
    for (let page = 1; page <= maxPages; page++) {
      await waitComplete(tab.id);
      await message('正在完整采集：' + site.id + ' · 第 ' + page + ' 页', 'running', site.id, {page});
      const baseSelector = cfg.schema?.baseSelector || null;
      let results = await executeCapture({target: {tabId: tab.id}, func: capturePage,
        args: [{origin, visited, baseSelector}]});
      let result = results[0]?.result;
      if (result && result.cards === 0) {
        // Card selector known but nothing rendered: reload once (slow render / interrupted load) before giving up.
        await chrome.tabs.reload(tab.id);
        await new Promise(resolve => setTimeout(resolve, 1500));
        await waitComplete(tab.id);
        results = await executeCapture({target: {tabId: tab.id}, func: capturePage,
          args: [{origin, visited, baseSelector}]});
        result = results[0]?.result;
      }
      if (!result?.html) throw Error('采集没有返回页面内容');
      checkpoints.push({page, url: result.url, html: result.html, cards: result.cards, title: result.title});
      visited.push(result.url);
      if (!result.next || visited.includes(result.next)) return {checkpoints};
      if (new URL(result.next).origin !== origin) throw Error('分页跳转到其他网站');
      visited.push(result.next);
      await new Promise(resolve => setTimeout(resolve, delay));
      await chrome.tabs.update(tab.id, {url: result.next});
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw Error('超过最大分页数 ' + maxPages);
  } finally {
    await cleanTrackedTab(tab.id,site.url);
  }
}
async function collect(site) {
  if (site.adapter !== 'odoo_lecomte') return collectGeneric(site);
  if (site.adapter !== 'odoo_lecomte') throw Error('日常 Chrome 扩展目前仅支持已验证的 Lecomte/Odoo 规则');
  const url = new URL(site.url);
  if (url.origin !== 'https://www.lecomte-alpirando.be' || url.pathname !== '/shop') throw Error('当前扩展仅授权 Lecomte 商品目录');
  const tab = await createOwnedTab(site);
  try {
    for (let i = 0; i < 70; i++) {
      const current = await chrome.tabs.get(tab.id);
      if (current.status === 'complete') break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    const results = await executeCapture({target: {tabId: tab.id}, func: captureCatalogue,
      args: [{origin: url.origin, search: url.search}]});
    if (!results[0]?.result) throw Error('采集没有返回完整检查点');
    return results[0].result;
  } finally {
    await cleanTrackedTab(tab.id,site.url);
  }
}
async function flush() {
  const {delivery} = await chrome.storage.session.get('delivery');
  if (!delivery) return;
  try { await api('result', delivery); }
  catch (error) { if (error.status !== 409) throw error; }
  await chrome.storage.session.remove(['delivery', 'active']);
}
async function poll() {
  if (busy) { try { await api('heartbeat'); } catch {} return; }
  busy = true;
  try {
    // Cleanup must also run when the backend is offline or an old result cannot be delivered.
    await cleanupIdleTabs();
    await api('heartbeat');
    await flush();
    const {active} = await chrome.storage.session.get('active');
    if (active) {
      if (Date.now() - active.started < 250000) return;
      await chrome.storage.session.set({delivery: {id: active.id, claim: active.claim, result: {error: '扩展采集被中断；没有提交快照'}}});
      await flush();
    }
    await cleanupIdleTabs();
    const {job} = await api('claim');
    if (!job) { await message('已连接后台，等待任务', 'idle'); return; }
    await chrome.storage.session.set({active: {id: job.id, claim: job.claim, started: Date.now()}});
    let result;
    try { await message('正在完整采集：' + job.site.id, 'running', job.site.id); result = {capture: await collect(job.site)}; }
    catch (e) { result = {error: String(e.message).slice(0,250), status: String(e.message).startsWith('ACCESS_DENIED:') ? 403 : null}; }
    await chrome.storage.session.set({delivery: {id: job.id, claim: job.claim, result}});
    await flush();
    await message(result.error || '完整采集已交给后台校验与比对', result.error ? 'error' : 'success', job.site.id);
  } catch (e) { await message(e.message, 'error'); }
  finally { busy = false; }
}
chrome.alarms.onAlarm.addListener(async alarm => {
  if(alarm.name.startsWith('tab-cleanup:')) {
    const id=Number(alarm.name.slice('tab-cleanup:'.length));
    const records=await ownedTabs();
    if(records[id])await cleanTrackedTab(id,records[id].url);
    return;
  }
  if(alarm.name==='collector'){await cleanupExpiredTabs();await poll();}
});
chrome.runtime.onStartup.addListener(() => poll());
chrome.runtime.onInstalled.addListener(() => poll());
chrome.runtime.onMessage.addListener((request, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  (async () => {
    if (request.kind === 'poll') { await poll(); return {ok:true}; }
    if (request.kind === 'cleanup') {
      if (busy) throw Error('采集正在执行，结束后会自动清理');
      busy = true;
      try {
        const result = await cleanupIdleTabs();
        await message(result.busy ? '采集任务仍在执行，保留采集页面' : '已关闭 '+result.closed+' 个可识别的采集标签页', 'idle');
        return result;
      } finally { busy = false; }
    }
    if (request.kind === 'sites') return api('sites');
    if (request.kind === 'verify') {
      if (busy) throw Error('已有任务正在执行');
      busy = true;
      try {
        const {items} = await api('sites');
        const site = items.find(s => s.id === request.site_id);
        if (!site) throw Error('请先在后台选择日常 Chrome 扩展模式');
        await message('正在进行完整性测试，不写入商品快照', 'running', site.id);
        const result = await api('verify', {site_id: site.id, capture: await collect(site)});
        await message('测试通过：' + result.products + ' 件 / ' + result.pages + ' 页，未发送通知', 'success', site.id);
        return result;
      } finally { busy = false; }
    }
    return {ok:false};
  })().then(respond).catch(async e => { await message(e.message, 'error', request?.site_id || null); respond({error:e.message}); });
  return true;
});
chrome.alarms.get('collector').then(alarm => {
  if (!alarm) chrome.alarms.create('collector', {periodInMinutes:0.5});
});
