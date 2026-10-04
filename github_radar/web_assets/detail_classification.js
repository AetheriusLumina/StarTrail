/* One editor for every detail entry point. Late responses never touch another card. */
window.RadarDetailClassification={create({byId,api,post,element,tr,current,onSaved}){
  const dialog=byId('classification-dialog'),choices=byId('classification-choices'),status=byId('classification-status');
  let generation=0,target=null,busy=false,loading=false,loaded=false,selected=new Set();
  const valid=(version,id)=>version===generation&&target===id&&current()===id;
  const controls=()=>{byId('classification-save').disabled=busy||loading||!loaded;byId('classification-name').disabled=busy||loading;for(const input of choices.querySelectorAll('input'))input.disabled=busy;};
  async function open(id,followed){
    const version=++generation;target=id;busy=false;loading=true;loaded=false;selected=new Set();choices.replaceChildren();status.textContent=tr('正在读取文件夹…');byId('classification-name').value='';
    if(!dialog.open)dialog.showModal();controls();byId('classification-cancel').focus();
    try{
      const data=await api(followed?'/api/following/'+id+'/folders':'/api/folders');
      if(!valid(version,id))return;
      selected=new Set(data.ids||[]);
      for(const folder of data.folders||[]){const label=element('label','folder-choice'),input=element('input');input.type='checkbox';input.checked=selected.has(folder.id);input.addEventListener('change',()=>input.checked?selected.add(folder.id):selected.delete(folder.id));label.append(input,element('span','',folder.name));choices.append(label);}
      loaded=true;status.textContent=tr('可选择多个文件夹；全部取消则放入未分类。');
    }catch(error){if(valid(version,id))status.textContent=error.message;}
    finally{if(valid(version,id)){loading=false;controls();}}
  }
  async function save(event){
    event.preventDefault();if(busy||loading||!loaded||target===null)return;
    const id=target,version=generation;busy=true;controls();status.textContent=tr('正在保存…');
    try{const value=await post('/api/following/'+id+'/classify',{ids:[...selected],create_name:byId('classification-name').value.trim()||null});
      if(!valid(version,id))return;
      onSaved(id,value);dialog.close();generation++;target=null;
    }catch(error){if(valid(version,id))status.textContent=error.message;}
    finally{if(version===generation){busy=false;controls();}}
  }
  const cancel=()=>{if(busy)return;generation++;target=null;dialog.close();};
  byId('classification-form').addEventListener('submit',save);
  byId('classification-cancel').addEventListener('click',cancel);
  dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();else{generation++;target=null;}});
  return {open,invalidate(){generation++;target=null;if(dialog.open)dialog.close();}};
}};
