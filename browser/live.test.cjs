const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

async function dragPanel(mode,localFiles,options={}){
  const listeners={},calls=[],saved=[];
  const file={category:'keyframes',name:'frame.png',path:'D:\\Project\\vfx\\shots\\SH010\\genai\\frame.png',
    dragUrl:'http://127.0.0.1:1234/file/temporary',nativeDragToken:'validated-token',size:10,modified:123,version:null};
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},querySelector(selector){return options.fields?.[selector]||null;},querySelectorAll(selector){return selector==='[data-category-field]'?(options.categoryFields||[]):[];}};
  const chrome={runtime:{getManifest:()=>({version:'0.27.8'}),onMessage:{addListener(){}},
    async sendMessage(message){
      calls.push(message);
      if(message.kind==='checkUpdate')return {ok:true,result:{}};
      if(message.kind==='recent')return {ok:true,result:{downloads:options.downloads||[],managed:[]}};
      if(message.type==='projects'){if(options.projectGate)await options.projectGate;if(options.projectError)throw Error(options.projectError);}
      if(message.type==='projects')return {ok:true,result:{projects:[{name:'Project',path:'D:\\Project',layout:'shots'}],categories:options.categories||[{id:'keyframes',folder:'img'}],preferences:options.preferences}};
      if(message.type==='navigation')return {ok:true,result:{layout:'shots',shots:['SH010'],sequences:[]}};
      if(message.type==='files')return {ok:true,result:{files:[file]}};
      if(message.type==='drag_prepare')return {ok:true,result:{ready:true}};
      if(message.type==='drag_direct')return {ok:true,result:options.dragResult||{started:true}};
      if(message.type==='preferences')return {ok:true,result:{settings:options.settings,ignoredNames:[],preview:options.preview}};
      if(message.type==='set_preferences'){if(options.saveError)throw Error(options.saveError);return {ok:true,result:{settings:message.payload.settings,ignoredNames:message.payload.names}};}
      if(message.type==='preview_naming')return {ok:true,result:options.preview};
      if(message.type==='import_file')return {ok:true,result:{name:'imported.png'}};
      if(message.type==='transfer_begin')return {ok:true,result:{transferId:'transfer-1'}};
      if(['transfer_chunk','transfer_finish','transfer_abort'].includes(message.type))return {ok:true,result:{}};
      if(message.type==='drag_status')return {ok:true,result:options.dropResult};
      throw Error(JSON.stringify(message));
    }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({lastContext:{project:'Project'},lastDestination:{project:'Project',shot:'SH010'},desktopDragMode:mode,desktopDragModeRevision:Object.hasOwn(options,'revision')?options.revision:1}),set:async value=>saved.push(value)}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,Date,btoa,
    ...(localFiles?{AIRenamerLocalFiles:class{connected(){return true;}get(){return localFiles;}}}:{}),
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  return {listeners,calls,file,root,saved};
}
test('native mode warms automatically and cancels the Chrome drag before handing off the same gesture',async()=>{
  const panel=await dragPanel('native');
  assert.equal(panel.calls.filter(item=>item.type==='drag_prepare').length,1);
  assert.match(panel.root.innerHTML,/Drag a file into an app/);
  let cancelled=false;
  panel.listeners.dragstart({target:{closest:selector=>selector==='.file-row'?{dataset:{category:'keyframes',name:'frame.png'}}:null},
    preventDefault(){cancelled=true;},dataTransfer:{setData(){throw Error('Chrome drag must not start');}}});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(cancelled,true);
  const request=panel.calls.find(item=>item.type==='drag_direct');
  assert.equal(request.payload.fileToken,'validated-token');
  assert.equal(request.payload.shot,'SH010');assert.equal(typeof request.payload.issued,'number');
});
test('native mode preserves the move handle drag for moving between shots',async()=>{
  const panel=await dragPanel('native');let cancelled=false;const values={};
  panel.listeners.dragstart({target:{closest:selector=>selector==='.file-row'?{dataset:{category:'keyframes',name:'frame.png'}}:selector==='[data-internal-drag]'?{}:null},
    preventDefault(){cancelled=true;},dataTransfer:{setData(type,value){values[type]=value;},items:{add(){}}}});
  assert.equal(cancelled,false);assert.equal(JSON.parse(values['application/x-airenamer-file']).shot,'SH010');
  assert(!panel.calls.some(item=>item.type==='drag_direct'));
});
test('connected Chrome drag keeps the backing File and excludes delayed DownloadURL data',async()=>{
  const backingFile={name:'frame.png'};
  const panel=await dragPanel('browser',backingFile);
  const values={},files=[];
  panel.listeners.dragstart({target:{closest:()=>({dataset:{category:'keyframes',name:'frame.png'}})},
    dataTransfer:{setData(type,value){values[type]=value;},items:{add(file){files.push(file);}}}});
  assert.equal(files[0],backingFile);
  assert.equal(values.DownloadURL,undefined);
  assert.equal(JSON.parse(values['application/x-airenamer-file']).shot,'SH010');
  assert.equal(panel.calls.filter(item=>item.type==='drag_prepare').length,0);
});

test('startup and old browser-mode preference automatically prepare native drag',async()=>{
  for(const mode of [undefined,'browser']){
    const panel=await dragPanel(mode,null,{revision:undefined});
    assert(panel.calls.some(item=>item.type==='drag_prepare'));
    assert.equal(panel.saved[0].desktopDragMode,'native');
    assert.equal(panel.saved[0].desktopDragModeRevision,1);
  }
});

test('Shift row drag preserves internal shot movement in native mode',async()=>{
  const panel=await dragPanel('native');const values={};
  panel.listeners.dragstart({shiftKey:true,target:{closest:selector=>selector==='.file-row'?{dataset:{category:'keyframes',name:'frame.png'}}:null},
    preventDefault(){throw Error('Internal drag must remain available');},dataTransfer:{setData(type,value){values[type]=value;},items:{add(){}}}});
  assert.equal(JSON.parse(values['application/x-airenamer-file']).shot,'SH010');
  assert(!panel.calls.some(item=>item.type==='drag_direct'));
});

test('startup displays Loading with three dots until projects resolve',async()=>{
  let resolveProjects;const gate=new Promise(resolve=>resolveProjects=resolve);
  const panel=await dragPanel(undefined,null,{projectGate:gate});
  assert.match(panel.root.innerHTML,/role="status"><span>Loading<\/span>/);
  assert.match(panel.root.innerHTML,/<i><\/i><i><\/i><i><\/i>/);
  assert.doesNotMatch(panel.root.innerHTML,/Local companion|Connection unavailable|Choose a project folder/i);
  resolveProjects();await new Promise(resolve=>setTimeout(resolve,0));
  assert.doesNotMatch(panel.root.innerHTML,/loading-dots/);
  assert.match(panel.root.innerHTML,/SH010/);
});

test('startup failure ends Loading and offers reconnect',async()=>{
  const panel=await dragPanel(undefined,null,{projectError:'Host unavailable'});
  assert.doesNotMatch(panel.root.innerHTML,/loading-dots/);
  assert.match(panel.root.innerHTML,/Connection unavailable/);
  assert.match(panel.root.innerHTML,/Host unavailable/);
});

test('native completion reports rejected drops and ends status polling',async()=>{
  const panel=await dragPanel('native',null,{dragResult:{started:true,id:'drag-1'},dropResult:{id:'drag-1',phase:'finished',ok:true,effect:'None'}});
  panel.listeners.dragstart({target:{closest:selector=>selector==='.file-row'?{dataset:{category:'keyframes',name:'frame.png'}}:null},preventDefault(){},dataTransfer:{}});
  await new Promise(resolve=>setTimeout(resolve,250));
  assert.match(panel.root.innerHTML,/destination did not accept this file/);
  assert.equal(panel.calls.filter(item=>item.type==='drag_status').length,1);
});

test('Recent Downloads requests a preview only after one file is clicked',async()=>{
  const listeners={};
  const calls=[];
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},
    querySelector(){return null;},querySelectorAll(){return [];}};
  const chrome={
    runtime:{getManifest:()=>({version:'0.26.28'}),onMessage:{addListener(){}},
      async sendMessage(message){
        calls.push(message);
        if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
        if(message.kind==='recent')return {ok:true,result:{downloads:[{
          id:1,filename:'C:/Downloads/clip.png',url:'https://example.com/clip.png',state:'complete',
        }],managed:[]}};
        if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{projects:[],categories:[]}};
        if(message.kind==='native'&&message.type==='preview_download')return {ok:true,result:{available:true,mime:'image/jpeg',data:'AAAA'}};
        throw Error('Unexpected message '+JSON.stringify(message));
      }},
    tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({}),set:async()=>{}}},
  };
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.match(root.innerHTML,/Load preview of clip.png/);
  assert.equal(calls.filter(item=>item.type==='preview_download').length,0);
  const click=action=>listeners.click({target:{dataset:{action,recent:'d1'},closest(){return this;}}});
  await click('load-thumbnail');
  assert.equal(calls.filter(item=>item.type==='preview_download').length,1);
  await click('load-thumbnail');
  await click('preview-recent');
  assert.equal(calls.filter(item=>item.type==='preview_download').length,1);
});

test('project picker appears only when no folders are configured',async()=>{
  async function start(projects){
    const root={innerHTML:'',addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];}};
    const chrome={runtime:{getManifest:()=>({version:'0.27.0'}),onMessage:{addListener(){}},
      async sendMessage(message){
        if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
        if(message.kind==='recent')return {ok:true,result:{downloads:[],managed:[]}};
        if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{projects,categories:[]}};
        if(message.kind==='native'&&message.type==='navigation')return {ok:true,result:{layout:'shots',sequences:[],shots:[]}};
        throw Error(JSON.stringify(message));
      }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
      storage:{local:{get:async()=>({}),set:async()=>{}}}};
    vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
      document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
      window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
      setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
    });
    await new Promise(resolve=>setTimeout(resolve,0));
    return root.innerHTML;
  }
  assert.match(await start([]),/Add your first project/);
  assert.doesNotMatch(await start([{name:'Existing',path:'D:\\Work\\Existing',layout:'shots',available:true}]),
    /role="dialog"[^>]*aria-label="Add your first project"/);
});

test('a project can be added by path with no desktop configuration',async()=>{
  const listeners={};
  const calls=[];
  const projects=[];
  const folder='D:\\Jobs\\ProjectA';
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},
    querySelector(selector){return selector==='#project-folder-input'?{value:folder}:null;},
    querySelectorAll(){return [];}};
  const chrome={runtime:{getManifest:()=>({version:'0.27.1'}),onMessage:{addListener(){}},
    async sendMessage(message){
      calls.push(message);
      if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
      if(message.kind==='recent')return {ok:true,result:{downloads:[],managed:[]}};
      if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{projects,categories:[]}};
      if(message.kind==='native'&&message.type==='add_project'){
        projects.push({name:'ProjectA',path:folder,layout:'shots',available:true});
        return {ok:true,result:{name:'ProjectA',path:folder,layout:'shots'}};
      }
      if(message.kind==='native'&&message.type==='navigation')return {ok:true,result:{layout:'shots',sequences:[],shots:['SH010']}};
      throw Error(JSON.stringify(message));
    }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({}),set:async()=>{}}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.match(root.innerHTML,/Add your first project/);
  await listeners.click({target:{dataset:{action:'save-project-path'},closest(){return this;}}});
  assert.equal(calls.find(item=>item.type==='add_project').payload.path,folder);
  assert.match(root.innerHTML,/SH010/);
  assert.doesNotMatch(root.innerHTML,/aria-label="Add your first project"/);
});

test('Browse fills the first project path before it is saved',async()=>{
  const listeners={};
  const calls=[];
  const folder='D:\\Jobs\\SelectedProject';
  const projects=[];
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},
    querySelector(selector){
      if(selector!=='#project-folder-input')return null;
      return {value:root.innerHTML.match(/id="project-folder-input"[^>]*value="([^"]*)"/)?.[1]||''};
    },querySelectorAll(){return [];}};
  const chrome={runtime:{getManifest:()=>({version:'0.27.3'}),onMessage:{addListener(){}},
    async sendMessage(message){
      calls.push(message);
      if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
      if(message.kind==='recent')return {ok:true,result:{downloads:[],managed:[]}};
      if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{projects,categories:[]}};
      if(message.kind==='native'&&message.type==='choose_project_folder')return {ok:true,result:{path:folder}};
      if(message.kind==='native'&&message.type==='add_project'){
        projects.push({name:'SelectedProject',path:folder,layout:'shots',available:true});
        return {ok:true,result:{name:'SelectedProject',path:folder,layout:'shots'}};
      }
      if(message.kind==='native'&&message.type==='navigation')return {ok:true,result:{layout:'shots',sequences:[],shots:[]}};
      throw Error(JSON.stringify(message));
    }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({}),set:async()=>{}}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  const click=action=>listeners.click({target:{dataset:{action},closest(){return this;}}});
  assert.match(root.innerHTML,/data-action="browse-project-folder"/);
  await click('browse-project-folder');
  assert.equal(calls.find(item=>item.type==='choose_project_folder').payload.initial,'');
  assert.match(root.innerHTML,/value="D:\\Jobs\\SelectedProject"/);
  assert.equal(calls.filter(item=>item.type==='add_project').length,0);
  await click('save-project-path');
  assert.equal(calls.find(item=>item.type==='add_project').payload.path,folder);
});

test('adding a project still completes if its dialog closes while the companion saves',async()=>{
  const listeners={};
  const projects=[];
  const folder='D:\\Jobs\\ProjectA';
  let finishSave;
  const saveStarted=new Promise(resolve=>{finishSave=resolve;});
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},
    querySelector(selector){return selector==='#project-folder-input'?{value:folder}:null;},
    querySelectorAll(){return [];}};
  const chrome={runtime:{getManifest:()=>({version:'0.27.4'}),onMessage:{addListener(){}},
    async sendMessage(message){
      if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
      if(message.kind==='recent')return {ok:true,result:{downloads:[],managed:[]}};
      if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{projects,categories:[]}};
      if(message.kind==='native'&&message.type==='add_project'){
        await saveStarted;
        projects.push({name:'ProjectA',path:folder,layout:'shots',available:true});
        return {ok:true,result:{name:'ProjectA',path:folder,layout:'shots'}};
      }
      if(message.kind==='native'&&message.type==='navigation')return {ok:true,result:{layout:'shots',sequences:[],shots:['SH010']}};
      throw Error(JSON.stringify(message));
    }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({}),set:async()=>{}}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  const click=action=>listeners.click({target:{dataset:{action},closest(){return this;}}});
  const saving=click('save-project-path');
  assert.match(root.innerHTML,/Adding project…/);
  await click('close');
  finishSave();
  await saving;
  assert.match(root.innerHTML,/SH010/);
  assert.doesNotMatch(root.innerHTML,/Cannot read properties of null/);
});

test('project registration failure is visible inside the still-open dialog',async()=>{
  const listeners={};
  const folder='X:\\UnavailableProject';
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},
    querySelector(selector){return selector==='#project-folder-input'?{value:folder}:null;},
    querySelectorAll(){return [];}};
  const chrome={runtime:{getManifest:()=>({version:'0.27.4'}),onMessage:{addListener(){}},
    async sendMessage(message){
      if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
      if(message.kind==='recent')return {ok:true,result:{downloads:[],managed:[]}};
      if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{projects:[],categories:[]}};
      if(message.kind==='native'&&message.type==='add_project')return {ok:false,error:'Project folder unavailable'};
      throw Error(JSON.stringify(message));
    }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({}),set:async()=>{}}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  await listeners.click({target:{dataset:{action:'save-project-path'},closest(){return this;}}});
  assert.match(root.innerHTML,/role="dialog"[^>]*aria-label="Add your first project"/);
  assert.match(root.innerHTML,/class="dialog-error" role="alert">Project folder unavailable/);
  assert.match(root.innerHTML,/value="X:\\UnavailableProject"/);
});

test('Settings can preview and save a project shots tree for multiple sequences',async()=>{
  const listeners={};
  const calls=[];
  const project={name:'Hammer_232851',path:'X:\\Hammer_232851',layout:'sequences',available:true};
  const fields={'#structure-scene':'vfx\\shots','#structure-shot':'',
    '#structure-target':'','#structure-image':'genai','#structure-video':'VIDEO',
    '#structure-layout':'sequences'};
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},
    querySelector(selector){return selector in fields?{value:fields[selector]}:null;},
    querySelectorAll(){return [];}};
  const preview={projectRoot:project.path,sequenceRoot:project.path+'\\vfx\\shots',
    exists:true,sequences:['45','46'],sequenceCount:2,
    examples:[{sequence:'45',shotsRoot:project.path+'\\vfx\\shots\\45',shots:['ham0010']}]};
  const chrome={runtime:{getManifest:()=>({version:'0.27.5'}),onMessage:{addListener(){}},
    async sendMessage(message){
      calls.push(message);
      if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
      if(message.kind==='recent')return {ok:true,result:{downloads:[],managed:[]}};
      if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{projects:[project],categories:[]}};
      if(message.kind==='native'&&message.type==='preferences')return {ok:true,result:{ignoredNames:[]}};
      if(message.kind==='native'&&message.type==='get_structure')return {ok:true,result:{
        projectRoot:project.path,custom:false,preview,
        defaults:{scenePrefix:'vfx\\shots',shotPrefix:'',targetPrefix:'',imageFolder:'genai',videoFolder:'VIDEO',defaultLayout:'auto'},
        values:{scenePrefix:'vfx\\shots',shotPrefix:'',targetPrefix:'',imageFolder:'genai',videoFolder:'VIDEO',layout:'sequences'}}};
      if(message.kind==='native'&&message.type==='preview_structure')return {ok:true,result:preview};
      if(message.kind==='native'&&message.type==='set_project_structure')return {ok:true,result:{values:message.payload,preview}};
      throw Error(JSON.stringify(message));
    }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({}),set:async()=>{}}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  const click=(action,extra={})=>listeners.click({target:{dataset:{action,...extra},closest(){return this;}}});
  await click('settings');
  assert.match(root.innerHTML,/data-action="edit-project-structure"/);
  assert.match(root.innerHTML,/data-action="edit-default-structure"/);
  await click('edit-project-structure',{project:project.name});
  assert.match(root.innerHTML,/X:\\Hammer_232851\\vfx\\shots/);
  assert.match(root.innerHTML,/45.*ham0010/);
  await click('check-structure');
  await click('save-structure');
  const saved=calls.find(message=>message.type==='set_project_structure');
  assert.equal(saved.payload.project,project.name);
  assert.equal(saved.payload.scenePrefix,'vfx\\shots');
  assert.equal(saved.payload.layout,'sequences');
});

test('Settings suggests the real project root when shots was added as a project',async()=>{
  const listeners={};
  const projects=[{name:'shots',path:'X:\\Hammer_232851\\vfx\\shots',layout:'sequences',available:true}];
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},
    querySelector(selector){return selector==='#project-folder-input'?{
      value:root.innerHTML.match(/id="project-folder-input"[^>]*value="([^"]*)"/)?.[1]||''}:null;},
    querySelectorAll(){return [];}};
  const chrome={runtime:{getManifest:()=>({version:'0.27.5'}),onMessage:{addListener(){}},
    async sendMessage(message){
      if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
      if(message.kind==='recent')return {ok:true,result:{downloads:[],managed:[]}};
      if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{projects,categories:[]}};
      if(message.kind==='native'&&message.type==='preferences')return {ok:true,result:{ignoredNames:[]}};
      if(message.kind==='native'&&message.type==='change_project_path'){
        assert.equal(message.payload.project,'shots');
        assert.equal(message.payload.path,'X:\\Hammer_232851');
        projects[0]={name:'Hammer_232851',path:message.payload.path,layout:'sequences',available:true};
        return {ok:true,result:projects[0]};
      }
      if(message.kind==='native'&&message.type==='navigation')return {ok:true,result:{layout:'sequences',sequences:['45'],shots:[]}};
      throw Error(JSON.stringify(message));
    }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({lastContext:{project:'shots'}}),set:async()=>{}}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  const click=(action,extra={})=>listeners.click({target:{dataset:{action,...extra},closest(){return this;}}});
  await click('settings');
  assert.match(root.innerHTML,/Fix project root/);
  await click('repair-project-root',{project:'shots',root:'X:\\Hammer_232851'});
  assert.match(root.innerHTML,/value="X:\\Hammer_232851"/);
  await click('save-project-path');
  assert.match(root.innerHTML,/Hammer_232851/);
  assert.match(root.innerHTML,/aria-label="Project: Hammer_232851"/);
  assert.doesNotMatch(root.innerHTML,/data-action="repair-project-root"/);
});

test('desktop drag action sends the selected file to the local companion',async()=>{
  const listeners={};
  const calls=[];
  const file={name:'frame.png',category:'keyframes',path:'D:\\Project\\SH010\\frame.png',
    dragPath:'D:\\Project\\SH010\\frame.png',size:100,modified:1};
  const root={innerHTML:'',addEventListener(name,fn){listeners[name]=fn;},
    querySelector(){return null;},querySelectorAll(){return [];}};
  const chrome={runtime:{getManifest:()=>({version:'0.27.1'}),onMessage:{addListener(){}},
    async sendMessage(message){
      calls.push(message);
      if(message.kind==='checkUpdate')return {ok:true,result:{reloading:false}};
      if(message.kind==='recent')return {ok:true,result:{downloads:[],managed:[]}};
      if(message.kind==='native'&&message.type==='projects')return {ok:true,result:{
        projects:[{name:'Project',path:'D:\\Project',layout:'shots',available:true}],
        categories:[{id:'keyframes',folder:'genai',mediaType:'image'}]}};
      if(message.kind==='native'&&message.type==='navigation')return {ok:true,result:{layout:'shots',sequences:[],shots:['SH010']}};
      if(message.kind==='native'&&message.type==='files')return {ok:true,result:{files:[file]}};
      if(message.type==='drag_prepare')return {ok:true,result:{ready:true}};
      if(message.kind==='native'&&message.type==='drag_to_app')return {ok:true,result:{opened:true}};
      throw Error(JSON.stringify(message));
    }},tabs:{query:async()=>[],onActivated:{addListener(){}}},
    storage:{local:{get:async()=>({lastContext:{project:'Project'}}),set:async()=>{}}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/live.js','utf8'),{
    document:{getElementById:()=>root,documentElement:{dataset:{}},hidden:false},
    window:{matchMedia:()=>({matches:false})},chrome,URL,Set,Map,Promise,
    setInterval:()=>0,setTimeout,clearTimeout,queueMicrotask,
  });
  await new Promise(resolve=>setTimeout(resolve,0));
  const click=dataset=>listeners.click({target:{dataset,closest(){return this;}}});
  await click({action:'shot',shot:'SH010'});
  await click({action:'file-menu',category:'keyframes',name:'frame.png'});
  assert.match(root.innerHTML,/Drag into desktop app/);
  await click({action:'drag-to-app',category:'keyframes',name:'frame.png'});
  const request=calls.find(item=>item.type==='drag_to_app');
  assert.equal(request.payload.project,'Project');
  assert.equal(request.payload.shot,'SH010');
  assert.equal(request.payload.name,'frame.png');
});


const namingPreferences={filename_template:'{shot}-{type}-{version}-r{subversion}{format}',image_type_suffix:'STILL',video_type_suffix:'CLIP',
  subversion_enabled:'Yes',image_extensions:['.png','.heic'],video_extensions:['.mov'],additional_categories:[]};
const settingsFields=()=>({'#setting-filename_template':{value:namingPreferences.filename_template},'#setting-image_type_suffix':{value:'STILL'},'#setting-video_type_suffix':{value:'CLIP'},
  '#setting-subversion_enabled':{checked:true},'#setting-image_extensions':{value:'.png, .heic'},'#setting-video_extensions':{value:'.mov'},
  '#ignored-names':{value:'ignore-me'},'#theme-setting':{value:'dark'},'#desktop-drag-setting':{value:'native'}});
const panelClick=(panel,action,extra={})=>panel.listeners.click({target:{dataset:{action,...extra},closest(){return this;}}});

test('Browser Settings saves naming and ignores together and uses configured import extensions',async()=>{
  const panel=await dragPanel('native',null,{settings:namingPreferences,fields:settingsFields()});
  await panelClick(panel,'settings');
  assert.match(panel.root.innerHTML,/Filename template/);assert.match(panel.root.innerHTML,/Enable subversions/);
  await panelClick(panel,'save-settings');
  const request=panel.calls.find(item=>item.type==='set_preferences');
  assert.equal(request.payload.settings.filename_template,namingPreferences.filename_template);
  assert.equal(request.payload.settings.subversion_enabled,'Yes');
  assert.equal(request.payload.names[0],'ignore-me');
  assert.match(panel.root.innerHTML,/accept=".png,.heic,.mov"/);
  assert(!panel.calls.some(item=>item.type==='set_ignored_names'));
});

test('invalid naming keeps Settings open and preserves the edited template',async()=>{
  const fields=settingsFields();fields['#setting-filename_template'].value='{wrong}_{version}';
  const panel=await dragPanel('native',null,{settings:namingPreferences,fields,saveError:'Unknown placeholder: wrong'});
  await panelClick(panel,'settings');await panelClick(panel,'save-settings');
  assert.match(panel.root.innerHTML,/aria-label="Settings"/);
  assert.match(panel.root.innerHTML,/Unknown placeholder: wrong/);
  assert.match(panel.root.innerHTML,/value="{wrong}_{version}"/);
  assert.doesNotMatch(panel.root.innerHTML,/Saving…/);
});

test('live naming preview uses draft settings without saving',async()=>{
  const fields=settingsFields(),preview={imageMain:'SH010-STILL-001-r00.png',videoMain:'SH010-CLIP-001-r00.mov',imageSubversion:'SH010-STILL-001-r01.png'};
  fields['#naming-preview']={innerHTML:''};
  const panel=await dragPanel('native',null,{settings:namingPreferences,fields,preview});
  await panelClick(panel,'settings');
  panel.listeners.input({target:{dataset:{preference:'filename_template'}}});
  await new Promise(resolve=>setTimeout(resolve,300));
  assert.match(fields['#naming-preview'].innerHTML,/SH010-STILL-001-r01.png/);
  assert.equal(panel.calls.find(item=>item.type==='preview_naming').payload.settings.filename_template,namingPreferences.filename_template);
  assert(!panel.calls.some(item=>item.type==='set_preferences'));
});

test('Create subversion applies to Recent Downloads and is hidden when disabled globally',async()=>{
  const downloads=[{id:5,filename:'C:/Downloads/image.png',state:'complete'}];
  const panel=await dragPanel('native',null,{downloads});
  await panel.listeners.change({target:{id:'create-subversion',checked:true,dataset:{}}});
  await panelClick(panel,'assign-download',{id:'5'});
  assert.equal(panel.calls.find(item=>item.type==='import_file').payload.subversion,true);
  const disabled=await dragPanel('native',null,{preferences:{subversionsEnabled:false},downloads});
  assert.doesNotMatch(disabled.root.innerHTML,/id="create-subversion"/);
  await panelClick(disabled,'assign-download',{id:'5'});
  assert.equal(disabled.calls.find(item=>item.type==='import_file').payload.subversion,false);
});

test('dropping onto a saved file forwards its version target through the transfer',async()=>{
  const panel=await dragPanel('native');
  const row={dataset:{category:'keyframes',name:'frame.png'}};
  const file={name:'new.png',stream(){return {getReader(){let read=false;return {read:async()=>read?{done:true}:(read=true,{done:false,value:new Uint8Array([1,2,3])})};}};}};
  await panel.listeners.drop({shiftKey:false,preventDefault(){},target:{closest:selector=>selector==='.file-row'?row:selector==='.files'?{}:null},dataTransfer:{getData:()=>'',files:[file]}});
  const request=panel.calls.find(item=>item.type==='transfer_begin');
  assert.equal(request.payload.subversion,true);assert.equal(request.payload.targetExisting,'frame.png');
  assert.equal(request.payload.targetCategory,'keyframes');
});
