const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function worker(desktopDragMode='browser',desktopDragModeRevision=1,options={}) {
  const events = {}, calls = [], session = {},local=options.local||{};
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
        postMessage(message) { calls.push(['native',message.type]);queueMicrotask(() => events.nativeMessage({id:message.id,ok:!options.fail?.(message),result:options.result?.(message)||{type:message.type},error:'Rejected'})); },
      }),
      onInstalled:on('installed'),onStartup:on('startup'),onMessage:on('message'),
    },
    contextMenus:{removeAll:async()=>calls.push(['removeMenus'])},
    scripting:{getRegisteredContentScripts:async()=>[{id:'airenamer-site-old'},{id:'unrelated'}],
      unregisterContentScripts:async value=>calls.push(['unregister',value.ids])},
    downloads:{search:async query=>{calls.push(['search',query]);return options.downloads||[{id:1,filename:'D:/Downloads/report.pdf'}];},
      onCreated:on('downloadCreated'),onChanged:on('downloadChanged')},
    tabs:{onRemoved:on('tabRemoved')},
    alarms:{get:async()=>null,create:async(name,options)=>calls.push(['alarm',name,options]),
      onAlarm:on('alarm')},
    idle:{queryState:async()=> 'idle'},
    storage:{local:{get:async()=>({desktopDragMode,desktopDragModeRevision,...local}),set:async value=>Object.assign(local,value)},session:{
      set:async values=>Object.assign(session,values),
      get:async key=>({[key]:session[key]}),
      remove:async keys=>{for(const key of keys)delete session[key];},
    }},
  };
  vm.runInNewContext(fs.readFileSync(__dirname+'/background.js','utf8'),{chrome,console,setTimeout,clearTimeout,Promise,Map});
  const send = message => new Promise(resolve=>events.message(message,{id:'extension-id',url:'chrome-extension://extension-id/live.html'},resolve));
  return {events,calls,send,setInstalledVersion:value=>{installedVersion=value;}};
}

test('Chrome startup warms native drag and respects an explicit browser fallback',async()=>{
  const enabled=worker('native'),disabled=worker();
  enabled.events.startup();disabled.events.startup();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(enabled.calls.some(item=>item[0]==='native'&&item[1]==='drag_prepare'));
  assert(!disabled.calls.some(item=>item[0]==='native'&&item[1]==='drag_prepare'));
});

test('Chrome startup prepares native drag without a previous preference',async()=>{
  const app=worker('browser',0);
  app.events.startup();await new Promise(resolve=>setTimeout(resolve,0));
  assert(app.calls.some(item=>item[0]==='native'&&item[1]==='drag_prepare'));
});

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

test('reopening reuses filesystem metadata and concurrent reads do not duplicate a scan',async()=>{
  const app=worker();
  const request={kind:'native',type:'files',payload:{project:'A',shot:'SH010'}};
  await Promise.all([app.send(request),app.send(request)]);
  await app.send(request);
  assert.equal(app.calls.filter(item=>item[0]==='native'&&item[1]==='files').length,1);
  await app.send({...request,fresh:true});
  assert.equal(app.calls.filter(item=>item[0]==='native'&&item[1]==='files').length,2);
});
test('moves, new shots and structure changes invalidate cached results',async()=>{
  const app=worker(),request={kind:'native',type:'files',payload:{project:'A',shot:'SH010'}};
  await app.send(request);
  for(const type of ['move_file','create_shot','set_preferences','reset_project_structure']){
    await app.send({kind:'native',type});await app.send(request);
  }
  assert.equal(app.calls.filter(item=>item[0]==='native'&&item[1]==='files').length,5);
});
test('failed reads are retried instead of being cached',async()=>{
  let fail=true;
  const app=worker('browser',1,{fail:()=>fail});
  const request={kind:'native',type:'files'};
  assert.equal((await app.send(request)).ok,false);
  fail=false;assert.equal((await app.send(request)).ok,true);
  assert.equal(app.calls.filter(item=>item[0]==='native'&&item[1]==='files').length,2);
});
test('panel snapshot survives closure but is discarded when its native host disconnects',async()=>{
  const app=worker();await app.send({kind:'native',type:'projects'});
  await app.send({kind:'savePanel',snapshot:{version:'0.26.28',files:[{name:'frame.png'}]}});
  assert.equal((await app.send({kind:'getPanel'})).result.files[0].name,'frame.png');
  app.events.nativeDisconnect();
  assert.equal((await app.send({kind:'getPanel'})).result,null);
  await app.send({kind:'native',type:'projects'});
  assert.equal(app.calls.filter(item=>item[0]==='native'&&item[1]==='projects').length,2);
});
test('download history cache is refreshed on Chrome download events',async()=>{
  const app=worker();await app.send({kind:'recent'});await app.send({kind:'recent'});
  assert.equal(app.calls.filter(item=>item[0]==='search').length,1);
  app.events.downloadChanged();await app.send({kind:'recent'});
  assert.equal(app.calls.filter(item=>item[0]==='search').length,2);
});

test('successful To shot import persists exactly its download across worker restarts',async()=>{
  const local={},downloads=[{id:7,state:'complete',filename:'D:/Downloads/frame.png',startTime:'2026-10-09T10:00:00Z'}];
  const app=worker('browser',1,{local,downloads,result:message=>message.type==='import_file'?{path:'X:/Project/shot/frame.png'}:null});
  await app.send({kind:'native',type:'import_file',payload:{source:downloads[0].filename,downloadId:7,project:'Project',sequence:'30',shot:'SH010'}});
  assert.equal(local.assignedDownloads.length,1);assert.equal(local.assignedDownloads[0].shot,'SH010');
  const reopened=worker('browser',1,{local,downloads});
  assert.equal((await reopened.send({kind:'recent'})).result.managed[0].id,7);
});
test('failed import never marks a download as assigned',async()=>{
  const local={},app=worker('browser',1,{local,downloads:[{id:1,state:'complete',filename:'D:/Downloads/a.png'}],fail:message=>message.type==='import_file'});
  const answer=await app.send({kind:'native',type:'import_file',payload:{source:'D:/Downloads/a.png',downloadId:1}});
  assert.equal(answer.ok,false);assert.equal(local.assignedDownloads,undefined);
});
test('several historic downloads of the same path require an exact download ID',async()=>{
  const local={},downloads=[1,2].map(id=>({id,state:'complete',filename:'D:/Downloads/a.png',startTime:String(id)}));
  const app=worker('browser',1,{local,downloads,result:message=>message.type==='import_file'?{path:'X:/saved.png'}:null});
  await app.send({kind:'native',type:'import_file',payload:{source:'D:/Downloads/a.png'}});
  assert.equal(local.assignedDownloads,undefined);
  await app.send({kind:'native',type:'import_file',payload:{source:'D:/Downloads/a.png',downloadId:2}});
  assert.equal(local.assignedDownloads[0].id,2);
});
test('Chrome startup prepares FFmpeg even when the user chose browser drag fallback',async()=>{
  const app=worker();await new Promise(resolve=>setTimeout(resolve,0));
  assert(app.calls.some(call=>call[0]==='native'&&call[1]==='ffmpeg_setup'));
});
