(() => {
"use strict";
const root = document.getElementById("root");
const state = {
  connected: false, loading: true, projects: [], categories: [], project: "", layout: "",
  sequences: [], sequence: "", shots: [], shot: "", files: [],
  recent: {downloads: [], managed: []}, tabId: null, windowId: null, query: "",
  selected: new Set(), modal: null, openMenu: null, busy: false, error: "",
  generation: 0, theme: "system", paneHeights: {shots:126,files:240}, resizing: null,
  subversionsEnabled:true,createSubversion:false,addFormat:false,importDestination:null,
  mediaExtensions:['.png','.jpg','.jpeg','.tif','.tiff','.webp','.avif','.gif','.exr','.mp4','.mov','.mkv','.webm','.avi','.m4v'],
  conversions: new Map(), setupDismissed: false, desktopDragMode: "native", nativeDragReady: false,
};
let mediaExt = /\.(png|jpe?g|tiff?|webp|avif|gif|exr|mp4|mov|mkv|webm|avi|m4v)$/i;
const thumbnailCache = new Map();
const localFiles = typeof AIRenamerLocalFiles === "function" ? new AIRenamerLocalFiles() : null;
const activeProject = () => state.projects.find(project => project.name === state.project);
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
  const message = String(error?.message || error || "");
  if (["settings","setup","project-path","structure","connect-files"].includes(state.modal?.kind)) {
    state.modal.error = message;
    state.error = "";
  } else state.error = message;
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
  if (!state.files.length&&!state.categories.some(cat=>!['keyframes','video'].includes(cat.id))) return '<div class="empty">No media files in this shot yet.</div>';
  return state.categories.map(category => {
    const items = state.files.filter(item => item.category === category.id);
    if (!items.length&&['keyframes','video'].includes(category.id)) return "";
    return '<section class="category" data-drop-category="'+esc(category.id)+'"><div class="section-title">'+esc(category.folder)+'<span>'+items.length+'</span>'+
      '<button class="icon-button" data-action="import" data-category="'+esc(category.id)+'" aria-label="Add files to '+esc(category.folder)+'">'+icon('plus')+'</button></div>'+
      items.map(item => '<div class="file-row" data-category="'+esc(item.category)+'" data-name="'+esc(item.name)+'" draggable="true">'+
      '<label class="file-check"><input type="checkbox" data-select="'+esc(key(item))+'" '+(state.selected.has(key(item))?"checked":"")+' aria-label="Select '+esc(item.name)+'"></label>'+
      '<div class="file-icon">'+icon(category.mediaType==="video"?"film":"image")+'</div>'+
      '<div class="file-data"><button class="file-name" data-action="preview" data-category="'+esc(item.category)+'" data-name="'+esc(item.name)+'">'+esc(item.name)+'</button>'+
      '<div class="file-sub">'+(item.version!==null?'v'+String(item.version).padStart(3,"0"):"No version")+
      (item.subversion ? ' · '+String(item.subversion).padStart(2,"0") : "")+
      (item.hasPsd?" · PSD":"")+(item.converted?" · PNG sequence":"")+
      (state.conversions.has(item.path)?" · Converting to PNG…":"")+'</div></div>'+
      (state.desktopDragMode==='native'?
      '<button class="icon-button shot-drag-handle" draggable="true" data-internal-drag="true" aria-label="Drag '+esc(item.name)+' to another shot" title="Drag to another shot">'+icon("move")+'</button>':
      '<button class="icon-button" data-action="drag-to-app" data-category="'+esc(item.category)+'" data-name="'+esc(item.name)+'" aria-label="Drag '+esc(item.name)+' into a desktop app" title="Drag into desktop app">'+icon("external")+'</button>')+
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
function structurePreviewHtml(preview) {
  if(!preview)return '<p class="hint">Use Check paths to preview the folders AIRenamer will read.</p>';
  const folder=preview.shotsRoot||preview.sequenceRoot;
  const items=preview.shots||preview.sequences||[];
  const count=preview.shotCount??preview.sequenceCount??0;
  return '<div class="structure-preview"><strong>'+(preview.shotsRoot?'Shots folder':'Sequences folder')+'</strong>'+
    '<code>'+esc(folder)+'</code><small>'+(preview.exists?count+' found':'Folder unavailable')+
    (items.length?' · '+esc(items.slice(0,5).join(', ')):'')+'</small>'+
    (preview.examples?.length?preview.examples.map(item=>'<small>'+esc(item.sequence)+' → '+esc(item.shotsRoot)+
      (item.shots.length?' · '+esc(item.shots.join(', ')):' · no shots found')+'</small>').join(''):'')+'</div>';
}
const namingDefaults={filename_template:'{sequence}_{shot}_{type}_v{version}_{subversion}{format}',
  image_type_suffix:'IMG',video_type_suffix:'VID',subversion_enabled:'Yes',
  image_extensions:['.png','.jpg','.jpeg','.tiff','.webp','.avif','.gif','.exr'],
  video_extensions:['.mp4','.mov','.mkv','.webm','.avi','.m4v'],additional_categories:[]};
function structuredNamingDefaults(){return JSON.parse(JSON.stringify(namingDefaults));}
function namingSettingsHtml(modal){
  const settings=modal.settings||namingDefaults;
  const field=(name,label)=>'<label>'+label+'<input type="text" id="setting-'+name+'" data-preference="'+name+'" spellcheck="false" value="'+esc(settings[name])+'"></label>';
  return '<details class="settings-group" open><summary>Names & versions</summary>'+
    field('filename_template','Filename template')+
    '<p class="hint">Tokens: {sequence}, {scene}, {shot}, {type}, {version}, {subversion}, {format}. File extensions are added automatically.</p>'+
    '<div class="setting-pair">'+field('image_type_suffix','Image type suffix')+field('video_type_suffix','Video type suffix')+'</div>'+
    '<label class="setting-row">Enable subversions<input id="setting-subversion_enabled" data-preference="subversion_enabled" type="checkbox" '+(settings.subversion_enabled!=='No'?'checked':'')+'></label>'+
    '<p class="hint">Versions use 001, 002…; subversions use 00, 01…. Drop onto a saved file to extend that version.</p>'+
    '<div id="naming-preview" class="naming-preview" aria-live="polite">'+namingPreviewHtml(modal.preview)+'</div></details>'+
    '<details class="settings-group"><summary>File types & categories</summary>'+
    ['image','video'].map(kind=>'<label>'+kind[0].toUpperCase()+kind.slice(1)+' extensions<input type="text" id="setting-'+kind+'_extensions" data-preference="'+kind+'_extensions" spellcheck="false" value="'+esc(settings[kind+'_extensions'].join(', '))+'"></label>').join('')+
    '<p class="hint">Separate extensions with commas. Additional categories share the project’s media root.</p>'+
    (settings.additional_categories||[]).map((cat,index)=>'<div class="category-setting">'+
      ['id','folder','type_suffix'].map(name=>'<label>'+({id:'Category ID',folder:'Folder',type_suffix:'Type suffix'}[name])+
        '<input type="text" data-category-index="'+index+'" data-category-field="'+name+'" value="'+esc(cat[name])+'" spellcheck="false"></label>').join('')+
      '<label>Media type<select data-category-index="'+index+'" data-category-field="media_type">'+
        ['image','video'].map(kind=>'<option value="'+kind+'" '+(kind===cat.media_type?'selected':'')+'>'+kind+'</option>').join('')+'</select></label>'+
      '<button class="small-action" data-action="remove-category" data-index="'+index+'">Remove category</button></div>').join('')+
    '<button class="small-action" data-action="add-category">'+icon('plus')+' Add category</button></details>';
}
function namingPreviewHtml(preview){
  if(!preview)return '<small>Edit a naming field to see an example.</small>';
  return '<small>Main version</small><code>'+esc(preview.imageMain)+'</code><code>'+esc(preview.videoMain)+'</code>'+
    (state.modal?.settings?.subversion_enabled!=='No'?'<small>Subversion</small><code>'+esc(preview.imageSubversion)+'</code>':'');
}
function namingForm(){
  const settings={...(state.modal?.settings||namingDefaults)};
  for(const name of ['filename_template','image_type_suffix','video_type_suffix','subversion_enabled','image_extensions','video_extensions']){
    const input=q('#setting-'+name);if(!input)continue;
    settings[name]=name==='subversion_enabled'?(input.checked?'Yes':'No'):
      name.endsWith('_extensions')?input.value.split(/[\s,;]+/).filter(Boolean):input.value.trim();
  }
  settings.additional_categories=(settings.additional_categories||[]).map(cat=>({...cat}));
  for(const input of root.querySelectorAll('[data-category-field]'))
    settings.additional_categories[Number(input.dataset.categoryIndex)][input.dataset.categoryField]=input.value.trim();
  return settings;
}
function applyPreferences(data){
  const settings=data.settings;
  state.subversionsEnabled=settings?settings.subversion_enabled!=='No':data.subversionsEnabled??state.subversionsEnabled;
  if(!state.subversionsEnabled)state.createSubversion=false;
  const extensions=settings?[...settings.image_extensions,...settings.video_extensions]:
    data.imageExtensions&&data.videoExtensions?[...data.imageExtensions,...data.videoExtensions]:null;
  if(extensions)state.mediaExtensions=extensions;
  if(extensions)mediaExt=new RegExp('(?:'+extensions.map(ext=>ext.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|')+')$','i');
}
function importOptions(shift=false){return {subversion:state.subversionsEnabled&&(state.createSubversion||shift),addFormat:state.addFormat};}
let namingPreviewTimer=null;
let namingPreviewGeneration=0;
function scheduleNamingPreview(){
  clearTimeout(namingPreviewTimer);
  const modal=state.modal;if(modal?.kind!=='settings')return;
  modal.settings=namingForm();const generation=++namingPreviewGeneration;
  namingPreviewTimer=setTimeout(async()=>{
    try{
      const preview=await native('preview_naming',{settings:modal.settings,sequence:state.sequence||'',shot:state.shot||'SH010'});
      if(state.modal!==modal||generation!==namingPreviewGeneration)return;
      modal.preview=preview;const node=q('#naming-preview');if(node)node.innerHTML=namingPreviewHtml(preview);
    }catch(error){
      if(state.modal!==modal||generation!==namingPreviewGeneration)return;
      const node=q('#naming-preview');if(node)node.innerHTML='<small class="field-error">'+esc(error.message||error)+'</small>';
    }
  },250);
}
function modalHtml() {
  if (!state.modal) return "";
  const modal = state.modal;
  const heading = modal.kind==="settings"?"Settings":modal.kind==="setup"?"Add your first project":
    modal.kind==="project-path"?(modal.project?"Change project folder":"Add project folder"):
    modal.kind==="connect-files"?"Connect folder for drag":
    modal.kind==="structure"?(modal.project?"Project structure":"Default structure"):
    modal.kind==="remove-project"?"Remove project shortcut":modal.kind==="preview"?"Preview":
    modal.kind==="move"?(modal.items?.length>1?"Move "+modal.items.length+" files":"Move file"):"File actions";
  let body = "";
  const projectPathField = '<label>Project folder path<div class="project-path-row">'+
    '<input id="project-folder-input" type="text" spellcheck="false" autocomplete="off" placeholder="D:\\Projects\\MyProject" value="'+esc(modal.path||"")+'">'+
    '<button class="small-action" data-action="browse-project-folder" type="button" '+(modal.browsing||modal.busy?'disabled':'')+'>'+(modal.browsing?'Opening…':'Browse…')+'</button></div></label>';
  if (modal.kind==="settings") {
    body = '<div class="settings-section"><div class="section-title">PROJECT FOLDERS</div>'+
      (state.projects.length?state.projects.map(project=>{
        const nested=project.path.match(/[\\/]vfx[\\/]shots[\\/]?$/i);
        const suggestedRoot=nested?project.path.slice(0,-nested[0].length):"";
        return (
        '<div class="project-setting"><div class="project-setting-info"><strong>'+esc(project.name)+'</strong><span title="'+esc(project.path)+'">'+esc(project.path)+'</span>'+
        '<small>'+esc(project.layout==="sequences"?'Sequences → Shots':'Shots directly')+(project.available?'':' · Folder unavailable')+'</small></div>'+
        (suggestedRoot?'<small class="structure-warning">This is a shots folder. Select the project root: '+esc(suggestedRoot)+'</small>':'')+
        '<div class="project-setting-actions"><button data-action="edit-project-structure" data-project="'+esc(project.name)+'">Structure</button>'+
        (suggestedRoot?'<button data-action="repair-project-root" data-project="'+esc(project.name)+'" data-root="'+esc(suggestedRoot)+'">Fix project root</button>':'')+
        '<button data-action="connect-files" data-project="'+esc(project.name)+'">Connect for drag</button>'+
        '<button data-action="change-project-path" data-project="'+esc(project.name)+'">Change folder</button>'+
        '<button data-action="ask-remove-project" data-project="'+esc(project.name)+'" aria-label="Remove '+esc(project.name)+' shortcut">Remove</button></div></div>');
      }).join(''):
        '<p class="hint">Paste a project root folder path copied from Explorer.</p>')+
      '<button class="small-action" data-action="new-project-path">'+icon("plus")+' Add project folder</button></div>'+
      '<div class="settings-section"><div class="section-title">FOLDER STRUCTURE</div>'+
      '<p class="hint">Default shots tree: Project / vfx / shots / [Sequence] / Shot</p>'+
      '<p class="hint">Browser settings are independent. Default media folders: Shot / genai / KEYFRAMES and Shot / genai / VIDEO.</p>'+
      '<button class="small-action" data-action="edit-default-structure">Edit default folders</button></div>'+
      namingSettingsHtml(modal)+
      '<label>Desktop file drag<select id="desktop-drag-setting">'+
      '<option value="native" '+((modal.dragMode||state.desktopDragMode)==='native'?'selected':'')+'>Native drag (default)</option>'+
      '<option value="browser" '+((modal.dragMode||state.desktopDragMode)==='browser'?'selected':'')+'>Chrome folder connection</option></select></label>'+
      '<p class="hint">Drag a file row directly into an app. Native drag starts automatically and needs no folder connection. Use the move handle beside a file to drag it to another shot. The file menu retains the fallback drag window.</p>'+
      '<button class="small-action" data-action="drag-probe">Open drag test target</button>'+
      '<label>Theme<select id="theme-setting">'+
      ['system','light','dark'].map(value=>'<option value="'+value+'" '+(state.theme===value?'selected':'')+'>'+value[0].toUpperCase()+value.slice(1)+'</option>').join('')+
      '</select></label><label>Ignored folder names<textarea id="ignored-names" rows="3" spellcheck="false" placeholder="One name per line">'+
      esc((modal.ignoredNames||[]).filter(name=>name.toLowerCase()!=="_shotcode").join("\n"))+
      '</textarea></label><p class="hint">_shotcode is always ignored. Add one folder name per line.</p>'+
      '<button class="primary wide" data-action="save-settings" '+(modal.busy?'disabled':'')+'>'+(modal.busy?'Saving…':'Save settings')+'</button>';
  } else if (modal.kind==="connect-files") {
    const project=state.projects.find(item=>item.name===modal.project);
    body='<p>Experimental direct file drag from this Chrome panel.</p>'+
      '<div class="folder-connect" data-connect-folder="'+esc(modal.project)+'"><strong>Drop the project folder here</strong><code>'+esc(project?.path)+'</code></div>'+
      '<p class="hint">Drag the root folder from Explorer. Files stay on disk; only the file under your pointer is prepared. Reconnect after closing this panel.</p>'+
      (localFiles?.connected(project)?'<p class="hint">Connected for this panel session.</p>':'')+
      '<button class="small-action" data-action="open-project-folder" data-project="'+esc(modal.project)+'">Show project folder in Explorer</button>';
  } else if (modal.kind==="setup") {
    body = '<p>Choose a project root folder, or paste its path from Explorer.</p>'+
      projectPathField+
      '<button class="primary wide" data-action="save-project-path" '+(modal.busy?'disabled':'')+'>'+(modal.busy?'Adding project…':'Add project')+'</button>'+
      '<p class="hint">You can add more project folders later in Settings.</p>';
  } else if (modal.kind==="project-path") {
    body = '<p>'+esc(modal.project?"Choose a new root folder for "+modal.project+", or paste its path.":"Choose a project root folder, or paste its path from Explorer.")+'</p>'+
      projectPathField+
      '<p class="hint">The folder must already exist. AIRenamer will detect its sequences and shots.</p>'+
      '<button class="primary wide" data-action="save-project-path" '+(modal.busy?'disabled':'')+'>'+(modal.busy?'Saving…':modal.project?'Save folder path':'Add project')+'</button>';
  } else if (modal.kind==="structure") {
    const values=modal.values;
    const layoutKey=modal.project?'layout':'defaultLayout';
    body='<p>'+(modal.project?'Set this project’s shots tree and layout.':'Defaults for projects without a custom structure.')+'</p>'+
      (modal.project?'<p class="structure-root">Project root<br><code>'+esc(modal.projectRoot)+'</code></p>':'')+
      '<label>Layout<select id="structure-layout">'+
      (modal.project?'':'<option value="auto" '+(values.defaultLayout==='auto'?'selected':'')+'>Detect automatically</option>')+
      '<option value="sequences" '+(values[layoutKey]==='sequences'?'selected':'')+'>Sequences → Shots</option>'+
      '<option value="shots" '+(values[layoutKey]==='shots'?'selected':'')+'>Shots directly</option></select></label>'+
      '<label>Shots tree folder inside project<input id="structure-scene" type="text" spellcheck="false" placeholder="vfx\\shots" value="'+esc(values.scenePrefix)+'"></label>'+
      '<label>Extra folder inside each sequence (optional)<input id="structure-shot" type="text" spellcheck="false" placeholder="Leave empty" value="'+esc(values.shotPrefix)+'"></label>'+
      '<label>Media folder inside each shot (optional)<input id="structure-target" type="text" spellcheck="false" placeholder="Leave empty" value="'+esc(values.targetPrefix)+'"></label>'+
      '<label>Images folder<input id="structure-image" type="text" spellcheck="false" placeholder="KEYFRAMES" value="'+esc(values.imageFolder)+'"></label>'+
      '<label>Videos folder<input id="structure-video" type="text" spellcheck="false" placeholder="VIDEO" value="'+esc(values.videoFolder)+'"></label>'+
      '<button class="small-action" data-action="standard-media-folders">Use genai / KEYFRAMES and VIDEO</button>'+
      '<p class="hint">With sequences: Project / vfx / shots / 45 / ham0010. Direct shots: Project / vfx / shots / neo0010. Paths must be relative to their parent folder.</p>'+
      (modal.project?structurePreviewHtml(modal.preview)+'<button class="small-action" data-action="check-structure" '+(modal.busy?'disabled':'')+'>Check paths</button>':'')+
      '<button class="primary wide" data-action="save-structure" '+(modal.busy?'disabled':'')+'>'+(modal.busy?'Saving…':'Save structure')+'</button>'+
      (modal.project&&modal.custom?'<button class="small-action reset-structure" data-action="reset-project-structure" '+(modal.busy?'disabled':'')+'>Use default structure</button>':'');
  } else if (modal.kind==="remove-project") {
    body = '<p>Remove <strong>'+esc(modal.project)+'</strong> from AIRenamer? Its folder and files will stay on disk.</p>'+
      '<button class="primary wide" data-action="confirm-remove-project">Remove shortcut</button>';
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
      '<button class="menu-action" data-action="drag-to-app" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("external")+'Drag into desktop app</button>'+
      '<button class="menu-action" data-action="copy-path" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("copy")+'Copy file path</button>'+
      (file?.converted?'<button class="menu-action" data-action="copy-sequence-path" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("copy")+'Copy PNG sequence folder</button>':"") +
      (file?.converted?'<button class="menu-action" data-action="show-sequence-folder" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("folder")+'Show PNG sequence folder</button>':"") +
      '<button class="menu-action" data-action="open-folder" data-category="'+esc(modal.category)+'" data-name="'+esc(modal.name)+'">'+icon("folder")+'Show file in folder</button>';
  }
  return '<div class="overlay"><div class="dialog" role="dialog" aria-modal="true" aria-label="'+heading+'"><header><h2>'+heading+'</h2><button class="icon-button" data-action="close" aria-label="Close">'+icon("close")+'</button></header><div class="dialog-body">'+
    (modal.error?'<div class="dialog-error" role="alert">'+esc(modal.error)+'</div>':'')+body+'</div></div></div>';
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
    (state.openMenu==="project"?'<div class="picker-menu" role="menu" aria-label="Projects">'+(projectItems||'<div class="picker-empty">No projects yet</div>')+'<button class="picker-option add-option" data-action="settings" role="menuitem">'+icon("settings")+'Manage project folders</button></div>':"")+'</div>';
  const sequences = '<div class="picker sequence-picker"><button class="picker-trigger" data-action="toggle-sequences" aria-label="Sequence: '+esc(state.sequence||'select')+'" aria-haspopup="menu" aria-expanded="'+(state.openMenu==="sequence")+'" '+(!hasSequences?'disabled':'')+'><span class="picker-value">'+esc(hasSequences?(state.sequence||'Sequence'):(state.project?'No sequences':'Sequence'))+'</span>'+icon("chevron")+'</button>'+
    (state.openMenu==="sequence"?'<div class="picker-menu" role="menu" aria-label="Sequences">'+(sequenceItems||'<div class="picker-empty">No sequences available</div>')+'</div>':"")+'</div>';
  const filteredShots = state.shots.filter(shot=>shot.toLowerCase().includes(state.query.toLowerCase()));
  const shots = filteredShots.map(shot=>
    '<button class="shot '+(shot===state.shot?'active':'')+'" data-action="shot" data-shot="'+esc(shot)+'" aria-pressed="'+(shot===state.shot)+'" title="'+esc(shot)+'">'+esc(shot)+'</button>'
  ).join("");
  return '<section class="navigation"><div class="nav-pickers">'+projects+sequences+'</div>'+
    (state.project?'<div class="shot-tools"><span>SHOTS <b>'+state.shots.length+'</b></span><div class="search">'+icon("search")+'<input id="shot-search" type="search" placeholder="Find shot" aria-label="Find shot" value="'+esc(state.query)+'"></div></div>'+
      '<div class="shots">'+(shots||'<div class="shot-empty">'+(state.query?'No shots match this search. <button data-action="clear-shot-search">Clear search</button>':(state.layout==="sequences"&&!state.sequence?'Select a sequence to view shots.':'No shots in this location.'))+'</div>')+'</div>':"")+
    (!state.projects.length&&!state.loading?'<div class="empty compact">Choose a project folder in Settings.</div>':"")+
    '</section>';
}
function render() {
  const recent = recentRows();
  document.documentElement.dataset.theme=state.theme==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":state.theme;
  root.innerHTML = '<main class="app" style="--shots-height:'+state.paneHeights.shots+'px;--files-height:'+state.paneHeights.files+'px">'+
    '<header class="app-header"><div class="brand"><img src="assets/mark.svg" alt=""><strong>AIRenamer</strong></div>'+
    '<div class="header-actions"><span class="version">'+esc(chrome.runtime.getManifest().version)+'</span><span class="host-dot '+(state.connected?"ready":"offline")+'" title="'+(state.loading?"Loading":state.connected?"Connected":"Disconnected")+'"></span><button class="icon-button" data-action="settings" aria-label="Settings">'+icon("settings")+'</button></div></header>'+
    (state.loading?'<div class="connection loading" role="status"><span>Loading</span><span class="loading-dots" aria-hidden="true"><i></i><i></i><i></i></span></div>':
    !state.connected?'<div class="connection offline"><span class="dot"></span>Connection unavailable<button data-action="refresh" aria-label="Refresh">'+icon("refresh")+'</button></div>':"")+
    (state.error?'<div class="error" role="alert">'+icon("alert")+'<span>'+esc(state.error)+'</span><button data-action="dismiss" aria-label="Dismiss error">×</button></div>':"")+
    navigationHtml()+'<div class="pane-handle" data-resize="shots" role="separator" aria-label="Resize Shots" title="Drag to resize Shots"></div>'+
    (state.shot?'<section class="files"><div class="section-title">FILES <span>'+state.files.length+'</span>'+
    '<button class="icon-button" data-action="refresh-files" aria-label="Refresh files">'+icon("refresh")+'</button>'+
    '<button class="icon-button inline-import" data-action="import" aria-label="Add files to '+esc(state.shot)+'" title="Add files">'+icon("plus")+'</button></div>'+
    '<button class="drag-connection" data-action="'+(state.desktopDragMode==='native'?'settings':'connect-files')+'" data-project="'+esc(state.project)+'">'+
    (state.desktopDragMode==='native'?(state.nativeDragReady?'Drag a file into an app · Move handle for shots':'Preparing native drag…'):
    localFiles?.connected(activeProject())?'Folder connected · hover a file, then drag':'Connect folder for direct drag · Experimental')+'</button>'+
    (state.selected.size?'<div class="selection"><span>'+state.selected.size+' selected</span><div class="selection-actions">'+
      '<button data-action="batch-rename" '+(state.busy?"disabled":"")+'>Fix names</button>'+
      '<button data-action="batch-move" '+(state.busy?"disabled":"")+'>Move</button>'+
      '<button data-action="clear-selection" '+(state.busy?"disabled":"")+'>Clear</button></div></div>':"")+
    '<div class="import-options">'+(state.subversionsEnabled?'<label><input id="create-subversion" type="checkbox" '+(state.createSubversion?'checked':'')+'> Create subversion</label>':'')+
    '<label><input id="add-format" type="checkbox" '+(state.addFormat?'checked':'')+'> Add aspect ratio</label></div>'+
    '<div class="file-list">'+fileRows()+'</div></section><div class="pane-handle" data-resize="files" role="separator" aria-label="Resize Files and Recent Downloads" title="Drag to resize Files and Recent Downloads"></div>':"")+
    '<section class="recent '+(!state.shot?'fill':'')+'">'+
    '<div class="recent-heading"><strong>RECENT DOWNLOADS <span>'+recent.count+'</span></strong></div>'+
    '<div class="recent-list">'+recent.html+'</div></section>'+
    '<input type="file" id="file-picker" multiple accept="'+esc(state.mediaExtensions.join(','))+'" hidden>'+
    modalHtml()+'</main>';
  paintCachedThumbnails();
}
function fileForRow(row) {
  return state.files.find(file=>file.category===row.dataset.category&&file.name===row.dataset.name);
}
function prepareBrowserDrag(item) {
  if(state.desktopDragMode==='native'){
    if(!state.nativeDragReady)warmNativeDrag();
  }
  const project=activeProject();
  if(!project||!localFiles||!item)return;
  localFiles.prepare(project,item).then(file=>{
    if(!file)return;
    root.querySelectorAll('.file-row').forEach(row=>{
      if(row.dataset.category===item.category&&row.dataset.name===item.name){
        row.dataset.localDrag='ready';row.title='Local file ready to drag';
      }
    });
  }).catch(error=>{
    root.querySelectorAll('.file-row').forEach(row=>{
      if(row.dataset.category===item.category&&row.dataset.name===item.name)row.title=String(error.message||error);
    });
  });
}
let warmingNativeDrag = null;
let latestNativeDragId = null;
function watchNativeDrop(id) {
  if(!id)return;
  latestNativeDragId=id;
  const started=Date.now();
  const poll=async()=>{
    if(latestNativeDragId!==id||Date.now()-started>120000)return;
    try{
      const result=await native('drag_status',{});
      if(latestNativeDragId!==id)return;
      if(result.id===id&&result.phase==='finished'){
        latestNativeDragId=null;
        if(!result.ok)status(result.error||'File drag failed. Please try again.');
        else if(result.effect==='None')status('The drop was cancelled or the destination did not accept this file. Drop onto the app’s file import area.');
        return;
      }
      setTimeout(poll,400);
    }catch(error){latestNativeDragId=null;state.nativeDragReady=false;status(error);}
  };
  setTimeout(poll,200);
}
function warmNativeDrag() {
  if(warmingNativeDrag)return warmingNativeDrag;
  warmingNativeDrag=native('drag_prepare',{}).then(()=>{
    state.nativeDragReady=true;
    const button=q('.drag-connection');
    if(button&&state.desktopDragMode==='native')button.textContent='Drag a file into an app · Move handle for shots';
  }).catch(error=>{state.nativeDragReady=false;status(error);})
    .finally(()=>{warmingNativeDrag=null;});
  return warmingNativeDrag;
}
async function refreshProjects({quiet=false,throwOnError=false}={}) {
  state.projectsLoading=(state.projectsLoading||0)+1;
  if(!state.connected){state.loading=true;render();}
  const token=++state.generation;
  try {
    const data=await native("projects");
    if(token!==state.generation)return;
    state.connected=true;state.loading=false;state.error="";
    state.projects=data.projects;state.categories=data.categories;
    if(data.preferences)applyPreferences(data.preferences);
    if(!state.projects.some(p=>p.name===state.project)){state.project="";state.sequence="";state.shot="";state.files=[];}
    if(!state.projects.length&&!state.modal&&!state.setupDismissed)state.modal={kind:"setup"};
    render();
    if(state.project)await refreshNavigation();
  } catch(error) {
    if(token!==state.generation)return;
    state.connected=false;state.loading=false;
    if(!quiet&&!throwOnError)status(error);
    else render();
    if(throwOnError)throw error;
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
    if(initial.categories)state.categories=initial.categories;
    const project=state.projects.find(item=>item.name===state.project);
    if(project)project.layout=initial.layout;
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
async function transferFile(file, destination={...currentDestination(),...importOptions()}) {
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
    destination={...destination,...importOptions(),...destination};
    const chosen=[...files].filter(file=>mediaExt.test(file.name));
    if(chosen.length!==files.length)throw new Error("Some files use an unsupported format");
    for(const file of chosen)await transferFile(file,destination);
    if(destination?.project===state.project&&destination?.sequence===(state.sequence||null)&&destination?.shot===state.shot)await refreshFiles();
  }catch(error){status(error);}
  finally{setBusy(false);}
}
async function openSettings() {
  state.openMenu=null;
  const data=await native("preferences");
  applyPreferences(data);
  state.modal={kind:"settings",ignoredNames:data.ignoredNames,settings:data.settings||structuredNamingDefaults(),preview:data.preview};render();
}
function structureForm() {
  return {scenePrefix:q('#structure-scene').value.trim(),
    shotPrefix:q('#structure-shot').value.trim(),
    targetPrefix:q('#structure-target').value.trim(),
    imageFolder:q('#structure-image').value.trim(),
    videoFolder:q('#structure-video').value.trim(),
    layout:q('#structure-layout').value};
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
    if(event.target.classList.contains("overlay")){
      if(state.modal?.kind==="setup")state.setupDismissed=true;
      state.modal=null;render();
    }
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
    else if(action==="new-project-path"){
      state.modal={kind:"project-path",path:""};render();
    }
    else if(action==="change-project-path"){
      const project=state.projects.find(item=>item.name===button.dataset.project);
      if(!project)throw new Error("Project is unavailable");
      state.modal={kind:"project-path",project:project.name,path:project.path};render();
    }
    else if(action==="repair-project-root"){
      state.modal={kind:"project-path",project:button.dataset.project,path:button.dataset.root};render();
    }
    else if(action==="edit-default-structure"){
      const data=await native("get_structure",{});
      state.modal={kind:"structure",values:data.defaults};render();
    }
    else if(action==="edit-project-structure"){
      const project=button.dataset.project;
      const data=await native("get_structure",{project});
      state.modal={kind:"structure",project,projectRoot:data.projectRoot,
        values:data.values,preview:data.preview,custom:data.custom};render();
    }
    else if(action==="check-structure"){
      const modal=state.modal;if(!modal?.project||modal.busy)return;
      const values=structureForm();modal.values={...values};modal.error="";modal.busy=true;render();
      try{modal.preview=await native("preview_structure",{project:modal.project,...values});}
      finally{if(state.modal===modal){modal.busy=false;render();}}
    }
    else if(action==="save-structure"){
      const modal=state.modal;if(!modal||modal.busy)return;
      const values=structureForm();modal.values=modal.project?{...values}:{...values,defaultLayout:values.layout};
      modal.error="";modal.busy=true;render();
      try{
        if(modal.project)await native("set_project_structure",{project:modal.project,...values});
        else await native("set_default_structure",{...values,defaultLayout:values.layout});
        state.modal=null;
        await refreshProjects({throwOnError:true});
        await openSettings();
      }finally{if(state.modal===modal){modal.busy=false;render();}}
    }
    else if(action==="standard-media-folders"){
      if(state.modal?.kind!=='structure'||state.modal.busy)return;
      state.modal.values={...structureForm(),targetPrefix:'genai',imageFolder:'KEYFRAMES',videoFolder:'VIDEO'};
      state.modal.preview=null;state.modal.error='';render();
    }
    else if(action==="reset-project-structure"){
      const modal=state.modal;if(!modal?.project||modal.busy)return;
      modal.busy=true;render();
      try{
        await native("reset_project_structure",{project:modal.project});
        state.modal=null;await refreshProjects({throwOnError:true});await openSettings();
      }finally{if(state.modal===modal){modal.busy=false;render();}}
    }
    else if(action==="browse-project-folder"){
      const modal=state.modal;
      if(!modal||!(modal.kind==="setup"||modal.kind==="project-path"))return;
      if(modal.busy)return;
      modal.path=q("#project-folder-input")?.value||modal.path||"";
      modal.error="";
      modal.browsing=true;render();
      try{
        const selected=await native("choose_project_folder",{initial:modal.path});
        if(state.modal===modal&&selected.path)modal.path=selected.path;
      }finally{
        if(state.modal===modal){modal.browsing=false;render();}
      }
    }
    else if(action==="save-project-path"){
      const modal=state.modal;
      if(!modal||modal.busy)return;
      const folder=q("#project-folder-input")?.value.trim().replace(/^"(.*)"$/, "$1").trim();
      if(!folder)throw new Error("Enter a project folder path");
      modal.path=folder;modal.error="";modal.busy=true;render();
      try{
        const previous=modal.project;
        const wasSelected=state.project===previous;
        const result=previous?
          await native("change_project_path",{project:previous,path:folder}):
          await native("add_project",{path:folder});
        await refreshProjects({throwOnError:true});
        if(!state.projects.some(project=>project.name===result.name&&project.path===result.path))
          throw new Error("The project was saved but could not be found. Try Refresh.");
        state.modal=null;
        if(!previous||wasSelected)await selectProject(result.name);
        if(modal.kind==="project-path")await openSettings();
      }finally{
        if(state.modal===modal){modal.busy=false;render();}
      }
    }
    else if(action==="ask-remove-project"){
      state.modal={kind:"remove-project",project:button.dataset.project};render();
    }
    else if(action==="confirm-remove-project"){
      const project=state.modal.project;
      await native("remove_project",{project});
      if(state.project===project){state.project="";state.sequence="";state.shot="";await saveDestination();}
      state.modal=null;
      if(!state.projects.some(item=>item.name!==project))state.setupDismissed=true;
      await refreshProjects();
      await openSettings();
    }
    else if(action==="shot")await chooseShot(button.dataset.shot);
    else if(action==="clear-shot-search"){state.query="";render();q("#shot-search")?.focus();}
    else if(action==="import"){
      state.importDestination={...currentDestination(),...importOptions(),...(button.dataset.category?{category:button.dataset.category}:{})};
      q("#file-picker").click();
    }
    else if(action==="connect-files"){
      state.modal={kind:"connect-files",project:button.dataset.project||state.project};render();
    }
    else if(action==="drag-probe"){
      await native('drag_probe',{});state.modal=null;render();
    }
    else if(action==="open-project-folder"){
      await native("open_folder",{project:button.dataset.project});
    }
    else if(action==="settings")await openSettings();
    else if(action==='add-category'||action==='remove-category'){
      const modal=state.modal;if(modal?.kind!=='settings')return;
      modal.settings=namingForm();
      modal.theme=q('#theme-setting')?.value||state.theme;modal.dragMode=q('#desktop-drag-setting')?.value||state.desktopDragMode;
      modal.ignoredNames=q('#ignored-names')?.value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean)||modal.ignoredNames;
      if(action==='add-category')modal.settings.additional_categories.push({id:'',folder:'',media_type:'image',type_suffix:''});
      else modal.settings.additional_categories.splice(Number(button.dataset.index),1);
      render();
    }
    else if(action==="save-settings"){
      const modal=state.modal;if(modal?.busy)return;
      const settings=namingForm();modal.settings=settings;modal.error='';
      const names=q("#ignored-names").value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
      const theme=q('#theme-setting').value;
      const dragMode=q('#desktop-drag-setting')?.value||state.desktopDragMode;
      modal.ignoredNames=names;modal.theme=theme;modal.dragMode=dragMode;modal.busy=true;render();
      try{
        const data=await native('set_preferences',{settings,names});applyPreferences(data);
        const previousDragMode=state.desktopDragMode;state.desktopDragMode=dragMode;state.theme=theme;
        await chrome.storage.local.set({panelTheme:theme,desktopDragMode:dragMode,desktopDragModeRevision:1});
        state.modal=null;render();
        if(dragMode==='native')await warmNativeDrag();
        else if(previousDragMode==='native'){state.nativeDragReady=false;await native('drag_stop',{});}
        await refreshProjects();
      }finally{modal.busy=false;}
    }
    else if(action==="close"){
      if(state.modal?.kind==="setup")state.setupDismissed=true;
      if(state.modal?.kind==="remove-project"){await openSettings();return;}
      state.modal=null;render();
    }
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
    else if(action==="drag-to-app"){
      await native("drag_to_app",{...currentDestination(),category:button.dataset.category,name:button.dataset.name});
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
      await native("import_file",{...currentDestination(),source:item.filename,...importOptions()});
      await refreshFiles();
    }
  }catch(error){status(error);}
});
root.addEventListener("change",async event=>{
  try{
    if(event.target.id==='create-subversion'){state.createSubversion=event.target.checked;}
    else if(event.target.id==='add-format'){state.addFormat=event.target.checked;}
    else if(event.target.dataset.preference||event.target.dataset.categoryField){scheduleNamingPreview();}
    else if(event.target.id==="file-picker"){
      const files=[...event.target.files];event.target.value="";const destination=state.importDestination||currentDestination();state.importDestination=null;await importFiles(files,destination);
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
  if(event.target.dataset.preference||event.target.dataset.categoryField){scheduleNamingPreview();return;}
  if(event.target.id==="project-folder-input"&&state.modal){
    state.modal.path=event.target.value;
    state.modal.error="";
    return;
  }
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
  if(state.desktopDragMode==='native'&&!event.target.closest('[data-internal-drag]')&&!event.shiftKey){
    // Cancel Chromium's drag loop before the companion starts its STA/OLE loop.
    // The companion also checks expiry and the physical left button state.
    event.preventDefault();
    if(!state.nativeDragReady){warmNativeDrag();status('Native drag is preparing. Hover the file, then try again.');return;}
    native('drag_direct',{...currentDestination(),category:item.category,name:item.name,
      fileToken:item.nativeDragToken,issued:Date.now()}).then(result=>watchNativeDrop(result.id)).catch(error=>{
        state.nativeDragReady=false;status(error);
    });
    return;
  }
  event.dataTransfer.setData("application/x-airenamer-file",JSON.stringify({
    project:state.project,sequence:state.sequence||null,shot:state.shot,
    category:item.category,name:item.name,
  }));
  const browserFile=localFiles&&activeProject()?localFiles.get(activeProject(),item):null;
  if(browserFile){
    // Preserve the snapshot File object itself. DownloadURL alongside it would
    // add a delayed virtual download and can interfere with native drop targets.
    event.dataTransfer.items.add(browserFile);
  }else if(item.dragUrl){
    // Chromium exposes DownloadURL to Windows file drop targets. It is a virtual file,
    // so native applications that only accept CF_HDROP may still require Explorer.
    const mime={png:"image/png",jpg:"image/jpeg",jpeg:"image/jpeg",webp:"image/webp",gif:"image/gif",mp4:"video/mp4",mov:"video/quicktime",webm:"video/webm"}[item.name.split(".").pop().toLowerCase()]||"application/octet-stream";
    event.dataTransfer.setData("DownloadURL",mime+":"+item.name+":"+item.dragUrl);
  }
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
  if(event.target.closest('[data-connect-folder]')){
    event.preventDefault();event.dataTransfer.dropEffect='copy';return;
  }
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
  const connection=event.target.closest('[data-connect-folder]');
  if(connection){
    event.preventDefault();
    // Read entries while the browser's drop data store is still available.
    const entries=[...event.dataTransfer.items].map(item=>item.webkitGetAsEntry?.()).filter(Boolean);
    try{
      const project=state.projects.find(item=>item.name===connection.dataset.connectFolder);
      if(!project||!localFiles)throw new Error('Folder connection is unavailable. Reload the extension.');
      if(entries.length!==1)throw new Error('Drop one project root folder.');
      localFiles.connect(project,entries[0]);state.modal=null;state.error='';render();
    }catch(error){status(error);}
    return;
  }
  const shot=event.target.closest(".shot");
  const filesArea=event.target.closest(".files");
  if(!shot&&!filesArea)return;
  event.preventDefault();
  shot?.classList.remove("drop-target");
  try{
    const targetShot=shot?.dataset.shot||state.shot;
    if(!targetShot)throw new Error("Select a shot first");
    const row=event.target.closest('.file-row');
    const targetFile=row?fileForRow(row):null;
    const destination={project:state.project,sequence:state.sequence||null,shot:targetShot,...importOptions(event.shiftKey),
      ...(event.target.closest('[data-drop-category]')?{category:event.target.closest('[data-drop-category]').dataset.dropCategory}:{}),
      ...(targetFile&&state.subversionsEnabled?{subversion:true,targetExisting:targetFile.name,targetCategory:targetFile.category,category:targetFile.category}: {})};
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
    chrome.storage.local.get(["lastContext","lastDestination","panelTheme","paneHeights","desktopDragMode","desktopDragModeRevision"]),
  ]);
  if(state.tabId!==tabId)return;
  const context=stored.lastContext||previous||tabContext||{};
  state.project=context.project||"";
  state.sequence=context.sequence||"";
  const savedShot=stored.lastDestination;
  state.shot=savedShot?.project===state.project&&
    (savedShot.sequence||"")===(state.sequence||"") ? savedShot.shot : previous?.shot||"";
  state.theme=stored.panelTheme||"system";
  state.desktopDragMode=stored.desktopDragModeRevision===1&&stored.desktopDragMode==='browser'?'browser':'native';
  if(stored.desktopDragModeRevision!==1)
    await chrome.storage.local.set({desktopDragMode:state.desktopDragMode,desktopDragModeRevision:1});
  if(state.desktopDragMode==='native')warmNativeDrag();
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
  }catch(error){state.loading=false;status(error);}
})();
})();
