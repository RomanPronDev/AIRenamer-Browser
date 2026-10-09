const HOST = "com.airenamer.browser";
let nativePort = null;
let nextId = 0;
const nativePending = new Map();
const UPDATE_ALARM = "airenamer-update";
const FOLLOWUP_ALARM = "airenamer-update-followup";
// Keep lightweight metadata while Chrome runs, including while its panel is closed.
const readCache=new Map(),inflightReads=new Map();
const cacheReads=new Set(['projects','navigation','files','preview','preview_download']);
const mutationTypes=new Set(['add_project','change_project_path','remove_project','set_preferences',
  'set_default_structure','set_project_structure','reset_project_structure','set_ignored_names','create_shot','create_media_folders',
  'import_file','rename_file','move_file','batch_rename','transfer_finish']);
let cacheEpoch=0,panelSnapshot=null,recentCache=null,recentPending=null,recentEpoch=0;
let panelUpdateCheck=null;
let managedWrites=Promise.resolve();
async function markAssignedDownload(payload,result){
  if(!payload.source||!result?.path)return;
  const downloads=(await getRecent()).downloads;
  const candidates=downloads.filter(item=>item.state==='complete'&&item.filename===payload.source&&
    (Number.isSafeInteger(payload.downloadId)?item.id===payload.downloadId:true));
  // A path alone can refer to several historical downloads. Never guess which
  // overwritten file was imported; To shot supplies the exact Chrome ID.
  if(candidates.length!==1)return;
  const item=candidates[0];
  const record={id:item.id,source:item.filename,startTime:item.startTime||'',path:result.path,
    project:payload.project,sequence:payload.sequence||null,shot:payload.shot,assignedAt:Date.now()};
  const write=managedWrites.then(async()=>{
    const stored=await chrome.storage.local.get('assignedDownloads');
    const entries=Array.isArray(stored.assignedDownloads)?stored.assignedDownloads:[];
    const records=[record,...entries.filter(entry=>entry.id!==record.id)].slice(0,5000);
    await chrome.storage.local.set({assignedDownloads:records});
    recentCache=null;
    chrome.runtime.sendMessage({kind:'recentChanged'}).catch(()=>{});
  });
  managedWrites=write.catch(()=>{});await write;
}
function invalidateNativeCache(){cacheEpoch++;readCache.clear();inflightReads.clear();panelSnapshot=null;}
async function cachedNativeCall(type,payload={},fresh=false){
  if(!cacheReads.has(type)){
    const result=await nativeCall(type,payload);
    if(mutationTypes.has(type)||type==='convert_status'&&result.state==='complete')invalidateNativeCache();
    if(type==='import_file'){
      try{await markAssignedDownload(payload,result);}catch(error){console.error('Could not record assigned download',error);}
    }
    return result;
  }
  const key=JSON.stringify([type,payload]),cached=readCache.get(key);
  if(!fresh&&cached&&Date.now()-cached.at<cached.ttl)return cached.result;
  if(!fresh&&inflightReads.has(key))return inflightReads.get(key);
  const epoch=cacheEpoch;
  const pending=nativeCall(type,payload).then(result=>{
    if(epoch===cacheEpoch){
      readCache.delete(key);
      readCache.set(key,{result,at:Date.now(),ttl:type.startsWith('preview')?300000:30000});
      // No original media bytes are cached. Bound even unusually large metadata/thumbnail results.
      while(readCache.size>32||JSON.stringify([...readCache]).length>4*1024*1024)
        readCache.delete(readCache.keys().next().value);
    }
    return result;
  }).finally(()=>{if(inflightReads.get(key)===pending)inflightReads.delete(key);});
  inflightReads.set(key,pending);return pending;
}

chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(console.error);

function newerVersion(candidate, current) {
  const valid = value => typeof value === "string" && /^\d+\.\d+\.\d+$/.test(value);
  if (!valid(candidate) || !valid(current)) return false;
  const next = candidate.split(".").map(Number), loaded = current.split(".").map(Number);
  for (let i = 0; i < 3; i++) if (next[i] !== loaded[i]) return next[i] > loaded[i];
  return false;
}
async function installedCompanionVersion() {
  const answer = await chrome.runtime.sendNativeMessage(HOST, {id:0,type:"ping",payload:{}});
  if (!answer?.ok) throw new Error(answer?.error || "Local companion unavailable");
  return answer.result?.version;
}
async function reloadIfUpdated() {
  if (!newerVersion(await installedCompanionVersion(), chrome.runtime.getManifest().version)) return false;
  chrome.runtime.reload();
  return true;
}
async function ensureUpdateAlarm() {
  if (!await chrome.alarms.get(UPDATE_ALARM)) {
    await chrome.alarms.create(UPDATE_ALARM,{delayInMinutes:5,periodInMinutes:360});
  }
}
async function checkForUpdate() {
  const current=chrome.runtime.getManifest().version;
  if (newerVersion(await installedCompanionVersion(),current)) {
    const state=await chrome.idle.queryState(300);
    if (state!=="active") { chrome.runtime.reload(); return; }
    await chrome.alarms.create(FOLLOWUP_ALARM,{delayInMinutes:15});
    return;
  }
  await nativeCall("start_update");
  await chrome.alarms.create(FOLLOWUP_ALARM,{delayInMinutes:15});
}
function nativeCall(type, payload={}) {
  return new Promise((resolve,reject)=>{
    try {
      if (!nativePort) {
        nativePort = chrome.runtime.connectNative(HOST);
        nativePort.onMessage.addListener(answer=>{
          const pending=nativePending.get(answer.id);if(!pending)return;
          nativePending.delete(answer.id);clearTimeout(pending.timer);
          if(answer.ok)pending.resolve(answer.result);
          else pending.reject(new Error(answer.error||"Local companion error"));
        });
        nativePort.onDisconnect.addListener(()=>{
          invalidateNativeCache();
          const reason=chrome.runtime.lastError?.message||"Local companion disconnected";
          for(const pending of nativePending.values()){
            clearTimeout(pending.timer);pending.reject(new Error(reason));
          }
          nativePending.clear();nativePort=null;
        });
      }
      const id=++nextId;
      const timer=setTimeout(()=>{nativePending.delete(id);reject(new Error("Local companion is not responding"));},
        ["add_project","change_project_path","choose_project_folder"].includes(type)?300000:120000);
      nativePending.set(id,{resolve,reject,timer});
      nativePort.postMessage({id,type,payload});
    }catch(error){reject(error);}
  });
}
async function getRecent() {
  // limit:0 asks Chrome for the entire download history, including non-media files.
  const stored=await chrome.storage.local.get('assignedDownloads');
  const managed=Array.isArray(stored.assignedDownloads)?stored.assignedDownloads:[];
  if(recentCache&&Date.now()-recentCache.at<10000)return {...recentCache.result,managed};
  if(recentPending)return {...await recentPending,managed};
  const epoch=recentEpoch;
  const pending=chrome.downloads.search({limit:0,orderBy:["-startTime"]}).then(downloads=>{
    const result={downloads,managed:[]};
    if(epoch===recentEpoch)recentCache={at:Date.now(),result};
    return result;
  }).finally(()=>{if(recentPending===pending)recentPending=null;});
  recentPending=pending;return {...await pending,managed};
}
async function removeOldSiteHooks() {
  if (chrome.contextMenus?.removeAll) await chrome.contextMenus.removeAll().catch(()=>{});
  if (chrome.scripting?.getRegisteredContentScripts) {
    const scripts=await chrome.scripting.getRegisteredContentScripts().catch(()=>[]);
    const ids=scripts.map(item=>item.id).filter(id=>id.startsWith("airenamer-site-"));
    if(ids.length)await chrome.scripting.unregisterContentScripts({ids}).catch(()=>{});
  }
}
chrome.runtime.onInstalled.addListener(()=>{
  removeOldSiteHooks().catch(()=>{});
  ensureUpdateAlarm().catch(()=>{});
  warmNativeDragIfEnabled().catch(()=>{});
});
chrome.runtime.onStartup.addListener(()=>{
  removeOldSiteHooks().catch(()=>{});
  reloadIfUpdated().catch(()=>{});
  ensureUpdateAlarm().catch(()=>{});
  warmNativeDragIfEnabled().catch(()=>{});
});
chrome.alarms.onAlarm.addListener(alarm=>{
  if (alarm.name===UPDATE_ALARM)checkForUpdate().catch(console.error);
  if (alarm.name===FOLLOWUP_ALARM) {
    installedCompanionVersion().then(version=>{
      if (newerVersion(version,chrome.runtime.getManifest().version)) return checkForUpdate();
    }).catch(console.error);
  }
});
ensureUpdateAlarm().catch(()=>{});
async function warmNativeDragIfEnabled() {
  const stored=await chrome.storage.local?.get(['desktopDragMode','desktopDragModeRevision']);
  if(stored?.desktopDragModeRevision!==1||stored?.desktopDragMode!=='browser')await nativeCall('drag_prepare');
  await nativeCall('ffmpeg_setup');
}
warmNativeDragIfEnabled().catch(()=>{});
function downloadsChanged(){
  recentEpoch++;recentCache=null;recentPending=null;
  chrome.runtime.sendMessage({kind:"recentChanged"}).catch(()=>{});
}
chrome.downloads.onCreated.addListener(downloadsChanged);
chrome.downloads.onChanged.addListener(downloadsChanged);
chrome.tabs.onRemoved.addListener(tabId=>{
  chrome.storage.session.remove(["destination:"+tabId,"context:"+tabId]).catch(()=>{});
});
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.kind==="recentChanged")return false;
  if(sender.id!==chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL(""))){
    sendResponse({ok:false,error:"Unauthorized sender"});return false;
  }
  (async()=>{
    if(message.kind==="checkUpdate"){
      if(panelUpdateCheck&&Date.now()-panelUpdateCheck.at<300000)return panelUpdateCheck.result;
      const updated=newerVersion(await installedCompanionVersion(),chrome.runtime.getManifest().version);
      if(updated)setTimeout(()=>chrome.runtime.reload(),150);
      const result={reloading:updated};panelUpdateCheck={at:Date.now(),result};return result;
    }
    if(message.kind==="native")return cachedNativeCall(message.type,message.payload||{},message.fresh===true);
    if(message.kind==='getPanel')return nativePort?panelSnapshot:null;
    if(message.kind==='savePanel'){
      const snapshot=message.snapshot;
      if(nativePort&&snapshot?.version===chrome.runtime.getManifest().version&&
        JSON.stringify(snapshot).length<=2*1024*1024)panelSnapshot=snapshot;
      return {saved:!!panelSnapshot};
    }
    if(message.kind==="recent")return getRecent();
    if(message.kind==="destination"||message.kind==="context"){
      const tabId=Number(message.tabId);
      if(!Number.isSafeInteger(tabId)||tabId<0)throw new Error("Invalid tab");
      const key=(message.kind==="destination"?"destination:":"context:")+tabId;
      if(message.value)await chrome.storage.session.set({[key]:message.value});
      else await chrome.storage.session.remove(key);
      return {saved:true};
    }
    if(message.kind==="getDestination"||message.kind==="getContext"){
      const tabId=Number(message.tabId);
      if(!Number.isSafeInteger(tabId)||tabId<0)throw new Error("Invalid tab");
      const key=(message.kind==="getDestination"?"destination:":"context:")+tabId;
      const values=await chrome.storage.session.get(key);
      return values[key]||null;
    }
    throw new Error("Unknown command");
  })().then(result=>sendResponse({ok:true,result}),error=>
    sendResponse({ok:false,error:String(error.message||error)}));
  return true;
});
