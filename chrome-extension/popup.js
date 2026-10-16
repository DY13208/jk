async function refresh() {
  const state = await chrome.storage.local.get(['status','updated']);
  document.querySelector('#status').textContent = state.status || '尚未连接，请检查本机配置';
  document.querySelector('#updated').textContent = state.updated ? new Date(state.updated).toLocaleString('zh-CN') : '';
}
async function action(request) {
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    const result = await chrome.runtime.sendMessage(request);
    if (result?.error) document.querySelector('#status').textContent = result.error;
    else await refresh();
  } catch (e) { document.querySelector('#status').textContent = e.message; }
  finally { document.querySelectorAll('button').forEach(b => b.disabled = false); }
}
document.querySelector('#poll').onclick = () => action({kind:'poll'});
document.querySelector('#cleanup').onclick = () => action({kind:'cleanup'});
document.querySelector('#verify').onclick = () => action({kind:'verify',site_id:document.querySelector('#site').value});
chrome.runtime.sendMessage({kind:'sites'}).then(result => {
  for (const site of result?.items || []) {
    const option = document.createElement('option'); option.value = site.id; option.textContent = site.name; document.querySelector('#site').append(option);
  }
  if (result?.error) document.querySelector('#status').textContent = result.error;
}).catch(e => document.querySelector('#status').textContent = e.message);
chrome.storage.onChanged.addListener(refresh);
refresh();
