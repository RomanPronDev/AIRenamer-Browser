const HOST = "com.airenamer.browser";
let nativePort = null;
let nextId = 0;
const nativePending = new Map();
const UPDATE_ALARM = "airenamer-update";
const FOLLOWUP_ALARM = "airenamer-update-followup";

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
  return {downloads:await chrome.downloads.search({limit:0,orderBy:["-startTime"]}),managed:[]};
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
}
warmNativeDragIfEnabled().catch(()=>{});
chrome.downloads.onCreated.addListener(()=>{chrome.runtime.sendMessage({kind:"recentChanged"}).catch(()=>{});});
chrome.downloads.onChanged.addListener(()=>{chrome.runtime.sendMessage({kind:"recentChanged"}).catch(()=>{});});
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
      const updated=newerVersion(await installedCompanionVersion(),chrome.runtime.getManifest().version);
      if(updated)setTimeout(()=>chrome.runtime.reload(),150);
      return {reloading:updated};
    }
    if(message.kind==="native")return nativeCall(message.type,message.payload||{});
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
