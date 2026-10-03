"use strict";
window.RadarCalendar=(()=>{
  const pad=n=>String(n).padStart(2,'0');
  function parts(month){
    if(!/^\d{4}-\d{2}$/.test(month))throw Error('请选择有效年月');
    const [y,m]=month.split('-').map(Number);
    if(y<1||y>9999||m<1||m>12)throw Error('请选择有效年月');
    return [y,m];
  }
  function dateAt(y,m,d){const value=new Date(0);value.setFullYear(y,m-1,d);value.setHours(0,0,0,0);return value;}
  function cells(month){
    const [y,m]=parts(month),first=dateAt(y,m,1),count=dateAt(y,m+1,0).getDate();
    const result=Array((first.getDay()+6)%7).fill(null);
    for(let d=1;d<=count;d++)result.push(month+'-'+pad(d));
    while(result.length%7)result.push(null);return result;
  }
  function shiftMonth(month,delta){const [y,m]=parts(month),index=Math.max(0,Math.min(9999*12-1,(y-1)*12+m-1+delta));
    return String(Math.floor(index/12)+1).padStart(4,'0')+'-'+pad(index%12+1);}
  function hasFilters(value){return !!(value.q||value.from||value.to||(value.source&&value.source!=='all'));}
  function create({api,byId,element,clear,tr,onDay,onMonth,isCurrent}){
    const today=new Date(),todayKey=today.getFullYear()+'-'+pad(today.getMonth()+1)+'-'+pad(today.getDate());
    let month=todayKey.slice(0,7),selected=null,enabled=true,generation=0,records=[],buttons=new Map();
    const controls=['history-year','history-month','history-prev-month','history-next-month','history-calendar-retry'];
    function setSearch(active){
      if(enabled!==!active)generation++;
      enabled=!active;const root=byId('history-calendar');root.inert=active;
      root.className='history-calendar'+(active?' is-disabled':'');root.setAttribute('aria-hidden',String(active));
      for(const id of controls)byId(id).disabled=active;
      for(const button of buttons.values())button.disabled=active;
    }
    function render(){
      const [y,m]=parts(month);byId('history-year').value=String(y);byId('history-month').value=String(m);
      const grid=byId('history-calendar-days');clear(grid);buttons=new Map();
      const counts=new Map(records.map(row=>[row.date,row.count]));
      for(const day of cells(month)){
        if(!day){const blank=element('span','calendar-blank');blank.setAttribute('aria-hidden','true');grid.append(blank);continue;}
        const count=counts.get(day)||0,button=element('button','calendar-day button button-quiet');button.type='button';
        button.setAttribute('data-date',day);button.setAttribute('aria-label',day+' · '+count+' '+tr('个项目'));
        button.setAttribute('aria-pressed',String(selected===day));if(day===todayKey)button.setAttribute('aria-current','date');
        button.disabled=!enabled;button.append(element('span','calendar-number',String(Number(day.slice(-2)))));
        if(count)button.append(element('span','calendar-count',count+' '+tr('个项目')));
        button.addEventListener('click',()=>choose(day));
        button.addEventListener('keydown',event=>{
          const offsets={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7};
          if(!(event.key in offsets))return;event.preventDefault();
          const keys=[...buttons.keys()],next=keys.indexOf(day)+offsets[event.key];buttons.get(keys[next])?.focus();
        });
        grid.append(button);buttons.set(day,button);
      }
    }
    async function choose(day){if(!enabled||!isCurrent())return;selected=day;
      for(const [key,button] of buttons)button.setAttribute('aria-pressed',String(key===day));
      return onDay(day);
    }
    async function load(){
      if(!enabled||!isCurrent())return;const version=++generation,requested=month;
      byId('history-calendar-status').textContent=tr('正在读取日历…');byId('history-calendar-retry').hidden=true;
      records=[];render();
      try{const data=await api('/api/history/calendar?month='+requested);
        if(version!==generation||!enabled||!isCurrent())return;
        records=data.dates||[];render();byId('history-calendar-status').textContent='';
      }catch(error){if(version!==generation||!enabled||!isCurrent())return;
        byId('history-calendar-status').textContent=error.message;byId('history-calendar-retry').hidden=false;}
    }
    function change(next){if(!enabled)return;parts(next);if(next===month)return;month=next;selected=null;onMonth();return load();}
    const select=()=>{try{return change(String(Number(byId('history-year').value)).padStart(4,'0')+'-'+pad(Number(byId('history-month').value)));}
      catch(error){byId('history-calendar-status').textContent=tr(error.message);render();}};
    byId('history-year').addEventListener('change',select);byId('history-month').addEventListener('change',select);
    byId('history-prev-month').addEventListener('click',()=>change(shiftMonth(month,-1)));
    byId('history-next-month').addEventListener('click',()=>change(shiftMonth(month,1)));
    byId('history-calendar-retry').addEventListener('click',load);
    return {load,setSearch,choose,month:()=>month,selected:()=>selected,invalidate(){generation++;},render};
  }
  return {cells,shiftMonth,hasFilters,create};
})();
