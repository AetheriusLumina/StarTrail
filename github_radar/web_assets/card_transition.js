"use strict";
window.RadarCardTransition=(()=>{
  const keys=['title','rank','stars','growth','description'];
  const duration=350,easing='cubic-bezier(.22,.72,.2,1)';
  function create({document,enabled}){
    let current=null,generation=0;const reveals=new Map();
    const visible=node=>!!node&&node.isConnected!==false&&!node.hidden&&node.getBoundingClientRect().width>0&&node.getBoundingClientRect().height>0;
    const focusDestination=to=>{const p=to();(p?.focus||p?.frame)?.focus({preventScroll:true});};
    function clean(state){for(const restore of state.restores.splice(0))restore();
      if(current===state){current=null;delete document.documentElement.dataset.cardTransition;delete document.documentElement.dataset.pageTransition;delete document.documentElement.dataset.motionPending;}}
    function cancel(){generation++;for(const a of reveals.values())a.cancel();reveals.clear();
      const state=current;if(!state)return;state.cancelled=true;for(const a of state.animations)a.cancel();clean(state);}
    function play(state,node,frames,options={}){if(!visible(node)||typeof node.animate!=='function')return;
      const a=node.animate(frames,{duration,easing,...options});state.animations.push(a);return a;}
    function morph(state,to,rects){
      const frame=to.frame,win=document.defaultView;
      if(!win?.getComputedStyle||!document.createElement||!frame.classList||!frame.append)return;
      const surface=document.createElement('div'),style=win.getComputedStyle(frame),dest=frame.getBoundingClientRect(),old=rects.frame;
      if(!old||!dest.width||!dest.height)return;
      surface.className='radar-morph-surface';surface.setAttribute('aria-hidden','true');
      for(const property of ['background','border','borderRadius','boxShadow','backdropFilter'])surface.style[property]=style[property];
      surface.style.width=dest.width+'px';surface.style.height=dest.height+'px';
      frame.classList.add('radar-live-card');frame.append(surface);
      state.restores.push(()=>{surface.remove();frame.classList.remove('radar-live-card');});
      play(state,surface,[{transform:`translate(${old.left-dest.left}px,${old.top-dest.top}px) scale(${old.width/dest.width},${old.height/dest.height})`},{transform:'translate(0px,0px) scale(1,1)'}]);
    }
    async function run({from,to,update,reverse=false,kind='card'}){
      cancel();const version=generation,animate=enabled()&&visible(from?.frame)&&typeof from.frame.animate==='function';
      const rects={};if(animate)for(const key of ['frame',...keys])if(visible(from[key]))rects[key]=from[key].getBoundingClientRect();
      if(!animate){await update();if(generation===version)focusDestination(to);return;}
      const state={animations:[],restores:[],cancelled:false};current=state;
      document.documentElement.dataset[kind==='page'?'pageTransition':'cardTransition']=reverse?'return':'open';
      const id=from.frame.id;
      document.documentElement.dataset.motionPending=kind==='page'&&['following-results','growth-results','keyword-results'].includes(id)?id:'route';
      try{
        await update();if(current!==state||generation!==version)return;
        const destination=to();if(!visible(destination?.frame))return;
        if(kind==='card'){
          morph(state,destination,rects);
          play(state,destination.frame,[{opacity:0},{opacity:1}]);
          for(const key of keys){const node=destination[key],old=rects[key];if(!old||!visible(node))continue;
            const next=node.getBoundingClientRect();play(state,node,[{transform:`translate(${old.left-next.left}px,${old.top-next.top}px)`,opacity:0},{transform:'translate(0px,0px)',opacity:1}]);}
          for(const [key,delay] of [['facts',80],['actions',50]])play(state,destination[key],[{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0px)'}],{duration:duration-delay,delay,fill:'backwards'});
        }else play(state,destination.page||destination.frame,[{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0px)'}]);
        delete document.documentElement.dataset.motionPending;
        await Promise.all(state.animations.map(a=>a.finished.catch(()=>{})));
      }finally{const focus=current===state&&!state.cancelled;clean(state);if(focus)focusDestination(to);}
    }
    function reveal(node){if(!enabled()||!visible(node)||typeof node.animate!=='function')return;
      reveals.get(node)?.cancel();const a=node.animate([{opacity:0},{opacity:1}],{duration,easing});reveals.set(node,a);
      a.finished.catch(()=>{}).then(()=>{if(reveals.get(node)===a)reveals.delete(node);});}
    return {run,cancel,reveal};
  }
  return {create};
})();
