"use strict";
/* Models only return text. Original DOM, attributes, code and link nodes stay local. */
window.RadarTranslation = {
  create({post, api, getLanguage, onStatus}) {
    const records = new Map();
    const inFlight = new Map();
    const pendingJobs=new Set();let cancellation=null;
    let generation = 0, nextId = 0, original = false, closed = false, lastRoot = null;
    const nodesOf = node => Array.from(node.childNodes);
    function snapshot(node) {
      const parts = [], literals = [];
      const walk = parent => {
        for (const child of nodesOf(parent)) {
          if (child.nodeType === 3) parts.push({kind:"text", text:child.textContent});
          else if (child.nodeType === 1) {
            if (["CODE","PRE","A"].includes(child.tagName) || child.hasAttribute("data-translation-literal")) {
              const text = child.textContent;
              parts.push({kind:"literal",text:text.length>4096?`⟦protected-${literals.length}⟧`:text}); literals.push(child);
            } else walk(child);
          }
        }
      };
      walk(node);
      return {node, id:`t${++nextId}`, source:nodesOf(node), last:nodesOf(node), parts, literals,
              target:null, status:"new", reason:null};
    }
    function restore(record) {
      record.node.replaceChildren(...record.source);
      record.last = nodesOf(record.node);
      record.target=null; record.status="new"; record.reason=null;
    }
    function invalidate() {
      generation++;
      inFlight.clear();
      const jobs=[...pendingJobs];pendingJobs.clear();
      if(jobs.length)cancellation=Promise.all(jobs.map(id=>post(`/api/translation/${encodeURIComponent(id)}/cancel`,{}).catch(()=>{})));
      for (const record of records.values()) restore(record);
      records.clear();
    }
    function status() {
      const visible = [...records.values()].filter(r => r.node.isConnected && !r.node.closest("[hidden]"));
      const pending = visible.filter(r=>r.status==="pending").length;
      const failed = visible.filter(r=>r.status==="failed").length;
      onStatus({state:original?"original":pending?"translating":failed?"fallback":visible.length?"ready":"idle",
                failed, count:visible.length, reason:visible.find(r=>r.reason)?.reason || null});
    }
    function apply(record, result) {
      if (result.status !== "translated") {
        record.status = result.reason ? "failed" : "ready";
        record.reason = result.reason || null;
        return;
      }
      // Link labels may have independently translated; compare immutable source parts.
      const values = record.parts.filter(p=>p.kind==="literal").map(p=>p.text);
      if (!Array.isArray(result.parts) || result.parts.some(p=>!p || !["text","literal"].includes(p.kind) || typeof p.text!=="string") ||
          JSON.stringify(result.parts.filter(p=>p.kind==="literal").map(p=>p.text)) !== JSON.stringify(values))
        throw new Error("保护片段发生变化，显示原文");
      const nodes = []; let literalIndex = 0;
      for (const part of result.parts) nodes.push(part.kind==="literal"
        ? record.literals[literalIndex++] : document.createTextNode(part.text));
      record.node.replaceChildren(...nodes);
      record.last=nodesOf(record.node); record.status="ready"; record.reason=null;
    }
    function chunks(parts) {
      const result=[];let current=[],size=0;
      const push=part=>{
        if(size+part.text.length>8192&&current.length){result.push(current);current=[];size=0;}
        current.push(part);size+=part.text.length;
      };
      for(const part of parts){
        if(part.kind==='literal'){push(part);continue;}
        let text=part.text;
        while(text.length>4096){
          let end=text.lastIndexOf(' ',4096);
          if(end<2048)end=4096;else end++;
          if(/[\uD800-\uDBFF]/.test(text[end-1]))end--;
          push({kind:'text',text:text.slice(0,end)});text=text.slice(end);
        }
        if(text)push({kind:'text',text});
      }
      if(current.length)result.push(current);
      return result;
    }
    function refresh(root) {
      const version=generation, existing=inFlight.get(root);
      if(existing)return existing.then(()=>version===generation?refresh(root):undefined);
      const work=performRefresh(root).finally(()=>{if(inFlight.get(root)===work)inFlight.delete(root);});
      inFlight.set(root,work);return work;
    }
    async function performRefresh(root, preparing=false) {
      if (!root || closed) return;
      if(!preparing){if(lastRoot && root!==lastRoot)invalidate();lastRoot=root;}
      if (original) { status(); return; }
      const version=generation, target=getLanguage();
      const candidates=[...(root.matches?.("[data-translate]") || root.matches?.("[data-translate-link]") ? [root] : []),
                        ...root.querySelectorAll("[data-translate],[data-translate-link]")];
      const work=[];
      for (const node of candidates) {
        if (node.closest("[hidden]") || node.closest("pre,code")) continue;
        let record=records.get(node);
        const current=nodesOf(node);
        if (record && (current.length!==record.last.length || current.some((n,i)=>n!==record.last[i]))) {
          records.delete(node); record=null;
        }
        if (!record) { record=snapshot(node); records.set(node,record); }
        record.preparing=preparing;
        if (record.target===target && record.status!=="new") continue;
        record.target=target;
        if (!record.parts.some(p=>p.kind==="text" && p.text.trim())) { record.status="ready"; continue; }
        record.status="pending";record.reason=null;
        record.chunks=chunks(record.parts);record.results=new Array(record.chunks.length);
        record.chunks.forEach((parts,index)=>work.push({id:`${record.id}-${index}`,parts,record,index}));
      }
      for (const [node,record] of records) if (!node.isConnected&&!record.preparing) records.delete(node);
      status();
      const active=()=>!closed && !original && version===generation && target===getLanguage() && (preparing||lastRoot===root);
      if(cancellation){const wait=cancellation;await wait;if(cancellation===wait)cancellation=null;}
      while (work.length && active()) {
        const batch=[]; let length=0;
        while (work.length && batch.length<128) {
          const candidate=work[0], size=candidate.parts.reduce((n,p)=>n+p.text.length,0);
          const trial=[...batch,candidate].map(r=>({id:r.id,parts:r.parts}));
          if (batch.length && (length+size>16384 || new TextEncoder().encode(JSON.stringify({target,items:trial})).length>524288)) break;
          work.shift(); batch.push(candidate); length+=size;
        }
        try {
          let result=await post("/api/translation",{target,items:batch.map(r=>({id:r.id,parts:r.parts}))});
          const identity=result.job_id;
          if(!active()){
            if(identity && ['queued','running'].includes(result.status))await post(`/api/translation/${encodeURIComponent(identity)}/cancel`,{}).catch(()=>{});
            return;
          }
          if(identity && ['queued','running'].includes(result.status))pendingJobs.add(identity);
          while (active() && ["queued","running"].includes(result.status)) {
            await new Promise(resolve=>setTimeout(resolve,200));
            if (!active()) return;
            result=await api(`/api/translation/${encodeURIComponent(result.job_id)}`);
          }
          if(identity)pendingJobs.delete(identity);
          if (!active()) return;
          if (result.status!=="ready") throw new Error(result.reason || "离线翻译未完成，显示原文");
          const results=new Map((result.items || []).map(r=>[r.id,r]));
          for (const job of batch) {
            const record=job.record;
            if ((!record.node.isConnected&&!preparing) || records.get(record.node)!==record) continue;
            try {
              const resultItem=results.get(job.id);
              if (!resultItem || !['translated','original'].includes(resultItem.status)) throw new Error("翻译未完成，显示原文");
              if(resultItem.reason)throw new Error(resultItem.reason);
              const literals=job.parts.filter(p=>p.kind==='literal').map(p=>p.text);
              if(!Array.isArray(resultItem.parts)||resultItem.parts.some(p=>!p||!['text','literal'].includes(p.kind)||typeof p.text!=='string')||JSON.stringify(resultItem.parts.filter(p=>p.kind==='literal').map(p=>p.text))!==JSON.stringify(literals))
                throw new Error('保护片段发生变化，显示原文');
              record.results[job.index]=resultItem.parts;
              if(record.status!=='failed'&&Array.from(record.results).every(Boolean))
                apply(record,{status:'translated',parts:record.results.flat()});
              if (result.reason) record.reason=result.reason;
            } catch (error) { restore(record);record.target=target;record.status="failed";record.reason=error.message; }
          }
        } catch (error) {
          if (!active()) return;
          for (const {record} of batch) { record.status="failed";record.reason=error.message; }
        }
        status();
      }
    }
    function adopt(from,to){
      for(const [node] of records)if(node===to||to.contains(node))records.delete(node);
      to.replaceChildren(...nodesOf(from));
      const own=records.get(from);if(own){records.delete(from);own.node=to;records.set(to,own);}
      for(const [node,record] of records)if(node===to||to.contains(node))record.preparing=false;
    }
    const prepare=root=>performRefresh(root,true).finally(()=>{for(const [node,record] of records)if(node===root||root.contains(node))record.preparing=false;});
    return {refresh, invalidate,prepare,adopt,
      retry() { for(const record of records.values())if(record.status==='failed'){record.status='new';record.target=null;}return lastRoot?refresh(lastRoot):undefined; },
      setOriginal(value) { original=Boolean(value);invalidate();status();if (!original && lastRoot) return refresh(lastRoot); },
      close() { closed=true;invalidate(); }
    };
  }
};
