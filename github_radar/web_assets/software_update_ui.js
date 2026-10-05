"use strict";
window.RadarSoftwareUpdate={create({byId,api,post,tr,reveal=()=>{},remember}) {
  let state={},poll=null,expiry=null,stopped=false,generation=0;
  const seen=new Set();
  function dismiss(){if(expiry!==null)clearTimeout(expiry);expiry=null;byId('software-update-toast').hidden=true;}
  function showNotice(text){
    dismiss();byId('software-update-toast-text').textContent=text;
    byId('software-update-toast').hidden=false;reveal(byId('software-update-toast'));
    expiry=setTimeout(dismiss,10000);
  }
  function notifyFailure(failure){
    if(stopped||!failure?.attempted_at||!failure.reason)return;
    const key='startrail-data-failure:'+failure.attempted_at;
    let remembered=false;try{remembered=Boolean(remember?.getItem(key));}catch{}
    if(seen.has(key)||remembered)return;
    seen.add(key);try{remember?.setItem(key,'1');}catch{}
    showNotice(tr('数据更新未完成')+'：'+tr(failure.reason));
  }
  function render(next){
    state=next||{};const release=state.release;
    const pending=['downloading','ready'].includes(state.status);
    byId('settings-software-version').textContent=state.current||'';
    byId('settings-software-status').textContent=state.message?tr(state.message):'';
    byId('settings-software-check').disabled=pending||state.status==='checking';
    byId('software-update-button').hidden=!release;
    byId('software-update-button').textContent=tr(pending?'软件更新中…':'软件更新');
    byId('software-update-version').textContent=release?`${state.current} → ${release.tag}`:state.current||'';
    byId('software-update-notes').textContent=release?.notes||'';
    byId('software-update-message').textContent=state.message?tr(state.message):'';
    const total=state.total||0;
    byId('software-update-progress').textContent=total?`${Math.min(100,Math.floor((state.downloaded||0)/total*100))}%`:'';
    byId('software-update-install').disabled=!release||!state.installable||pending;
    byId('software-release-link').hidden=!release;
    if(release)byId('software-release-link').href=release.url;
    const key=release&&'startrail-update-notified:'+release.tag;
    let remembered=false;try{remembered=Boolean(key&&remember?.getItem(key));}catch{}
    if(release&&state.status==='available'&&!seen.has(release.tag)&&!remembered){
      seen.add(release.tag);try{remember?.setItem(key,'1');}catch{}
      showNotice(tr('发现新软件版本')+' '+release.tag+'。'+tr('可从左侧“软件更新”升级，保留原数据。'));
    }
  }
  async function load(){
    if(stopped)return;
    const requestGeneration=generation;
    try{const next=await api('/api/software-update');if(!stopped&&requestGeneration===generation)render(next);}
    catch(error){if(!stopped&&requestGeneration===generation){byId('software-update-message').textContent=error.message;byId('settings-software-status').textContent=error.message;byId('settings-software-check').disabled=false;}}
    if(poll!==null)clearTimeout(poll);
    if(!stopped)poll=setTimeout(load,['downloading','ready','checking'].includes(state.status)?1500:1800000);
  }
  function open(){byId('software-update-dialog').showModal();reveal(byId('software-update-dialog'));}
  // The explicit settings action bypasses the six-hour automatic check interval.
  // It checks fixed Releases metadata only, without downloading or running AI.
  byId('settings-software-check').addEventListener('click',async()=>{
    if(stopped||byId('settings-software-check').disabled)return;
    const requestGeneration=++generation;
    byId('settings-software-check').disabled=true;
    byId('settings-software-status').textContent=tr('正在检查软件版本');
    try{const next=await post('/api/software-update/check',{});if(!stopped&&requestGeneration===generation)render(next);}
    catch(error){if(!stopped&&requestGeneration===generation){byId('settings-software-status').textContent=error.message;byId('settings-software-check').disabled=false;}}
    if(poll!==null)clearTimeout(poll);
    if(!stopped)poll=setTimeout(load,1500);
  });
  byId('software-update-button').addEventListener('click',open);
  byId('software-update-toast-close').addEventListener('click',dismiss);
  byId('software-update-dialog-close').addEventListener('click',()=>byId('software-update-dialog').close());
  byId('software-update-install').addEventListener('click',async()=>{
    byId('software-update-install').disabled=true;
    try{render(await post('/api/software-update/install',{confirmed:true}));if(poll!==null)clearTimeout(poll);if(!stopped)poll=setTimeout(load,1500);}
    catch(error){byId('software-update-message').textContent=error.message;byId('software-update-install').disabled=!state.installable;}
  });
  return {render,notifyFailure,start:load,close(){stopped=true;if(poll!==null)clearTimeout(poll);poll=null;dismiss();}};
}};
