const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const filename=require('node:path').join(__dirname,'../github_radar/web_assets/translation.js');
assert.ok(fs.existsSync(filename),'Offline DOM translation controller not implemented');
class Node {
  constructor(tag='SPAN',text='') { this.tagName=tag; this.nodeType=tag==='#text'?3:1; this.value=text; this.childNodes=[]; this.parentNode=null; this.attrs={}; this.isConnected=true; }
  get textContent(){return this.nodeType===3?this.value:this.childNodes.map(n=>n.textContent).join('');}
  set textContent(value){this.replaceChildren(new Node('#text',value));}
  append(...nodes){for(const n of nodes){n.parentNode=this; this.childNodes.push(n);}}
  replaceChildren(...nodes){this.childNodes=[]; this.append(...nodes);}
  matches(selector){return selector==='[data-translate]'?'data-translate' in this.attrs:selector==='[data-translate-link]'?'data-translate-link' in this.attrs:false;}
  hasAttribute(name){return name in this.attrs;}
  contains(node){return this===node||this.childNodes.some(n=>n.contains(node));}
  closest(selector){for(let n=this;n;n=n.parentNode){if(selector==='[hidden]'&&n.hidden)return n; if(selector==='pre,code'&&['PRE','CODE'].includes(n.tagName))return n;} return null;}
  querySelectorAll(selector){const out=[]; for(const n of this.childNodes){if(selector.split(',').some(s=>n.matches(s.trim())))out.push(n);out.push(...n.querySelectorAll(selector));}return out;}
}
const doc={createTextNode:text=>new Node('#text',text)};
const context={window:{},document:doc,setTimeout,TextEncoder,console};
vm.createContext(context);vm.runInContext(fs.readFileSync(filename,'utf8'),context);
const create=context.window.RadarTranslation.create;
function readable(text){const n=new Node('P');n.textContent=text;n.attrs['data-translate']='';return n;}
function translateResult(payload){return {status:'ready',items:payload.items.map(i=>({id:i.id,status:'translated',parts:i.parts.map(p=>p.kind==='literal'?p:{kind:'text',text:'译:'+p.text})}))};}
(async()=>{
  const root=new Node('MAIN'),p=readable('');root.append(p);
  const code=new Node('CODE');code.textContent='npm install';
  const link=new Node('A');link.textContent='Installation';link.attrs['data-translate-link']='';link.href='https://example.com/#original';
  const arrow=new Node('SPAN');arrow.textContent=' ↗';arrow.attrs['data-translation-literal']='';link.append(arrow);
  p.replaceChildren(new Node('#text','See '),code,new Node('#text',' and '),link);
  const original=[...p.childNodes], requests=[],states=[];
  const control=create({post:async(path,payload)=>{requests.push(payload);return translateResult(payload);},api:async()=>assert.fail('unexpected polling'),getLanguage:()=> 'zh',onStatus:s=>states.push(s)});
  await control.refresh(root);
  assert.equal(link.href,'https://example.com/#original');assert.equal(code.textContent,'npm install');
  assert.ok(p.childNodes.includes(code)&&p.childNodes.includes(link),'literal nodes retained');
  assert.equal(link.textContent,'译:Installation ↗');assert.equal(arrow.textContent,' ↗');assert.ok(p.textContent.includes('译:See'));
  await control.refresh(root);assert.equal(requests.length,1,'same content does not resubmit');
  control.setOriginal(true);assert.deepEqual(p.childNodes,original);assert.equal(link.textContent,'Installation ↗');
  control.setOriginal(false);await control.refresh(root);assert.ok(p.textContent.includes('译:See'));
  const unsafe=readable('Safety');root.append(unsafe);
  const html=create({post:async(_,payload)=>({status:'ready',items:payload.items.map(i=>({id:i.id,status:'translated',parts:[{kind:'text',text:'<img src=x onerror=evil()>'}]}))}),api:async()=>{},getLanguage:()=> 'zh',onStatus:()=>{}});
  await html.refresh(unsafe);assert.equal(unsafe.childNodes[0].nodeType,3);assert.ok(unsafe.textContent.startsWith('<img'));
  const many=new Node('MAIN');for(let i=0;i<260;i++)many.append(readable('Project '+i));
  const batches=[];const long=create({post:async(_,payload)=>{batches.push(payload);return translateResult(payload)},api:async()=>{},getLanguage:()=> 'zh',onStatus:()=>{}});
  await long.refresh(many);assert.equal(batches.length,3);assert.equal(batches.reduce((n,b)=>n+b.items.length,0),260);assert.ok(many.childNodes[259].textContent.startsWith('译:'));
  let language='zh',release;
  const lateNode=readable('Late prose'),oldNodes=[...lateNode.childNodes];
  const late=create({post:async(_,payload)=>{await new Promise(r=>release=r);return translateResult(payload)},api:async()=>{},getLanguage:()=>language,onStatus:()=>{}});
  const pending=late.refresh(lateNode);language='en';late.invalidate();release();await pending;assert.deepEqual(lateNode.childNodes,oldNodes,'old language response discarded');
  const pending2=late.refresh(lateNode);late.setOriginal(true);release();await pending2;assert.deepEqual(lateNode.childNodes,oldNodes,'original mode rejects old reply');
  late.setOriginal(false);const pending3=late.refresh(lateNode);late.close();release();await pending3;assert.deepEqual(lateNode.childNodes,oldNodes,'quit rejects old reply');
  const failed=readable('Original remains');let failure;
  const fallback=create({post:async()=>({status:'error',reason:'offline unavailable',items:[]}),api:async()=>{},getLanguage:()=> 'zh',onStatus:s=>failure=s});
  await fallback.refresh(failed);assert.equal(failed.textContent,'Original remains');assert.equal(failure.failed,1);
  const pre=new Node('PRE'),fenced=readable('dangerous code');pre.append(fenced);
  await control.refresh(pre);assert.equal(fenced.textContent,'dangerous code');
  const giant=readable('Beginning. '+'Long English paragraph. '.repeat(3500)+' Final marker.');
  const giantOriginal=giant.textContent, giantBatches=[], giantStates=[];
  const full=create({post:async(_,payload)=>{giantBatches.push(payload);return translateResult(payload);},
    api:async()=>{},getLanguage:()=> 'zh',onStatus:s=>giantStates.push(s)});
  await full.refresh(giant);
  assert.ok(giant.textContent.startsWith('译:Beginning.'),'The first long paragraph block is translated');
  assert.ok(giant.textContent.endsWith('Final marker.'),'The tail is present');
  assert.ok(giantBatches.length>1,'Full README crosses job boundaries');
  assert.equal(giantBatches.flatMap(b=>b.items).flatMap(i=>i.parts).map(p=>p.text).join(''),giantOriginal);
  assert.ok(giantBatches.every(b=>b.items.every(i=>i.parts.reduce((n,p)=>n+p.text.length,0)<=16384)));
  assert.equal(giantStates.at(-1).failed,0);
  full.setOriginal(true);assert.equal(giant.textContent,giantOriginal);

  const retryNode=readable('Chunked English. '.repeat(1500));let failOnce=true,retryState;
  const retry=create({post:async(_,payload)=>{if(failOnce){failOnce=false;throw Error('temporary error');}return translateResult(payload);},
    api:async()=>{},getLanguage:()=> 'zh',onStatus:s=>retryState=s});
  const retryOriginal=retryNode.textContent;
  await retry.refresh(retryNode);assert.equal(retryState.failed,1);assert.equal(retryNode.textContent,retryOriginal);
  await retry.retry();assert.equal(retryState.failed,0);assert.ok(retryNode.textContent.startsWith('译:'));
  const codeLong=new Node('CODE');codeLong.textContent='literal command '.repeat(4000);
  const codeOwner=readable('Read ');codeOwner.append(codeLong,new Node('#text',' safely.'));
  const codeControl=create({post:async(_,payload)=>translateResult(payload),api:async()=>{},getLanguage:()=> 'zh',onStatus:()=>{}});
  await codeControl.refresh(codeOwner);assert.ok(codeOwner.textContent.startsWith('译:Read '));
  assert.equal(codeLong.textContent,'literal command '.repeat(4000));
  let submitRelease;const cancelled=[];
  const switching=create({post:async(path)=>{
    if(path.endsWith('/cancel')){cancelled.push(path);return {status:'cancelled'};}
    await new Promise(r=>submitRelease=r);return {status:'queued',job_id:'oldjob'};
  },api:async()=>assert.fail('An obsolete page must not poll'),getLanguage:()=> 'zh',onStatus:()=>{}});
  const obsolete=readable('Obsolete project');const obsoleteWork=switching.refresh(obsolete);
  switching.invalidate();submitRelease();await obsoleteWork;
  assert.deepEqual(cancelled,['/api/translation/oldjob/cancel']);assert.equal(obsolete.textContent,'Obsolete project');
  const joinedNode=readable('Prepared before showing');let finishJoined,joinedDone=false;
  const joined=create({post:async(_,payload)=>{await new Promise(r=>finishJoined=r);return translateResult(payload);},
    api:async()=>{},getLanguage:()=> 'zh',onStatus:()=>{}});
  const firstJoined=joined.refresh(joinedNode);
  const secondJoined=joined.refresh(joinedNode).then(()=>{joinedDone=true;});
  await new Promise(r=>setTimeout(r,5));
  assert.equal(joinedDone,false,'A second refresh must wait for already pending translation before displaying the page');
  finishJoined();await Promise.all([firstJoined,secondJoined]);assert.ok(joinedNode.textContent.startsWith('译:'));
  const screen=new Node('MAIN'),visible=readable('Old purpose');screen.append(visible);let ready;
  const replacement=create({post:async(_,payload)=>{if(payload.items.some(i=>i.parts.some(p=>p.text==='New purpose')))await new Promise(r=>ready=r);return translateResult(payload);},api:async()=>{},getLanguage:()=> 'zh',onStatus:()=>{}});
  await replacement.refresh(screen);const oldTranslated=visible.textContent,staging=readable('New purpose');staging.isConnected=false;
  const prepared=replacement.prepare(staging);await new Promise(r=>setTimeout(r,5));assert.equal(visible.textContent,oldTranslated,'Preparing detached new text does not restore or replace visible translation');
  ready();await prepared;assert.equal(staging.textContent,'译:New purpose');replacement.adopt(staging,visible);assert.equal(visible.textContent,'译:New purpose');
  replacement.setOriginal(true);assert.equal(visible.textContent,'New purpose','Original switch retains the newly adopted source');
  console.log('Translation DOM tests passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
