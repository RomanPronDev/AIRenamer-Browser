const test=require('node:test');
const assert=require('node:assert/strict');
const {clampPanes,recordRecent,thumbnailKey}=require('./panel-model.js');
test('pane budget reserves downloads at large, small and very short viewport sizes',()=>{
  for(const height of [250,320,480,600,900,1400])for(const active of [null,'shots','files']){
    const result=clampPanes({shots:9999,files:9999},height,150,active);
    assert(result.shots>=0&&result.files>=0);
    assert(result.shots+result.files+150+66<=Math.max(height,216));
  }
});
test('dragging one pane to the limit retains room for the other pane',()=>{
  for(const active of ['shots','files']){
    const result=clampPanes({shots:5000,files:5000},600,160,active);
    assert(result.shots>=42);assert(result.files>=92);
    assert.equal(result.shots+result.files,600-160-66);
  }
});
test('invalid persisted pane heights cannot break layout and no-shot mode reserves no Files pane',()=>{
  const result=clampPanes({shots:NaN,files:Infinity},600);
  assert(Number.isFinite(result.shots)&&Number.isFinite(result.files));
  assert.equal(clampPanes({shots:9999,files:9999},600,150,null,false).files,0);
});
test('recent shots deduplicate full destinations and retain identically named shots elsewhere',()=>{
  let list=[];
  for(const item of [{project:'A',sequence:'30',shot:'SH010'},{project:'A',sequence:'45',shot:'SH010'},
    {project:'B',sequence:null,shot:'SH010'},{project:'A',sequence:'30',shot:'SH010'}])list=recordRecent(list,item);
  assert.equal(list.length,3);assert.equal(list[0].sequence,'30');assert.equal(list[1].project,'B');
  for(let i=0;i<20;i++)list=recordRecent(list,{project:'A',shot:'SH'+i});
  assert.equal(list.length,12);assert.equal(list[0].shot,'SH19');
});
test('thumbnail keys change after a file is replaced, even if its filename is unchanged',()=>{
  assert.notEqual(thumbnailKey({path:'a',size:100,modified:1}),thumbnailKey({path:'a',size:100,modified:2}));
});
