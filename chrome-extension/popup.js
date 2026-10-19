// Popup UI only. All collection logic lives in worker.js; this file sends the same messages as before
// (poll / cleanup / verify / sites) and renders the worker's status from chrome.storage.local.
const $ = selector => document.querySelector(selector);
const ADMIN_URL = 'http://127.0.0.1:8080/';
const LABELS = {idle: '空闲', running: '采集中', success: '已完成', error: '失败'};
const ICONS = {idle: '#i-idle', running: '#i-loader', success: '#i-check', error: '#i-x'};
let busy = false;
let note = null;           // transient message from a popup action (shown until the worker writes new state)
let configuredOrigins = [];

function inferState(text) {
  if (!text) return 'idle';
  return /^正在/.test(text) ? 'running' : 'idle';
}
function formatTime(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay ? date.toLocaleTimeString('zh-CN', {hour12: false})
                 : date.toLocaleString('zh-CN', {hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}
function render(state) {
  const kind = note?.state || state.state || inferState(state.status);
  const text = note?.text || state.status || '尚未连接，请检查本地配置';
  $('#status-card').dataset.state = kind;
  $('#status-icon').setAttribute('href', ICONS[kind] || ICONS.idle);
  $('#state-label').textContent = LABELS[kind] || LABELS.idle;
  $('#task').textContent = (note ? note.task : state.task) || '';
  $('#status').textContent = text;
  $('#status').title = text;
  $('#updated').textContent = formatTime(note ? new Date().toISOString() : state.updated);
  const page = !note && kind === 'running' && state.progress && Number.isInteger(state.progress.page) ? state.progress.page : null;
  $('#progress').textContent = page ? '第 ' + page + ' 页' : '';
  const failed = kind === 'error';
  $('#reason-toggle').hidden = !failed;
  $('#reason-text').textContent = failed ? text : '';
  if (!failed) toggleReason(false);
}
async function refresh() {
  render(await chrome.storage.local.get(['status', 'state', 'task', 'progress', 'updated']));
}
function setBusy(value, primary = false) {
  busy = value;
  document.querySelectorAll('button, select').forEach(el => { if (el.id !== 'reason-toggle') el.disabled = value; });
  $('#verify').classList.toggle('loading', value && primary);
  $('#verify .label').textContent = value && primary ? '正在采集测试…' : '完整采集测试（不写入快照）';
}
async function action(request, primary = false) {
  if (busy) return;                       // prevent double trigger
  setBusy(true, primary);
  closeMenu();
  note = null;
  try {
    const result = await chrome.runtime.sendMessage(request);
    if (result?.error) note = {state: 'error', text: result.error, task: request.site_id || ''};
  } catch (e) { note = {state: 'error', text: e.message, task: request.site_id || ''}; }
  finally { setBusy(false); await refresh(); }
}
function toggleReason(open) {
  const show = open ?? $('#reason').hidden;
  $('#reason').hidden = !show;
  $('#reason-toggle').setAttribute('aria-expanded', String(show));
  $('#reason-toggle').textContent = show ? '收起原因' : '查看原因';
}
function closeMenu() { $('#menu').hidden = true; $('#more').setAttribute('aria-expanded', 'false'); }
async function updatePermissionHint() {
  if (!configuredOrigins.length) { delete $('#grant').dataset.attention; return; }
  let missing = [];
  for (const origin of configuredOrigins) if (!await chrome.permissions.contains({origins: [origin]})) missing.push(origin);
  if (missing.length) { $('#grant').dataset.attention = ''; $('#grant').title = '待授权：' + missing.map(o => o.replace('/*', '')).join(', '); }
  else { delete $('#grant').dataset.attention; $('#grant').title = '已授权全部已配置网站'; }
}

// Host access request must run synchronously inside the click (user gesture), so origins are loaded up front.
$('#grant').onclick = () => {
  if (!configuredOrigins.length) { note = {state: 'idle', text: '没有配置为扩展模式的网站', task: ''}; refresh(); return; }
  chrome.permissions.request({origins: configuredOrigins}).then(granted => {
    note = granted ? {state: 'success', text: '已授权：' + configuredOrigins.map(o => o.replace('/*', '')).join(', '), task: ''}
                   : {state: 'error', text: '未授权：浏览器拒绝或已取消授权', task: ''};
    refresh(); updatePermissionHint();
  }).catch(e => { note = {state: 'error', text: e.message, task: ''}; refresh(); });
};
$('#poll').onclick = () => action({kind: 'poll'});
$('#cleanup').onclick = () => action({kind: 'cleanup'});
$('#verify').onclick = () => action({kind: 'verify', site_id: $('#site').value}, true);
$('#reason-toggle').onclick = () => toggleReason();
$('#more').onclick = event => {
  event.stopPropagation();
  const open = $('#menu').hidden;
  $('#menu').hidden = !open;
  $('#more').setAttribute('aria-expanded', String(open));
  if (open) $('#cleanup').focus();
};
$('#settings').onclick = () => chrome.tabs.create({url: ADMIN_URL});
document.addEventListener('click', event => {
  if (!event.target.closest('.more')) closeMenu();
  if (!event.target.closest('#reason, #reason-toggle')) toggleReason(false);
});
document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeMenu(); toggleReason(false); } });

chrome.runtime.sendMessage({kind: 'sites'}).then(result => {
  configuredOrigins = [...new Set((result?.items || []).map(s => { try { return new URL(s.url).origin + '/*'; } catch { return null; } }).filter(Boolean))];
  for (const site of result?.items || []) {
    const option = document.createElement('option'); option.value = site.id; option.textContent = site.name; $('#site').append(option);
  }
  if (!result?.items?.length) { const option = new Option('暂无扩展模式网站', ''); option.disabled = true; option.selected = true; $('#site').append(option); }
  if (result?.error) { note = {state: 'error', text: result.error, task: ''}; refresh(); }
  updatePermissionHint();
}).catch(e => { note = {state: 'error', text: e.message, task: ''}; refresh(); });
chrome.storage.onChanged.addListener(() => { if (!busy) note = null; refresh(); });
refresh();
$('#extension-version').textContent = '扩展 ' + chrome.runtime.getManifest().version + ' · 任务页自动关闭';
