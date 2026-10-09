/* Small, shared panel state helpers. No media bytes or filesystem operations. */
(function(scope){
  'use strict';
  const finite=(value,fallback)=>Number.isFinite(Number(value))?Number(value):fallback;
  function clampPanes(panes,height,overhead=150,active=null,hasFiles=true){
    // Reserve the download heading and at least one visible download row.
    const budget=Math.max(0,finite(height,800)-finite(overhead,150)-66);
    const scale=Math.min(1,budget/(hasFiles?134:42));
    const minimum={shots:Math.floor(42*scale),files:hasFiles?Math.floor(92*scale):0};
    const result={shots:Math.max(minimum.shots,finite(panes.shots,126)),
      files:hasFiles?Math.max(minimum.files,finite(panes.files,240)):0};
    if(active==='shots'||active==='files'){
      const other=active==='shots'?'files':'shots';
      result[active]=Math.min(result[active],budget-minimum[other]);
      result[other]=Math.min(result[other],budget-result[active]);
    }else if(result.shots+result.files>budget){
      const extra=Math.max(1,result.shots+result.files-minimum.shots-minimum.files);
      const room=Math.max(0,budget-minimum.shots-minimum.files);
      result.shots=minimum.shots+(result.shots-minimum.shots)*room/extra;
      result.files=minimum.files+(result.files-minimum.files)*room/extra;
    }
    return {shots:Math.max(0,Math.floor(result.shots)),files:Math.max(0,Math.floor(result.files))};
  }
  function recordRecent(list,destination,limit=12){
    if(!destination?.project||!destination?.shot)return list||[];
    const same=item=>item.project===destination.project&&(item.sequence||'')===(destination.sequence||'')&&item.shot===destination.shot;
    return [{project:destination.project,sequence:destination.sequence||null,shot:destination.shot},
      ...(list||[]).filter(item=>item?.project&&item?.shot&&!same(item))].slice(0,limit);
  }
  const thumbnailKey=item=>JSON.stringify([item.path||item.filename,item.size??item.fileSize,item.modified??item.startTime]);
  const model={clampPanes,recordRecent,thumbnailKey};
  if(typeof module!=='undefined'&&module.exports)module.exports=model;
  else scope.AIRenamerPanelModel=model;
})(typeof globalThis==='undefined'?this:globalThis);
