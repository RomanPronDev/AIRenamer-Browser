(() => {
"use strict";
const root = document.getElementById("root");
const state = {
  connected: false, projects: [], categories: [], project: "", layout: "",
  sequences: [], sequence: "", shots: [], shot: "", files: [],
  recent: {downloads: [], managed: []}, tabId: null, windowId: null, query: "",
  selected: new Set(), modal: null, openMenu: null, busy: false, error: "",
  generation: 0, theme: "system", paneHeights: {shots:126,files:240}, resizing: null,
  conversions: new Map(),
};
const mediaExt = /\.(png|jpe?g|tiff?|webp|avif|gif|exr|mp4|mov|mkv|webm|avi|m4v)$/i;
const thumbnailCache = new Map();
const dragFileCache = new Map();
const MAX_BROWSER_DRAG_BYTES = 128 * 1024 * 1024;
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const q = selector => root.querySelector(selector);
const key = item => item.category + "/" + item.name;
const selectedFiles = () => state.files.filter(item => state.selected.has(key(item)));
const currentDestination = () => state.project && state.shot ?
  {project:state.project, sequence:state.sequence || null, shot:state.shot} : null;

async function message(kind, payload = {}) {
  const answer = await chrome.runtime.sendMessage({kind, ...payload});
  if (!answer?.ok) throw new Error(answer?.error || "Extension is not responding");
  return answer.result;
}
const native = (type, payload) => message("native", {type, payload});
function status(error) {
  state.error = String(error?.message || error || "");
  render();
}
function icon(name) {
  const paths = {
    chevron:'<path d="m9 18 6-6-6-6"/>', search:'<circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/>',
    plus:'<path d="M12 4v16M4 12h16"/>', folder:'<path d="M3 7V5h7l2 3h9v12H3z"/>',
    download:'<path d="M12 3v12m-5-5 5 5 5-5M5 15v5h14v-5"/>',
    refresh:'<path d="M20 10a8 8 0 1 0-1 8M20 3v7h-7"/>',
    settings:'<path d="M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z"/><circle cx="12" cy="12" r="3"/>',
    external:'<path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6"/>',
    move:'<path d="M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4"/>',
    copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    image:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
    film:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4m-4 8h4m10-8h4m-4 8h4"/>',
    close:'<path d="M6 6l12 12M18 6 6 18"/>',
    check:'<path d="m4 12 5 5L20 6"/>',
    alert:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4v1"/>',
    upload:'<path d="M12 16V4m-5 5 5-5 5 5M4 16v4h16v-4"/>',
  };
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[name] || paths.image) + '</svg>';
}
function selectHtml(list, selected, empty) {
  return '<option value="">'+esc(empty)+'</option>' +
    list.map(item => '<option value="'+esc(item)+'" '+(item===selected?"selected":"")+'>'+esc(item)+'</option>').join("");
}
function fileRows() {
  if (!state.shot) return '<div class="empty">Select a shot to view its files.</div>';
  if (!state.files.length) return '<div class="empty">No media files in this shot yet.</div>';
  return state.categories.map(category => {
    const items = state.files.filter(item => item.category === category.id);
    if (!items.length) return "";
    return '<section class="category"><div class="section-title">'+esc(category.folder)+'<span>'+items.length+'</span></div>'+
      items.map(item => '<div class="file-row" data-category="'+esc(item.category)+'" data-name="'+esc(item.name)+'" draggable="true">'+
      '<label class="file-check"><input type="checkbox" data-select="'+esc(key(item))+'" '+(state.selected.has(key(item))?"checked":"")+' aria-label="Select '+esc(item.name)+'"></label>'+
      '<div class="file-icon">'+icon(category.mediaType==="video"?"film":"image")+'</div>'+
      '<div class="file-data"><button class="file-name" data-action="preview" data-category="'+esc(item.category)+'" data-name="'+esc(item.name)+'">'+esc(item.name)+'</button>'+
      '<div class="file-sub">'+(item.version!==null?'v'+String(item.version).padStart(3,"0"):"No version")+
      (item.subversion ? ' · '+String(item.subversion).padStart(2,"0") : "")+
      (item.hasPsd?" · PSD":"")+(item.converted?" · PNG sequence":"")+
      (state.conversions.has(item.path)?" · Converting to PNG…":"")+'</div></div>'+
      '<button class="icon-button folder-shortcut" data-action="open-folder" data-category="'+esc(item.category)+'" data-name="'+esc(item.name)+'" aria-label="Show '+esc(item.name)+' in Explorer" title="Show in Explorer">'+icon("folder")+'</button>'+
      '<button class="icon-button" data-action="file-menu" data-category="'+esc(item.category)+'" data-name="'+esc(item.name)+'" aria-label="Actions for '+esc(item.name)+'">⋯</button></div>').join("")+'</section>';
  }).join("");
}
function recentRows() {
  const items=state.recent.downloads.map(item=>({
    id:"d"+item.id,downloadId:item.id,
    name:item.filename?.split(/[\\/]/).pop()||item.url?.split("/").pop()||"Download",
    state:item.state==="complete"?"complete":item.state==="interrupted"?"error":"working",
    source:(()=>{try{return new URL(item.finalUrl||item.url).hostname;}catch{return "Chrome";}})(),
    path:item.filename,error:item.error,
  }));
  return {count:items.length,html:items.length?items.map(item=>{
    const media=item.path&&mediaExt.test(item.path);
    return '<div class="recent-row"><span class="recent-state '+esc(item.state)+'"></span>'+
      (item.state==="complete"&&media?
        '<button class="recent-thumb" data-action="load-thumbnail" data-recent="'+esc(item.id)+'" data-recent-id="'+esc(item.id)+'" aria-label="Load preview of '+esc(item.name)+'" title="Load preview">'+icon("image")+'</button>':
        '<span class="recent-thumb">'+icon("download")+'</span>')+
      '<div class="recent-data">'+(item.state==="complete"&&media?
        '<button class="recent-name-button" data-action="preview-recent" data-recent="'+esc(item.id)+'" title="Preview '+esc(item.name)+'">'+esc(item.name)+'</button>':
        '<span class="recent-name">'+esc(item.name)+'</span>')+
      '<small>'+esc(item.source)+(item.state==="working"?" · downloading":item.state==="error"?" · "+esc(item.error||"error"):"")+'</small></div>'+
      (item.state==="complete"&&state.shot&&media?
       '<button class="small-action" data-action="assign-download" data-id="'+item.downloadId+'">To shot</button>':"")+'</div>';
  }).join(""):'<div class="empty compact">No downloads yet.</div>'};
}
function thumbnailItem(id){return state.recent.downloads.find(item=>"d"+item.id===id);}
function paintCachedThumbnails(){
  root.querySelectorAll(".recent-thumb").forEach(node=>{
    const data=thumbnailCache.get(node.dataset.recentId);
    if(data&&!(data instanceof Promise))paintThumbnail(node,data);
  });
}
function loadRecentThumbnail(id){
  const item=thumbnailItem(id);
  if(!item||item.state!=="complete"||!item.filename||!mediaExt.test(item.filename))
    throw new Error("Preview is unavailable for this file");
  if(thumbnailCache.has(id))return thumbnailCache.get(id);
  for(const node of root.querySelectorAll('.recent-thumb[data-recent-id="'+id+'"]')){
    node.classList.add("loading");
    node.setAttribute("aria-busy","true");
  }
  const pending=native("preview_download",{path:item.filename}).then(data=>{
    thumbnailCache.set(id,data);
    for(const node of root.querySelectorAll('.recent-thumb[data-recent-id="'+id+'"]')){
      node.classList.remove("loading");
      node.removeAttribute("aria-busy");
      paintThumbnail(node,data);
    }
    return data;
  }).catch(error=>{
    thumbnailCache.delete(id);
    throw error;
  });
  thumbnailCache.set(id,pending);
  return pending;
}
function paintThumbnail(node,data){
  if(!data?.available)return;
  if(data.mediaType==="video"){
    node.innerHTML='<video muted playsinline preload="metadata" src="'+esc(data.url)+'"></video>';
    const video=node.querySelector("video");
    video.addEventListener("loadedmetadata",()=>{video.currentTime=Math.min(0.1,(video.duration||0)/2)||0.1;},{once:true});
  }else if(data.data){
    node.innerHTML='<img src="data:'+esc(data.mime)+';base64,'+data.data+'" alt="">';
  }
}
function modalHtml() {
  if (!state.modal) return "";
  const modal = state.modal;
  const heading = modal.kind==="settings"?"Settings":modal.kind==="preview"?"Preview":
    modal.kind==="move"?(modal.items?.length>1?"Move "+modal.items.length+" files":"Move file"):"File actions";
  let body = "";
  if (modal.kind==="settings") {
    body = '<label>Theme<select id="theme-setting">'+
      ['system','light','dark'].map(value=>'<option value="'+value+'" '+(state.theme===value?'selected':'')+'>'+value[0].toUpperCase()+value.slice(1)+'</option>').join('')+
      '</select></label><label>Ignored folder names<textarea id="ignored-names" rows="3" spellcheck="false" placeholder="One name per line">'+
      esc((modal.ignoredNames||[]).filter(name=>name.toLowerCase()!=="_shotcode").join("\n"))+
      '</textarea></label><p class="hint">_shotcode is always ignored. Add one folder name per line.</p>'+
      '<button class="primary wide" data-action="save-settings">Save settings</button>';
  } else if (modal.kind==="preview") {
    body = '<p class="menu-file">'+esc(modal.name)+'</p>'+(modal.data?.available ? (modal.data.mediaType==="video" ?
      '<video class="preview-video" controls playsinline preload="metadata" src="'+esc(modal.data.url)+'"></video>'+
      '<p class="hint">'+(modal.category?'If Chrome cannot play this format, open the file in its folder.':'Chrome may not play every video codec.')+'</p>'+
      (modal.category?'<button class="menu-action" data-action="open-folder" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("folder")+'Show file in folder</button>':'') :
      '<img class="preview-image" src="data:'+esc(modal.data.mime)+';base64,'+modal.data.data+'" alt="'+esc(modal.name)+'">') :
      '<p>Preview is unavailable for this format.</p>');
  } else if (modal.kind==="move") {
    body = '<p>'+esc(modal.items?.length>1?modal.items.length+" selected files":modal.name)+'</p><label>Project<select data-move="project">'+selectHtml(state.projects.map(p=>p.name),modal.project,"Select project")+'</select></label>'+
      (modal.layout==="sequences"?'<label>Sequence<select data-move="sequence">'+selectHtml(modal.sequences||[],modal.sequence,"Select sequence")+'</select></label>':"")+
      '<label>Shot<select data-move="shot">'+selectHtml(modal.shots||[],modal.shot,"Select shot")+'</select></label>'+
      '<button class="primary wide" data-action="confirm-move" '+(!modal.shot||state.busy?"disabled":"")+'>Move and rename</button>';
  } else {
    const file=state.files.find(item=>item.category===modal.category&&item.name===modal.name);
    const video=state.categories.some(category=>category.id===modal.category&&category.mediaType==="video");
    const conversion=file&&state.conversions.get(file.path);
    body = '<p class="menu-file">'+esc(modal.name)+'</p>'+
      '<button class="menu-action" data-action="preview" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("image")+'Preview</button>'+
      (conversion?"":'<button class="menu-action" data-action="rename" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("refresh")+'Fix name</button>')+
      (conversion?"":'<button class="menu-action" data-action="move" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("move")+'Move to another shot</button>')+
      (video&&!file?.converted&&!conversion?'<button class="menu-action" data-action="convert-png" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("image")+'Convert to PNG</button>':"")+
      (conversion?'<button class="menu-action" data-action="cancel-conversion" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("close")+'Cancel conversion</button>':"")+
      '<button class="menu-action" data-action="copy-path" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("copy")+'Copy file path</button>'+
      (file?.converted?'<button class="menu-action" data-action="copy-sequence-path" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("copy")+'Copy PNG sequence folder</button>':"") +
      (file?.converted?'<button class="menu-action" data-action="show-sequence-folder" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("folder")+'Show PNG sequence folder</button>':"") +
      '<button class="menu-action" data-action="open-folder" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("folder")+'Show file in folder</button>';
  }
  return '<div class="overlay"><div class="dialog" role="dialog" aria-modal="true" aria-label="'+heading+'"><header><h2>'+heading+'</h2><button class="icon-button" data-action="close" aria-label="Close">'+icon("close")+'</button></header><div class="dialog-body">'+body+'</div></div></div>';
}
function navigationHtml() {
  const projectItems = state.projects.map(project =>
    '<button class="picker-option '+(project.name===state.project?'selected':'')+'" data-action="select-project" data-project="'+esc(project.name)+'" role="menuitem">'+esc(project.name)+'</button>'
  ).join("");
  const sequenceItems = state.sequences.map(sequence =>
    '<button class="picker-option '+(sequence===state.sequence?'selected':'')+'" data-action="select-sequence" data-sequence="'+esc(sequence)+'" role="menuitem">'+esc(sequence)+'</button>'
  ).join("");
  const hasSequences = state.project && state.layout==="sequences";
  const projects = '<div class="picker project-picker"><button class="picker-trigger" data-action="toggle-projects" aria-label="Project: '+esc(state.project||'select')+'" aria-haspopup="menu" aria-expanded="'+(state.openMenu==="project")+'"><span class="picker-value">'+esc(state.project||'Project')+'</span>'+icon("chevron")+'</button>'+
    (state.openMenu==="project"?'<div class="picker-menu" role="menu" aria-label="Projects">'+(projectItems||'<div class="picker-empty">No projects yet</div>')+'<button class="picker-option add-option" data-action="add-project" role="menuitem">'+icon("plus")+'Add project</button></div>':"")+'</div>';
  const sequences = '<div class="picker sequence-picker"><button class="picker-trigger" data-action="toggle-sequences" aria-label="Sequence: '+esc(state.sequence||'select')+'" aria-haspopup="menu" aria-expanded="'+(state.openMenu==="sequence")+'" '+(!hasSequences?'disabled':'')+'><span class="picker-value">'+esc(hasSequences?(state.sequence||'Sequence'):(state.project?'No sequences':'Sequence'))+'</span>'+icon("chevron")+'</button>'+
    (state.openMenu==="sequence"?'<div class="picker-menu" role="menu" aria-label="Sequences">'+(sequenceItems||'<div class="picker-empty">No sequences available</div>')+'</div>':"")+'</div>';
  const filteredShots = state.shots.filter(shot=>shot.toLowerCase().includes(state.query.toLowerCase()));
  const shots = filteredShots.map(shot=>
    '<button class="shot '+(shot===state.shot?'active':'')+'" data-action="shot" data-shot="'+esc(shot)+'" aria-pressed="'+(shot===state.shot)+'" title="'+esc(shot)+'">'+esc(shot)+'</button>'
  ).join("");
  return '<section class="navigation"><div class="nav-pickers">'+projects+sequences+'</div>'+
    (state.project?'<div class="shot-tools"><span>SHOTS <b>'+state.shots.length+'</b></span><div class="search">'+icon("search")+'<input id="shot-search" type="search" placeholder="Find shot" aria-label="Find shot" value="'+esc(state.query)+'"></div></div>'+
      '<div class="shots">'+(shots||'<div class="shot-empty">'+(state.query?'No shots match this search. <button data-action="clear-shot-search">Clear search</button>':(state.layout==="sequences"&&!state.sequence?'Select a sequence to view shots.':'No shots in this location.'))+'</div>')+'</div>':"")+
    (!state.projects.length?'<div class="empty compact">Add a project from the menu above.</div>':"")+
    '</section>';
}
function render() {
  const recent = recentRows();
  document.documentElement.dataset.theme=state.theme==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":state.theme;
  root.innerHTML = '<main class="app" style="--shots-height:'+state.paneHeights.shots+'px;--files-height:'+state.paneHeights.files+'px">'+
    '<header class="app-header"><div class="brand"><img src="assets/mark.svg" alt=""><strong>AIRenamer</strong></div>'+
    '<div class="header-actions"><span class="version">'+esc(chrome.runtime.getManifest().version)+'</span><span class="host-dot '+(state.connected?"ready":"offline")+'" title="'+(state.connected?"Connected":"Disconnected")+'"></span><button class="icon-button" data-action="settings" aria-label="Settings">'+icon("settings")+'</button></div></header>'+
    (!state.connected?'<div class="connection offline"><span class="dot"></span>Local companion is not connected<button data-action="refresh" aria-label="Refresh">'+icon("refresh")+'</button></div>':"")+
    (state.error?'<div class="error" role="alert">'+icon("alert")+'<span>'+esc(state.error)+'</span><button data-action="dismiss" aria-label="Dismiss error">×</button></div>':"")+
    navigationHtml()+'<div class="pane-handle" data-resize="shots" role="separator" aria-label="Resize Shots" title="Drag to resize Shots"></div>'+
    (state.shot?'<section class="files"><div class="section-title">FILES <span>'+state.files.length+'</span>'+
    '<button class="icon-button" data-action="refresh-files" aria-label="Refresh files">'+icon("refresh")+'</button>'+
    '<button class="icon-button inline-import" data-action="import" aria-label="Add files to '+esc(state.shot)+'" title="Add files">'+icon("plus")+'</button></div>'+
    (state.selected.size?'<div class="selection"><span>'+state.selected.size+' selected</span><div class="selection-actions">'+
      '<button data-action="batch-rename" '+(state.busy?"disabled":"")+'>Fix names</button>'+
      '<button data-action="batch-move" '+(state.busy?"disabled":"")+'>Move</button>'+
      '<button data-action="clear-selection" '+(state.busy?"disabled":"")+'>Clear</button></div></div>':"")+
    '<div class="file-list">'+fileRows()+'</div></section><div class="pane-handle" data-resize="files" role="separator" aria-label="Resize Files and Recent Downloads" title="Drag to resize Files and Recent Downloads"></div>':"")+
    '<section class="recent '+(!state.shot?'fill':'')+'">'+
    '<div class="recent-heading"><strong>RECENT DOWNLOADS <span>'+recent.count+'</span></strong></div>'+
    '<div class="recent-list">'+recent.html+'</div></section>'+
    '<input type="file" id="file-picker" multiple accept=".png,.jpg,.jpeg,.tiff,.webp,.avif,.gif,.exr,.mp4,.mov,.mkv,.webm,.avi,.m4v" hidden>'+
    modalHtml()+'</main>';
  paintCachedThumbnails();
}
function fileForRow(row) {
  return state.files.find(file=>file.category===row.dataset.category&&file.name===row.dataset.name);
}
function prepareBrowserDrag(item) {
  if(!item?.dragUrl||item.size>MAX_BROWSER_DRAG_BYTES)return;
  const key=item.path+":"+item.modified;
  if(dragFileCache.has(key))return;
  const pending=fetch(item.dragUrl).then(response=>{
    if(!response.ok)throw new Error("File unavailable");
    return response.blob();
  }).then(blob=>{
    const file=new File([blob],item.name,{type:blob.type||"application/octet-stream"});
    dragFileCache.set(key,file);
    return file;
  });
  dragFileCache.set(key,pending);
  pending.catch(()=>dragFileCache.delete(key));
  if(dragFileCache.size>2)dragFileCache.delete(dragFileCache.keys().next().value);
}
async function refreshProjects({quiet=false}={}) {
  state.projectsLoading=(state.projectsLoading||0)+1;
  const token=++state.generation;
  try {
    const data=await native("projects");
    if(token!==state.generation)return;
    state.connected=true;state.error="";
    state.projects=data.projects;state.categories=data.categories;
    if(!state.projects.some(p=>p.name===state.project)){state.project="";state.sequence="";state.shot="";state.files=[];}
    render();
    if(state.project)await refreshNavigation();
  } catch(error) {
    if(token!==state.generation)return;
    state.connected=false;
    if(!quiet)status(error);
  } finally {
    state.projectsLoading--;
  }
}
async function refreshNavigation() {
  const token=++state.generation;
  try {
    const initial=await native("navigation",{project:state.project});
    if(token!==state.generation)return;
    state.layout=initial.layout;state.sequences=initial.sequences;
    if(initial.layout==="sequences") {
      if(!state.sequences.includes(state.sequence)){state.sequence="";state.shot="";}
      if(!state.sequence&&state.sequences.length===1){
        state.sequence=state.sequences[0];
        await saveDestination();
      }
      if(state.sequence){
        const details=await native("navigation",{project:state.project,sequence:state.sequence});
        if(token!==state.generation)return;
        state.shots=details.shots;
      }else state.shots=[];
    } else {
      state.sequence="";state.shots=initial.shots;
    }
    if(!state.shots.includes(state.shot)){state.shot="";state.files=[];}
    render();
    if(state.shot)await refreshFiles();
  }catch(error){if(token===state.generation)status(error);}
}
async function refreshFiles() {
  if(!state.shot)return;
  const token=++state.generation, destination=currentDestination();
  try {
    const data=await native("files",destination);
    if(token!==state.generation)return;
    state.files=data.files;
    const available=new Set(data.files.map(key));
    state.selected=new Set([...state.selected].filter(item=>available.has(item)));
    render();
  }catch(error){if(token===state.generation)status(error);}
}
async function refreshRecent() {
  try{state.recent=await message("recent");render();}catch(error){status(error);}
}
async function saveDestination() {
  const context=state.project?{project:state.project,sequence:state.sequence||""}:null;
  const writes=[];
  if(state.tabId!==null){
    writes.push(message("destination",{tabId:state.tabId,value:currentDestination()}));
    writes.push(message("context",{tabId:state.tabId,value:context}));
  }
  writes.push(chrome.storage.local.set({lastContext:context,lastDestination:currentDestination()}));
  await Promise.all(writes);
}
async function selectProject(project) {
  state.openMenu=null;state.project=project;state.layout="";
  state.sequence="";state.shot="";state.sequences=[];state.shots=[];
  state.files=[];state.query="";state.selected.clear();
  await saveDestination();render();
  if(project)await refreshNavigation();
}
async function selectSequence(sequence) {
  state.openMenu=null;state.sequence=sequence;state.shot="";
  state.shots=[];state.files=[];state.query="";state.selected.clear();
  await saveDestination();render();
  if(state.project)await refreshNavigation();
}
async function chooseShot(shot) {
  state.shot=shot;state.files=[];state.query="";state.selected.clear();
  await saveDestination();
  render();await refreshFiles();
}
function setBusy(value){state.busy=value;render();}
async function transferFile(file, destination=currentDestination()) {
  if(!destination)throw new Error("Select a shot first");
  const started=await native("transfer_begin",{...destination,name:file.name});
  let transferId=started.transferId;
  try {
    const reader=file.stream().getReader();
    for(;;){
      const {done,value}=await reader.read();if(done)break;
      for(let i=0;i<value.length;i+=64*1024){
        let binary="";
        for(const byte of value.subarray(i,i+64*1024))binary+=String.fromCharCode(byte);
        await native("transfer_chunk",{transferId,data:btoa(binary)});
      }
    }
    const result=await native("transfer_finish",{transferId});
    transferId=null;return result;
  }finally{
    if(transferId)await native("transfer_abort",{transferId}).catch(()=>{});
  }
}
async function importFiles(files, destination=currentDestination()) {
  if(state.busy)return;
  try{
    setBusy(true);
    const chosen=[...files].filter(file=>mediaExt.test(file.name));
    if(chosen.length!==files.length)throw new Error("Some files use an unsupported format");
    for(const file of chosen)await transferFile(file,destination);
    if(destination?.project===state.project&&destination?.sequence===(state.sequence||null)&&destination?.shot===state.shot)await refreshFiles();
  }catch(error){status(error);}
  finally{setBusy(false);}
}
async function openSettings() {
  const data=await native("preferences");
  state.modal={kind:"settings",ignoredNames:data.ignoredNames};render();
}
async function showPreview(category,name) {
  try {
    const data=await native("preview",{...currentDestination(),category,name});
    state.modal={kind:"preview",category,name,data};render();
  }catch(error){status(error);}
}
async function showRecentPreview(id) {
  const item=thumbnailItem(id);
  if(!item||item.state!=="complete")throw new Error("Download is unavailable");
  const data=await loadRecentThumbnail(id);
  state.modal={kind:"preview",name:item.filename.split(/[\\/]/).pop(),data};render();
}
async function pollConversion(path, jobId){
  try{
    for(;;){
      await new Promise(resolve=>setTimeout(resolve,1000));
      const result=await native("convert_status",{jobId});
      if(result.state==="queued"||result.state==="running")continue;
      state.conversions.delete(path);
      if(state.files.some(item=>item.path===path))await refreshFiles();
      else render();
      if(result.state==="error")throw new Error(result.error||"PNG conversion failed");
      return;
    }
  }catch(error){state.conversions.delete(path);status(error);}
}
async function changeMove(level,value) {
  const modal=state.modal;
  if(!modal||modal.kind!=="move")return;
  modal[level]=value;
  try{
    if(level==="project"){
      modal.sequence="";modal.shot="";modal.sequences=[];modal.shots=[];
      if(value){
        const data=await native("navigation",{project:value});
        modal.layout=data.layout;modal.sequences=data.sequences;modal.shots=data.shots;
      }
    }
    if(level==="sequence"){
      modal.shot="";modal.shots=[];
      if(value)modal.shots=(await native("navigation",{project:modal.project,sequence:value})).shots;
    }
    render();
  }catch(error){status(error);}
}
root.addEventListener("click",async event=>{
  const button=event.target.closest("[data-action]");
  if(!button){
    if(event.target.classList.contains("overlay")){state.modal=null;render();}
    else if(state.openMenu&&!event.target.closest(".nav-pickers")){
      state.openMenu=null;q(".picker-menu")?.remove();
      root.querySelectorAll(".picker-trigger").forEach(item=>item.setAttribute("aria-expanded","false"));
    }
    return;
  }
  const action=button.dataset.action;
  try {
    if(action==="dismiss"){state.error="";render();}
    else if(action==="refresh")await refreshProjects();
    else if(action==="refresh-files")await refreshFiles();
    else if(action==="toggle-projects"){state.openMenu=state.openMenu==="project"?null:"project";render();}
    else if(action==="toggle-sequences"){state.openMenu=state.openMenu==="sequence"?null:"sequence";render();}
    else if(action==="select-project")await selectProject(button.dataset.project);
    else if(action==="select-sequence")await selectSequence(button.dataset.sequence);
    else if(action==="add-project"){
      state.openMenu=null;
      const result=await native("add_project");
      if(!result.cancelled)await selectProject(result.name);
      await refreshProjects();
    }
    else if(action==="shot")await chooseShot(button.dataset.shot);
    else if(action==="clear-shot-search"){state.query="";render();q("#shot-search")?.focus();}
    else if(action==="import")q("#file-picker").click();
    else if(action==="settings")await openSettings();
    else if(action==="save-settings"){
      const names=q("#ignored-names").value.split(/[\n,]+/).map(item=>item.trim()).filter(Boolean);
      const ignored=await native("set_ignored_names",{names});
      state.theme=q("#theme-setting").value;
      await chrome.storage.local.set({panelTheme:state.theme});
      state.modal=null;render();
      if(state.project)await refreshNavigation();
    }
    else if(action==="close"){state.modal=null;render();}
    else if(action==="clear-selection"){state.selected.clear();render();}
    else if(action==="batch-rename"){
      if(state.busy)return;
      const items=selectedFiles();
      let completed=0;
      setBusy(true);
      try{
        for(const item of items){
          await native("rename_file",{...currentDestination(),category:item.category,name:item.name});
          state.selected.delete(key(item));
          completed++;
        }
        await refreshFiles();
      }catch(error){
        await refreshFiles().catch(()=>{});
        throw new Error(completed+" of "+items.length+" names fixed. "+String(error.message||error));
      }finally{setBusy(false);}
    }
    else if(action==="batch-move"){
      if(state.busy)return;
      const items=selectedFiles().map(item=>({category:item.category,name:item.name}));
      if(!items.length)throw new Error("Select files first");
      state.modal={kind:"move",items,project:state.project,layout:state.layout,
        sequence:state.sequence,shot:"",sequences:state.sequences,shots:state.shots};
      render();
    }
    else if(action==="file-menu"){state.modal={kind:"menu",category:button.dataset.category,name:button.dataset.name};render();}
    else if(action==="preview")await showPreview(button.dataset.category,button.dataset.name);
    else if(action==="convert-png"){
      const file=state.files.find(item=>item.category===button.dataset.category&&item.name===button.dataset.name);
      if(!file||file.converted)throw new Error("Video is unavailable for conversion");
      const result=await native("convert_begin",{...currentDestination(),category:file.category,name:file.name});
      state.conversions.set(file.path,{jobId:result.jobId});
      state.modal=null;render();
      pollConversion(file.path,result.jobId);
    }
    else if(action==="cancel-conversion"){
      const file=state.files.find(item=>item.category===button.dataset.category&&item.name===button.dataset.name);
      const job=file&&state.conversions.get(file.path);
      if(job)await native("convert_cancel",{jobId:job.jobId});
      state.modal=null;render();
    }
    else if(action==="load-thumbnail")await loadRecentThumbnail(button.dataset.recent);
    else if(action==="preview-recent")await showRecentPreview(button.dataset.recent);
    else if(action==="copy-path"){
      const file=state.files.find(item=>item.category===button.dataset.category&&item.name===button.dataset.name);
      if(!file?.path)throw new Error("File path is unavailable");
      await navigator.clipboard.writeText(file.path);
      state.modal=null;render();
    }
    else if(action==="copy-sequence-path"){
      const file=state.files.find(item=>item.category===button.dataset.category&&item.name===button.dataset.name);
      if(!file?.converted||!file.dragPath)throw new Error("PNG sequence folder is unavailable");
      await navigator.clipboard.writeText(file.dragPath);
      state.modal=null;render();
    }
    else if(action==="show-sequence-folder"){
      await native("open_folder",{...currentDestination(),category:button.dataset.category,
        file:button.dataset.name,convertedFolder:true});
      state.modal=null;render();
    }
    else if(action==="open-folder"){
      await native("open_folder",{...currentDestination(),category:button.dataset.category,file:button.dataset.name});
      state.modal=null;render();
    }
    else if(action==="rename"){
      await native("rename_file",{...currentDestination(),category:button.dataset.category,name:button.dataset.name});
      state.modal=null;await refreshFiles();
    }
    else if(action==="move"){
      state.modal={kind:"move",category:button.dataset.category,name:button.dataset.name,
        project:state.project,layout:state.layout,sequence:state.sequence,shot:"",
        sequences:state.sequences,shots:state.shots};
      render();
    }
    else if(action==="confirm-move"){
      if(state.busy)return;
      const m=state.modal;
      const items=m.items||[{category:m.category,name:m.name}];
      if(m.project===state.project&&(m.sequence||null)===(state.sequence||null)&&m.shot===state.shot)
        throw new Error("Choose a different shot");
      let moved=0;
      setBusy(true);
      try{
        for(const item of items){
          await native("move_file",{
            sourceProject:state.project,sourceSequence:state.sequence||null,sourceShot:state.shot,
            category:item.category,name:item.name,project:m.project,sequence:m.sequence||null,shot:m.shot,
          });
          state.selected.delete(key(item));
          moved++;
        }
        state.modal=null;
        await refreshFiles();
      }catch(error){
        state.modal=null;
        await refreshFiles().catch(()=>{});
        throw new Error(moved+" of "+items.length+" files moved. "+String(error.message||error));
      }finally{setBusy(false);}
    }
    else if(action==="assign-download"){
      const item=state.recent.downloads.find(x=>x.id===Number(button.dataset.id));
      if(!item||item.state!=="complete")throw new Error("Download is unavailable");
      await native("import_file",{...currentDestination(),source:item.filename});
      await refreshFiles();
    }
  }catch(error){status(error);}
});
root.addEventListener("change",async event=>{
  try{
    if(event.target.id==="file-picker"){
      const files=[...event.target.files];event.target.value="";await importFiles(files);
    }
    else if(event.target.dataset.select!==undefined){
      if(event.target.checked)state.selected.add(event.target.dataset.select);
      else state.selected.delete(event.target.dataset.select);
      render();
    }
    else if(event.target.dataset.move)await changeMove(event.target.dataset.move,event.target.value);
  }catch(error){status(error);}
});
root.addEventListener("input",event=>{
  if(event.target.id==="shot-search"){
    state.query=event.target.value;
    const value=event.target.value;
    render();
    const input=q("#shot-search");input.focus();input.setSelectionRange(value.length,value.length);
  }
});
root.addEventListener("dragstart",event=>{
  const row=event.target.closest(".file-row");if(!row)return;
  const item=fileForRow(row);
  if(!item)return;
  const path=item.dragPath || item.path;
  if(!path)return;
  event.dataTransfer.setData("application/x-airenamer-file",JSON.stringify({
    project:state.project,sequence:state.sequence||null,shot:state.shot,
    category:item.category,name:item.name,
  }));
  if(item.dragUrl){
    // Chromium exposes DownloadURL to Windows file drop targets. It is a virtual file,
    // so native applications that only accept CF_HDROP may still require Explorer.
    const mime={png:"image/png",jpg:"image/jpeg",jpeg:"image/jpeg",webp:"image/webp",gif:"image/gif",mp4:"video/mp4",mov:"video/quicktime",webm:"video/webm"}[item.name.split(".").pop().toLowerCase()]||"application/octet-stream";
    event.dataTransfer.setData("DownloadURL",mime+":"+item.name+":"+item.dragUrl);
  }
  const browserFile=dragFileCache.get(item.path+":"+item.modified);
  if(browserFile instanceof File)event.dataTransfer.items.add(browserFile);
  event.dataTransfer.effectAllowed="copyMove";
});
root.addEventListener("pointerenter",event=>{
  const row=event.target.closest?.(".file-row");
  if(row)prepareBrowserDrag(fileForRow(row));
},true);
root.addEventListener("pointerdown",event=>{
  const row=event.target.closest?.(".file-row");
  if(row)prepareBrowserDrag(fileForRow(row));
});
root.addEventListener("pointerdown",event=>{
  const handle=event.target.closest?.(".pane-handle");
  if(!handle)return;
  event.preventDefault();
  const pane=handle.dataset.resize;
  const element=pane==="shots"?q(".shots"):q(".files");
  if(!element)return;
  state.resizing={pane,startY:event.clientY,startHeight:element.getBoundingClientRect().height};
  handle.setPointerCapture(event.pointerId);
});
root.addEventListener("pointermove",event=>{
  if(!state.resizing)return;
  const {pane,startY,startHeight}=state.resizing;
  const next=Math.max(pane==="shots"?58:80,Math.min(window.innerHeight*0.65,startHeight+event.clientY-startY));
  state.paneHeights[pane]=Math.round(next);
  q(".app")?.style.setProperty(pane==="shots"?"--shots-height":"--files-height",next+"px");
});
root.addEventListener("pointerup",()=>{
  if(!state.resizing)return;
  state.resizing=null;
  chrome.storage.local.set({paneHeights:state.paneHeights}).catch(status);
});
root.addEventListener("dragover",event=>{
  const shot=event.target.closest(".shot");
  if(shot||event.target.closest(".files")){
    event.preventDefault();
    event.dataTransfer.dropEffect=event.dataTransfer.types.includes("application/x-airenamer-file")?"move":"copy";
    if(shot)shot.classList.add("drop-target");
  }
});
root.addEventListener("dragleave",event=>{
  event.target.closest(".shot")?.classList.remove("drop-target");
});
root.addEventListener("drop",async event=>{
  const shot=event.target.closest(".shot");
  const filesArea=event.target.closest(".files");
  if(!shot&&!filesArea)return;
  event.preventDefault();
  shot?.classList.remove("drop-target");
  try{
    const targetShot=shot?.dataset.shot||state.shot;
    if(!targetShot)throw new Error("Select a shot first");
    const destination={project:state.project,sequence:state.sequence||null,shot:targetShot};
    const internal=event.dataTransfer.getData("application/x-airenamer-file");
    if(internal&&shot){
      const source=JSON.parse(internal);
      if(source.project===destination.project&&source.sequence===destination.sequence&&source.shot===destination.shot)return;
      await native("move_file",{
        sourceProject:source.project,sourceSequence:source.sequence,sourceShot:source.shot,
        category:source.category,name:source.name,...destination,
      });
      await chooseShot(targetShot);
    }else if(event.dataTransfer.files.length){
      await importFiles(event.dataTransfer.files,destination);
      if(shot&&targetShot!==state.shot)await chooseShot(targetShot);
    }
  }catch(error){status(error);}
});
root.addEventListener("keydown",event=>{
  if(event.key==="Escape"&&state.openMenu){state.openMenu=null;render();}
});
chrome.runtime.onMessage.addListener(message=>{
  if(message.kind==="recentChanged")refreshRecent();
});
async function activateTab(tab) {
  const tabId=tab?.id??null;
  state.generation++;
  state.tabId=tabId;
  state.windowId=tab?.windowId??state.windowId;
  const [previous,tabContext,stored]=await Promise.all([
    tabId!==null?message("getDestination",{tabId}):null,
    tabId!==null?message("getContext",{tabId}):null,
    chrome.storage.local.get(["lastContext","lastDestination","panelTheme","paneHeights"]),
  ]);
  if(state.tabId!==tabId)return;
  const context=stored.lastContext||previous||tabContext||{};
  state.project=context.project||"";
  state.sequence=context.sequence||"";
  const savedShot=stored.lastDestination;
  state.shot=savedShot?.project===state.project&&
    (savedShot.sequence||"")===(state.sequence||"") ? savedShot.shot : previous?.shot||"";
  state.theme=stored.panelTheme||"system";
  if(stored.paneHeights)state.paneHeights={...state.paneHeights,...stored.paneHeights};
  state.layout="";state.sequences=[];state.shots=[];state.files=[];
  state.selected.clear();state.query="";state.openMenu=null;
  await refreshProjects();
}
chrome.tabs.onActivated.addListener(async info=>{
  if(state.windowId!==null&&info.windowId!==state.windowId)return;
  try{await activateTab(await chrome.tabs.get(info.tabId));}
  catch(error){status(error);}
});
// Setup may finish while the panel is already open; reconnect without another click.
setInterval(()=>{
  if(!state.connected&&!state.projectsLoading&&!document.hidden)refreshProjects({quiet:true});
},5000);
(async()=>{
  render();
  try{
    // Activate a newly installed extension without visiting chrome://extensions.
    const update=await message("checkUpdate").catch(()=>null);
    if(update?.reloading){root.innerHTML='<main class="panel"><div class="empty">Updating AIRenamer…</div></main>';return;}
    const tabs=await chrome.tabs.query({active:true,currentWindow:true});
    await activateTab(tabs[0]);
    await refreshRecent();
  }catch(error){status(error);}
})();
})();
