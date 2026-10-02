const test=require('node:test');
const assert=require('node:assert/strict');
const {LocalFiles,relativePath}=require('./local-files.js');
const project={name:'Example',path:'X:\\Example'};
const item={path:'X:\\Example\\vfx\\shots\\45\\ham0010\\genai\\clip.mov',name:'clip.mov',size:9000000000,modified:123};
function folder(file,lookup){return {isDirectory:true,name:'Example',getFile(path,options,success){lookup?.(path,options);success({file:resolve=>resolve(file)});}};}
test('folder snapshot is reused intact without reading or copying a large video',async()=>{
  const file={name:item.name,size:item.size,lastModified:123000};
  const files=new LocalFiles();let calls=0;
  files.connect(project,folder(file,(path,options)=>{
    calls++;assert.equal(path,'vfx/shots/45/ham0010/genai/clip.mov');assert.equal(options.create,false);
  }));
  assert.equal(await files.prepare(project,item),file);
  assert.equal(files.get(project,item),file);
  await files.prepare(project,item);assert.equal(calls,1);
  files.disconnect(project.name);assert.equal(files.get(project,item),null);
});
test('a folder grant cannot read outside its registered root',()=>{
  assert.throws(()=>relativePath(project.path,'X:\\Example2\\clip.mov'));
  assert.throws(()=>relativePath(project.path,'X:\\Example\\..\\Other\\clip.mov'));
  assert.throws(()=>relativePath(project.path,'X:\\Example\\clip.mov:stream'));
  assert.equal(relativePath(project.path,'x:\\EXAMPLE\\clip.mov'),'clip.mov');
});
test('folder names and changed file metadata require a correct reconnection',async()=>{
  const files=new LocalFiles();assert.equal(files.connected(null),false);
  assert.throws(()=>files.connect(project,{isDirectory:true,name:'Other'}));
  files.connect(project,folder({name:item.name,size:1,lastModified:123000}));
  await assert.rejects(files.prepare(project,item),/different file/);
  assert.equal(files.get(project,item),null);
  assert.equal(files.connected({...project,path:'Y:\\Example'}),false);
});
test('disconnect during snapshot creation does not grant access to the old folder',async()=>{
  const files=new LocalFiles();let finish;
  files.connect(project,{isDirectory:true,name:'Example',getFile(path,options,success){success({file:resolve=>{finish=resolve;}});}});
  const pending=files.prepare(project,item);files.disconnect(project.name);
  finish({name:item.name,size:item.size,lastModified:123000});
  assert.equal(await pending,null);assert.equal(files.get(project,item),null);
});
