"use strict";
// Query values and folder names are literals, never translation input.
window.RadarHistoryFollowing=(()=>{
  const fields=['q','from','to','source'];
  function filters(value={}) {return {q:value.q||'',from:value.from||'',to:value.to||'',source:value.source||'all'};}
  function query(value,cursor=null) {
    const p=new URLSearchParams();
    for(const key of fields)if(value[key] && !(key==='source'&&value[key]==='all'))p.set(key,value[key]);
    if(cursor!==null)p.set('cursor',String(cursor));
    return p.size?'?'+p.toString():'';
  }
  function historyController({api,writeURL,onIndex}) {
    let state=filters(),generation=0,next=null,loadingMore=false;
    return {
      readURL(search){const p=new URLSearchParams(search);return filters(Object.fromEntries(fields.map(k=>[k,p.get(k)])));},
      filters:()=>({...state}),version:()=>generation,
      invalidate(){generation++;loadingMore=false;},
      emptyMessage:data=>data.history_total===0?'还没有历史记录。首页更新后，这里会按日期保存推荐。':'没有符合这些条件的历史项目。',
      async search(value,{write=true}={}){
        state=filters(value);const version=++generation;next=null;loadingMore=false;
        if(write)writeURL('/history'+query(state));
        try{const data=await api('/api/history'+query(state));if(version!==generation)return false;
          next=data.next_cursor??null;await onIndex(data,false,version);return version===generation;
        }catch(error){if(version===generation)throw error;return false;}
      },
      async more(){
        if(next===null||loadingMore)return false;const version=generation,cursor=next;loadingMore=true;
        try{const data=await api('/api/history'+query(state,cursor));if(version!==generation)return false;
          next=data.next_cursor??null;await onIndex(data,true,version);return version===generation;
        }catch(error){if(version===generation)throw error;return false;}
        finally{if(version===generation)loadingMore=false;}
      },
      async day(day){const version=generation;
        try{const data=await api('/api/history/'+encodeURIComponent(day)+query(state));return version===generation?data:null;}
        catch(error){if(version===generation)throw error;return null;}
      }
    };
  }
  function folderSelection({post}){
    let repo=null,generation=0,selected=[],saved=[],folders=[];
    return {
      load(id,ids,items){generation++;repo=id;selected=[...ids];saved=[...ids];folders=[...items];},
      ids:()=>[...selected],saved:()=>[...saved],folders:()=>[...folders],
      select(id,on){selected=on?[...new Set([...selected,id])]:selected.filter(x=>x!==id);},
      unfollow(){generation++;repo=null;selected=[];saved=[];folders=[];},
      async create(name){const version=generation;const {folder}=await post('/api/folders',{name});
        if(version!==generation)return null;folders.push(folder);selected.push(folder.id);return folder;},
      async save(){const version=generation,id=repo,intent=[...selected];
        const result=await post('/api/following/'+encodeURIComponent(id)+'/folders',{ids:intent});
        if(version!==generation)return false;saved=[...result.ids];return true;}
    };
  }
  return {historyController,folderSelection};
})();
