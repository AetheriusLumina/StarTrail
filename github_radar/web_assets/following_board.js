"use strict";
window.RadarFollowingBoard=(()=>{
  function create({api,post,byId,element,clear,tr,cardNode,translatePage,isCurrent,open}){
    let generation=0,folders=[],folder='all',busy=false,drag=null,editor=null,loading=false,tabsSignature='';
    const buttons=new Map(),dialog=byId('folder-dialog');
    const error=(where,value)=>{byId(where).textContent=tr(value.message||String(value));};
    const stop=e=>e?.stopPropagation?.();
    const action=(label,cls,handler)=>{const b=element('button','button button-quiet '+cls,tr(label));b.type='button';
      b.addEventListener('click',e=>{stop(e);if(!busy)return handler(e);});return b;};
    function setBusy(value){busy=value;byId('folder-save').disabled=value;byId('folder-cancel').disabled=value;byId('folder-new').disabled=value;
      byId('folder-name').disabled=value;byId('folder-destination').disabled=value;}
    async function write(path,payload,afterSave=null){
      if(busy)return;setBusy(true);const version=generation;
      try{await post(path,payload);if(version!==generation||!isCurrent())return;
        if(dialog.open)dialog.close();if(afterSave)return afterSave();await load();if(isCurrent())buttons.get(folder)?.focus();
      }catch(e){if(version===generation&&isCurrent())error(dialog.open?'folder-feedback':'following-status',e);}
      finally{setBusy(false);}
    }
    function showEditor(kind,item=null){
      if(busy)return;editor={kind,item};byId('folder-feedback').textContent='';
      const naming=kind==='create'||kind==='rename',moving=kind==='move';
      byId('folder-name').hidden=!naming;byId('folder-name-label').hidden=!naming;byId('folder-name').required=naming;
      if(window.RadarThemeSelect)window.RadarThemeSelect.setHidden(byId('folder-destination'),!moving);else byId('folder-destination').hidden=!moving;
      byId('folder-destination-label').hidden=!moving;byId('folder-destination').required=moving;
      byId('folder-dialog-title').textContent=tr({create:'新建文件夹',rename:'重命名文件夹',delete:'删除文件夹',move:'移动到文件夹'}[kind]);
      byId('folder-save').textContent=tr({create:'创建',rename:'保存',delete:'删除文件夹',move:'移动'}[kind]);
      byId('folder-dialog-note').textContent=kind==='delete'?item.name+' · '+tr('只删除文件夹分类，关注项目保留。'):kind==='move'?item.title:'';
      byId('folder-name').value=kind==='rename'?item.name:'';
      clear(byId('folder-destination'));
      const placeholder=element('option','',tr('选择文件夹'));placeholder.value='';byId('folder-destination').append(placeholder);
      for(const f of folders){const option=element('option','',f.name);option.value=String(f.id);byId('folder-destination').append(option);}
      byId('folder-destination').value='';window.RadarThemeSelect?.refresh();dialog.showModal();
      if(moving&&window.RadarThemeSelect)window.RadarThemeSelect.focus(byId('folder-destination'));else (naming?byId('folder-name'):moving?byId('folder-destination'):byId('folder-cancel')).focus();
    }
    async function saveEditor(e){e.preventDefault();if(!editor||busy)return;
      const {kind,item}=editor;
      if(kind==='create')return write('/api/folders',{name:byId('folder-name').value});
      if(kind==='rename')return write('/api/folders/'+item.id,{action:'rename',name:byId('folder-name').value});
      if(kind==='delete'){
        return write('/api/folders/'+item.id,{action:'delete'},folder===String(item.id)?()=>open('/following'):null);
      }
      if(kind==='move')return write('/api/following/'+item.repo_id+'/folders',{action:'move_unfiled',folder_id:Number(byId('folder-destination').value)});
    }
    function reorder(identity,target){
      const ids=folders.map(f=>f.id),from=ids.indexOf(identity),to=ids.indexOf(target);
      if(from<0||to<0||from===to)return;ids.splice(from,1);ids.splice(to,0,identity);
      return write('/api/folders/order',{ids});
    }
    function endDrag(){drag=null;for(const node of byId('following-folders').querySelectorAll('.folder-tab'))node.setAttribute('data-drop-target','false');}
    function renderTabs(data){
      const root=byId('following-folders');
      const signature=JSON.stringify([tr('全部关注'),tr('未分类'),data.all_count??data.cards.length,data.unfiled_count??data.cards.length,folders]);
      if(signature===tabsSignature){
        for(const [id,button] of buttons)button.setAttribute('aria-pressed',String(folder===id));
        for(const row of root.querySelectorAll('.folder-tab'))row.setAttribute('data-selected',String(folder===row.getAttribute('data-folder-id')));
        return;
      }
      tabsSignature=signature;clear(root);buttons.clear();
      for(const entry of [{id:'all',name:tr('全部关注'),count:data.all_count??data.cards.length},{id:'unfiled',name:tr('未分类'),count:data.unfiled_count??data.cards.length}]){
        const b=action(entry.name+' · '+entry.count,'folder-system',()=>open('/following'+(entry.id==='all'?'':'?folder=unfiled')));
        b.setAttribute('aria-pressed',String(folder===entry.id));buttons.set(entry.id,b);
        if(entry.id==='unfiled'){
          b.addEventListener('dragover',e=>{if(drag?.kind==='project'&&!busy&&!loading){e.preventDefault();b.setAttribute('data-drop-target','true');}});
          b.addEventListener('dragleave',()=>b.setAttribute('data-drop-target','false'));
          b.addEventListener('drop',e=>{if(drag?.kind!=='project'||busy||loading)return;e.preventDefault();stop(e);const value=drag;endDrag();b.setAttribute('data-drop-target','false');return write('/api/following/'+value.id+'/folders',{action:'move',source:value.source,target:'unfiled'});});
        }
        root.append(b);
      }
      folders.forEach((item,index)=>{
        const row=element('div','folder-tab button button-quiet');row.draggable=true;row.setAttribute('data-folder-id',String(item.id));
        row.setAttribute('data-selected',String(folder===String(item.id)));
        const select=action(item.name+' · '+item.count,'folder-select',()=>open('/following?folder='+item.id));
        select.setAttribute('aria-pressed',String(folder===String(item.id)));select.setAttribute('data-translation-literal','');buttons.set(String(item.id),select);
        const details=element('details','folder-actions'),summary=element('summary','button button-quiet folder-menu-button','⋮');
        details.name='following-folder-actions';
        summary.setAttribute('aria-label',item.name+' · '+tr('文件夹操作'));summary.addEventListener('click',e=>{stop(e);if(busy)e.preventDefault();});
        const menu=element('div','folder-menu');menu.append(action('重命名','folder-rename',()=>showEditor('rename',item)),action('删除文件夹','folder-delete',()=>showEditor('delete',item)));
        const prev=action('前移','folder-prev',()=>reorder(item.id,folders[index-1]?.id)),next=action('后移','folder-next',()=>reorder(item.id,folders[index+1]?.id));
        prev.disabled=index===0;next.disabled=index===folders.length-1;menu.append(prev,next);details.append(summary,menu);row.append(select,details);
        details.addEventListener('keydown',e=>{if(e.key==='Escape'){details.open=false;summary.focus();}});
        row.addEventListener('dragstart',e=>{if(busy||loading||e.target?.closest?.('.folder-actions')){e.preventDefault();return;}drag={kind:'folder',id:item.id};e.dataTransfer.setData('application/x-github-radar',JSON.stringify(drag));e.dataTransfer.effectAllowed='move';});
        row.addEventListener('dragend',endDrag);
        row.addEventListener('dragover',e=>{if(!drag||busy||loading)return;e.preventDefault();e.dataTransfer.dropEffect='move';row.setAttribute('data-drop-target','true');});
        row.addEventListener('dragleave',()=>row.setAttribute('data-drop-target','false'));
        row.addEventListener('drop',e=>{if(!drag||busy||loading)return;e.preventDefault();stop(e);const value=drag;endDrag();
          if(value.kind==='folder')return reorder(value.id,item.id);
          if(value.kind==='project')return write('/api/following/'+value.id+'/folders',value.source==='unfiled'?{action:'move_unfiled',folder_id:item.id}:{action:'move',source:value.source,target:String(item.id)});
        });root.append(row);
      });
    }
    function renderCards(data){
      const root=byId('following-cards');clear(root);
      for(const card of data.cards||[]){
        const row=element('div','following-project-row'),node=cardNode(card,{compact:true});node.draggable=folder!=='all';
        node.addEventListener('dragstart',e=>{if(folder==='all'||busy||loading){e.preventDefault();return;}
          node.setAttribute('data-drag-block','true');drag={kind:'project',id:card.repo_id,source:folder};e.dataTransfer.setData('application/x-github-radar',JSON.stringify(drag));e.dataTransfer.effectAllowed='move';});
        node.addEventListener('dragend',endDrag);
        node.addEventListener('pointerdown',()=>node.setAttribute('data-drag-block','false'));
        node.addEventListener('keydown',()=>node.setAttribute('data-drag-block','false'));
        row.append(node);

        root.append(row);
      }
      byId('following-empty').hidden=!!data.cards?.length;
      byId('following-empty').textContent=tr(folder==='all'?(new URLSearchParams(location.search).get('q')?'没有匹配的关注项目。':'还没有关注项目。打开项目详情，点击“关注”即可加入。'):'这个分类还没有关注项目。');
    }
    async function load(){
      const version=++generation,params=new URLSearchParams(location.search),requested= params.get('folder')||'all',q=requested==='all'?(params.get('q')||''):'';
      loading=true;endDrag();byId('following-search').hidden=requested!=='all';byId('following-query').value=q;
      const query=new URLSearchParams();if(requested!=='all')query.set('folder',requested);if(q)query.set('q',q);
      try{const data=await api('/api/following'+(query.size?'?'+query:''));if(version!==generation||!isCurrent())return;
        folders=data.folders||[];folder=requested;renderTabs(data);renderCards(data);byId('following-status').textContent='';translatePage();
      }catch(e){if(version===generation&&isCurrent())error('following-status',e);}
      finally{if(version===generation)loading=false;}
    }
    byId('folder-new').addEventListener('click',()=>showEditor('create'));
    byId('folder-editor-form').addEventListener('submit',saveEditor);
    byId('folder-cancel').addEventListener('click',()=>{if(!busy)dialog.close();});
    dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
    byId('following-search').addEventListener('submit',e=>{e.preventDefault();const q=byId('following-query').value;return open('/following'+(q?'?q='+encodeURIComponent(q):''));});
    byId('following-clear').addEventListener('click',()=>open('/following'));
    document.addEventListener('click',e=>{
      for(const menu of byId('following-folders').querySelectorAll('.folder-actions'))
        if(menu.open&&!menu.contains(e.target))menu.open=false;
    },true);
    return {load,invalidate(){generation++;loading=false;endDrag();if(dialog.open)dialog.close();}};
  }
  return {create};
})();
