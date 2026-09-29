const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

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
