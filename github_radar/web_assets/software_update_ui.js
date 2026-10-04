"use strict";
window.RadarSoftwareUpdate={create({byId,api,post,tr,reveal=()=>{},remember}) {
  let state={},poll=null,expiry=null,stopped=false;
  const seen=new Set();
  function dismiss(){if(expiry!==null)clearTimeout(expiry);expiry=null;byId('software-update-toast').hidden=true;}
  function render(next){
    state=next||{};const release=state.release;
    const pending=['downloading','ready'].includes(state.status);
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
      dismiss();byId('software-update-toast-text').textContent=tr('发现新软件版本')+' '+release.tag+'。'+tr('可从左侧“软件更新”升级，保留原数据。');
      byId('software-update-toast').hidden=false;reveal(byId('software-update-toast'));
      expiry=setTimeout(dismiss,10000);
    }
  }
  async function load(){
    if(stopped)return;
    try{render(await api('/api/software-update'));}
    catch(error){byId('software-update-message').textContent=error.message;}
    if(poll!==null)clearTimeout(poll);
    if(!stopped)poll=setTimeout(load,['downloading','ready','checking'].includes(state.status)?1500:1800000);
  }
  function open(){byId('software-update-dialog').showModal();reveal(byId('software-update-dialog'));}
  byId('software-update-button').addEventListener('click',open);
  byId('software-update-toast-close').addEventListener('click',dismiss);
  byId('software-update-dialog-close').addEventListener('click',()=>byId('software-update-dialog').close());
  byId('software-update-install').addEventListener('click',async()=>{
    byId('software-update-install').disabled=true;
    try{render(await post('/api/software-update/install',{confirmed:true}));if(poll!==null)clearTimeout(poll);if(!stopped)poll=setTimeout(load,1500);}
    catch(error){byId('software-update-message').textContent=error.message;byId('software-update-install').disabled=!state.installable;}
  });
  return {render,start:load,close(){stopped=true;if(poll!==null)clearTimeout(poll);poll=null;dismiss();}};
}};
