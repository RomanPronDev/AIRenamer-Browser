const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function worker() {
  const events = {}, calls = [], session = {};
  let installedVersion='0.26.28';
  const on = name => ({addListener(fn) { events[name] = fn; }});
  const chrome = {
    sidePanel: {setPanelBehavior: async () => {}},
    runtime: {
      id: 'extension-id', lastError: null,
      getURL: () => 'chrome-extension://extension-id/',
      getManifest: () => ({version:'0.26.28'}),
      sendNativeMessage: async () => ({ok:true,result:{version:installedVersion}}),
      sendMessage: async value => { calls.push(['broadcast',value.kind]); },
      reload: () => calls.push(['reload']),
      connectNative: () => ({
        onMessage:on('nativeMessage'),onDisconnect:on('nativeDisconnect'),
        postMessage(message) { queueMicrotask(() => events.nativeMessage({id:message.id,ok:true,result:{type:message.type}})); },
      }),
      onInstalled:on('installed'),onStartup:on('startup'),onMessage:on('message'),
    },
    contextMenus:{removeAll:async()=>calls.push(['removeMenus'])},
    scripting:{getRegisteredContentScripts:async()=>[{id:'airenamer-site-old'},{id:'unrelated'}],
      unregisterContentScripts:async value=>calls.push(['unregister',value.ids])},
    downloads:{search:async query=>{calls.push(['search',query]);return [{id:1,filename:'D:/Downloads/report.pdf'}];},
      onCreated:on('downloadCreated'),onChanged:on('downloadChanged')},
    tabs:{onRemoved:on('tabRemoved')},
    alarms:{get:async()=>null,create:async(name,options)=>calls.push(['alarm',name,options]),
      onAlarm:on('alarm')},
    idle:{queryState:async()=> 'idle'},
    storage:{session:{
      set:async values=>Object.assign(session,values),
      get:async key=>({[key]:session[key]}),
      remove:async keys=>{for(const key of keys)delete session[key];},
    }},
  };
  vm.runInNewContext(fs.readFileSync(__dirname+'/background.js','utf8'),{chrome,console,setTimeout,clearTimeout,Promise,Map});
  const send = message => new Promise(resolve=>events.message(message,{id:'extension-id',url:'chrome-extension://extension-id/live.html'},resolve));
  return {events,calls,send,setInstalledVersion:value=>{installedVersion=value;}};
}

test('periodic alarm starts updater and reloads when installed host is newer', async()=>{
  const app=worker();
  app.events.alarm({name:'airenamer-update'});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(app.calls.some(item=>item[0]==='alarm' && item[1]==='airenamer-update-followup'));
  app.setInstalledVersion('0.26.29');
  app.events.alarm({name:'airenamer-update-followup'});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(app.calls.some(item=>item[0]==='reload'));
});

test('Recent Downloads queries the complete Chrome history including non-media', async()=>{
  const app=worker();
  const answer=await app.send({kind:'recent'});
  assert.equal(answer.result.downloads[0].filename,'D:/Downloads/report.pdf');
  const query=app.calls.find(item=>item[0]==='search')[1];
  assert.equal(query.limit,0);
  assert.equal(query.orderBy[0],'-startTime');
});

test('old site hooks are removed on extension update', async()=>{
  const app=worker();
  app.events.installed();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(app.calls.some(item=>item[0]==='removeMenus'));
  assert.deepEqual(Array.from(app.calls.find(item=>item[0]==='unregister')[1]),['airenamer-site-old']);
});

test('download changes notify the panel', async()=>{
  const app=worker();
  app.events.downloadCreated();
  app.events.downloadChanged();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(app.calls.filter(item=>item[0]==='broadcast').length,2);
});

test('context is stored and retrieved for a tab', async()=>{
  const app=worker();
  assert.equal((await app.send({kind:'context',tabId:7,value:{project:'A'}})).ok,true);
  assert.equal((await app.send({kind:'getContext',tabId:7})).result.project,'A');
});

test('extension pages can call native host while web pages cannot', async()=>{
  const app=worker();
  assert.equal((await app.send({kind:'native',type:'projects'})).result.type,'projects');
  const answer=await new Promise(resolve=>app.events.message({kind:'native',type:'projects'},
    {id:'other',url:'https://example.com'},resolve));
  assert.equal(answer.ok,false);
});
