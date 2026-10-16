const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const removed = [], state = {};
let tabUrl = 'https://www.lecomte-alpirando.be/shop?attribute_value=729-2672';
let removeFails = false, getError = null;
const listener = {addListener() {}};
const chrome = {
  tabs: {
    get: async () => { if (getError) throw getError; return {url:tabUrl,status:'complete'}; },
    create: async () => ({id:100}),
    remove: async id => { if (removeFails) throw Error('temporarily unavailable'); removed.push(id); }
  },
  storage: {
    local: {set:async values=>Object.assign(state,values)},
    session: {
      get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(key=>[key,state[key]])),
      set:async values=>Object.assign(state,values),
      remove:async keys=>{for(const key of Array.isArray(keys)?keys:[keys])delete state[key];}
    }
  },
  runtime:{onStartup:listener,onInstalled:listener,onMessage:listener},
  alarms:{onAlarm:listener,get:async()=>({}),create:async()=>{}}
};
const ctx = vm.createContext({chrome,URL,AbortSignal,importScripts(){},setTimeout});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../chrome-extension/worker.js'),'utf8'),ctx);
(async()=>{
  const target=tabUrl;
  assert.equal(await ctx.closeOwnedTab(1,target),'closed');
  tabUrl='https://www.lecomte-alpirando.be/en/shop/page/2?attribute_value=729-2672';
  assert.equal(await ctx.closeOwnedTab(2,target),'closed');
  tabUrl='https://www.lecomte-alpirando.be/fr_BE/shop/page/5/?b=2&attribute_value=729-2672';
  assert.equal(await ctx.closeOwnedTab(3,target+'&b=2'),'closed');
  for(const url of ['https://www.lecomte-alpirando.be/shop/cart',
    'https://www.lecomte-alpirando.be/shop/checkout',
    'https://www.lecomte-alpirando.be/shop/product-44892',
    'https://www.lecomte-alpirando.be/shop?attribute_value=729-9999',
    'https://example.test/']) {
    tabUrl=url;state.ownedTabs={'90':{id:90,url:target}};
    await ctx.cleanupIdleTabs();assert.equal(state.ownedTabs['90'],undefined);
  }
  assert.deepEqual(removed,[1,2,3,90,90,90,90,90]);
  // A redirect outside host permissions may hide URL metadata; ownership still suffices.
  tabUrl=undefined;state.ownedTabs={'91':{id:91,url:target}};
  await ctx.cleanupIdleTabs();assert.ok(removed.includes(91));
  const untouched=removed.length;await ctx.cleanupIdleTabs();
  assert.equal(removed.length,untouched);assert.ok(!removed.includes(999));

  // Both successful and failed collection close their created tab in finally.
  ctx.captureCatalogue=function(){};
  chrome.scripting={executeScript:async()=>[{result:{checkpoints:[]}}]};
  const site={adapter:'odoo_lecomte',url:target};
  await ctx.collect(site);assert.equal(removed.at(-1),100);assert.equal(state.ownedTabs['100'],undefined);
  chrome.scripting.executeScript=async()=>{throw Error('capture failed');};
  const successCloses=removed.length;
  await assert.rejects(()=>ctx.collect(site),/capture failed/);
  assert.equal(removed.length,successCloses+1);assert.equal(state.ownedTabs['100'],undefined);

  // Transient close failures retain ownership, allowing the next alarm to retry.
  tabUrl=target;state.ownedTabs={'4':{id:4,url:target}};removeFails=true;
  await ctx.cleanTrackedTab(4,target);assert.ok(state.ownedTabs['4']);
  removeFails=false;await ctx.cleanupIdleTabs();assert.equal(state.ownedTabs['4'],undefined);

  // Cleanup occurs before heartbeat, even when the local server is unreachable.
  state.tab={id:5,url:target};state.active={id:'abandoned',started:Date.now()-300000};
  ctx.api=async()=>{throw Error('backend offline');};
  await ctx.poll();assert.ok(removed.includes(5));assert.equal(state.tab,undefined);

  // Never sweep unrelated tabs; only explicitly recorded extension-created IDs are visited.
  const before=removed.length;state.ownedTabs={'6':{id:6,url:target}};
  state.active={id:'running',started:Date.now()};
  await ctx.cleanupIdleTabs();assert.equal(removed.length,before);assert.ok(state.ownedTabs['6']);
  delete state.active;
  getError=Error('No tab with id: 6');await ctx.cleanupIdleTabs();assert.equal(state.ownedTabs['6'],undefined);
  getError=null;

  // Transport failures keep the original result; they never rerun collection.
  state.delivery={id:'job',claim:'test-only',result:{error:'stopped'}};state.active={id:'job'};
  ctx.api=async()=>{const e=Error('network unavailable');e.status=503;throw e;};
  await assert.rejects(()=>ctx.flush());assert.ok(state.delivery);
  ctx.api=async()=>{const e=Error('already accepted');e.status=409;throw e;};
  await ctx.flush();assert.equal(state.delivery,undefined);assert.equal(state.active,undefined);
  console.log('PASS: owned-tab redirects and hidden URLs, success/failure cleanup, unrelated tabs untouched, retry retention, offline cleanup, interrupted/active tasks, legacy migration, delivery retry');
})().catch(e=>{console.error(e);process.exitCode=1;});
