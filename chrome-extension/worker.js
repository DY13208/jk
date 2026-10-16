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
async function message(text) { await chrome.storage.local.set({status: text, updated: new Date().toISOString()}); }
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
  if (outcome === 'retry') records[id] = {id,url:target};
  else delete records[id];
  await chrome.storage.session.set({ownedTabs:records});
  return outcome;
}
async function cleanupIdleTabs() {
  const {active} = await chrome.storage.session.get('active');
  if (active && Date.now() - active.started < 250000) return {busy:true,closed:0};
  const records = await ownedTabs();
  let closed = 0;
  for (const record of Object.values(records)) if (await cleanTrackedTab(record.id,record.url) === 'closed') closed++;
  return {closed};
}
async function collect(site) {
  if (site.adapter !== 'odoo_lecomte') throw Error('日常 Chrome 扩展目前仅支持已验证的 Lecomte/Odoo 规则');
  const url = new URL(site.url);
  if (url.origin !== 'https://www.lecomte-alpirando.be' || url.pathname !== '/shop') throw Error('当前扩展仅授权 Lecomte 商品目录');
  const tab = await chrome.tabs.create({url: site.url, active: false});
  try {
    const records = await ownedTabs();
    records[tab.id] = {id:tab.id,url:site.url};
    await chrome.storage.session.set({ownedTabs:records});
    for (let i = 0; i < 70; i++) {
      const current = await chrome.tabs.get(tab.id);
      if (current.status === 'complete') break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    const results = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: captureCatalogue,
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
    if (!job) { await message('已连接后台，等待任务'); return; }
    await chrome.storage.session.set({active: {id: job.id, claim: job.claim, started: Date.now()}});
    let result;
    try { await message('正在完整采集：' + job.site.id); result = {capture: await collect(job.site)}; }
    catch (e) { result = {error: String(e.message).slice(0,250), status: String(e.message).startsWith('ACCESS_DENIED:') ? 403 : null}; }
    await chrome.storage.session.set({delivery: {id: job.id, claim: job.claim, result}});
    await flush();
    await message(result.error || '完整采集已交给后台校验与比对');
  } catch (e) { await message(e.message); }
  finally { busy = false; }
}
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'collector') poll(); });
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
        await message(result.busy ? '采集任务仍在执行，保留采集页面' : '已关闭 '+result.closed+' 个可识别的采集标签页');
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
        await message('正在进行完整性测试，不写入商品快照');
        const result = await api('verify', {site_id: site.id, capture: await collect(site)});
        await message('测试通过：' + result.products + ' 件 / ' + result.pages + ' 页，未发送通知');
        return result;
      } finally { busy = false; }
    }
    return {ok:false};
  })().then(respond).catch(async e => { await message(e.message); respond({error:e.message}); });
  return true;
});
chrome.alarms.get('collector').then(alarm => {
  if (!alarm) chrome.alarms.create('collector', {periodInMinutes:0.5});
});
