const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const file=__dirname+'/../github_radar/web_assets/following_board.js';
class Node{
  constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.listeners=new Map();this.hidden=false;this.value='';}
  append(...items){this.children.push(...items);}setAttribute(k,v){this[k]=v;}getAttribute(k){return this[k];}
  addEventListener(k,fn){this.listeners.set(k,fn);}focus(){this.focused=true;}
  contains(n){return this===n||this.children.some(c=>c.contains(n));}
  showModal(){this.open=true;}close(){this.open=false;}get firstChild(){return this.children[0];}
  removeChild(n){this.children.splice(this.children.indexOf(n),1);}
  querySelectorAll(s){return this.children.flatMap(n=>[((n.className||'').split(' ').includes(s.slice(1))?n:null),...n.querySelectorAll(s)]).filter(Boolean);}
}
const event=()=>({button:0,preventDefault(){this.defaultPrevented=true;},stopPropagation(){},dataTransfer:{setData(){},effectAllowed:'',dropEffect:''}});
function fixture(){
  const document={listeners:new Map(),addEventListener(k,fn){this.listeners.set(k,fn);}};
  const context=vm.createContext({window:{},document,URL,URLSearchParams,location:{pathname:'/following',search:''}});
  if(fs.existsSync(file))vm.runInContext(fs.readFileSync(file,'utf8'),context);
  assert.ok(context.window.RadarFollowingBoard,'Following board module exists');
  const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
  let folders=[{id:1,name:'Alpha',count:0},{id:2,name:'Beta',count:0}],cards=[{repo_id:7,title:'owner/repo',description:'Local tool',folders:[]}];
  let fail=false,current=true,release=null;const posts=[],opens=[];
  const board=context.window.RadarFollowingBoard.create({byId:node,element:(tag,cls='',text='')=>{const n=new Node(tag,text);n.className=cls;return n;},
    clear:n=>{n.children=[];n.textContent='';},tr:s=>s,cardNode:card=>new Node('a',card.title),translatePage(){},isCurrent:()=>current,
    open:url=>{opens.push(url);context.location.search=new URL(url,'http://localhost').search;return board.load();},
    api:async()=>{if(release)return new Promise(resolve=>{release=()=>resolve({folders,cards,all_count:1,unfiled_count:1});});
      return {folders,cards:context.location.search.includes('folder=unfiled')?cards.filter(c=>!c.folders.length):cards,all_count:1,unfiled_count:1};},
    post:async(path,body)=>{posts.push({path,body});if(fail)throw Error('disk failure');
      if(path==='/api/folders/order')folders=body.ids.map(id=>folders.find(f=>f.id===id));
      else if(body.action==='move_unfiled')cards=cards.map(c=>({...c,folders:[{id:body.folder_id}]}));
      else if(body.action==='rename')folders=folders.map(f=>f.id===1?{...f,name:body.name}:f);
      return {ordered:true};}});
  return {board,node,context,posts,opens,fail:v=>{fail=v;},current:v=>{current=v;},delay:()=>{release=true;},release:()=>release()};
}
async function run(){
  const app=fixture();await app.board.load();
  let tabs=app.node('following-folders').querySelectorAll('.folder-tab');assert.equal(tabs.length,2);
  const stableTab=tabs[0],stableSelect=stableTab.querySelectorAll('.folder-select')[0];
  app.context.location.search='?folder=1';await app.board.load();
  assert.equal(app.node('following-folders').querySelectorAll('.folder-tab')[0],stableTab,'Selecting a folder must preserve the stable controls');
  assert.equal(stableSelect.getAttribute('aria-pressed'),'true');
  assert.equal(app.node('following-cards').children[0].children[0].draggable,true,'Custom folder cards must be draggable');
  app.context.location.search='';await app.board.load();
  const dots=tabs[0].querySelectorAll('.folder-menu-button')[0],menu=tabs[0].querySelectorAll('.folder-actions')[0];
  assert.equal(dots.textContent,'⋮','Dots are vertical');
  menu.open=true;
  const outside=app.context.document.listeners.get('click');assert.equal(typeof outside,'function');
  outside({target:dots});assert.equal(menu.open,true,'Internal menu click remains open');
  outside({target:tabs[0].querySelectorAll('.folder-select')[0]});assert.equal(menu.open,false,'Name click closes menu');
  menu.open=true;outside({target:new Node()});assert.equal(menu.open,false,'Any external click closes menu');
  await tabs[0].querySelectorAll('.folder-menu-button')[0].listeners.get('click')(event());
  assert.equal(app.opens.length,0,'Three dots must not switch classification');
  app.fail(true);await tabs[0].querySelectorAll('.folder-next')[0].listeners.get('click')(event());
  assert.match(app.node('following-status').textContent,/disk failure/);
  assert.equal(app.node('following-folders').querySelectorAll('.folder-tab')[0].getAttribute('data-folder-id'),'1','Failed order keeps the existing order');
  app.fail(false);await tabs[0].querySelectorAll('.folder-next')[0].listeners.get('click')(event());
  assert.deepEqual(JSON.parse(JSON.stringify(app.posts.at(-1).body)),{ids:[2,1]});
  assert.equal(app.node('following-folders').querySelectorAll('.folder-tab')[0].getAttribute('data-folder-id'),'2');
  assert.equal(app.node('following-cards').children[0].children[0].draggable,false,'All followed projects cannot drag');
  app.context.location.search='?folder=unfiled';await app.board.load();
  const card=app.node('following-cards').children[0].children[0];assert.equal(card.draggable,true);
  assert.equal(app.node('following-cards').querySelectorAll('.following-move').length,0,'Remove card-bottom move buttons');
  card.listeners.get('dragstart')(event());
  tabs=app.node('following-folders').querySelectorAll('.folder-tab');await tabs[0].listeners.get('drop')(event());
  assert.equal(app.posts.at(-1).body.action,'move_unfiled');assert.equal(app.posts.at(-1).body.folder_id,2);
  assert.equal(app.node('following-cards').children.length,0,'Successful move exits unfiled without unfollowing');
  app.context.location.search='';await app.board.load();
  app.node('following-query').value='draft';app.fail(true);
  await app.node('folder-new').listeners.get('click')();app.node('folder-name').value='Keep me';
  await app.node('folder-editor-form').listeners.get('submit')(event());
  assert.equal(app.node('folder-name').value,'Keep me');assert.equal(app.node('folder-dialog').open,true);
  assert.match(app.node('folder-feedback').textContent,/disk failure/);
  const late=fixture();late.delay();const pending=late.board.load();late.current(false);late.board.invalidate();late.release();await pending;
  assert.equal(late.node('following-cards').children.length,0,'Late load cannot render after leaving following');
  console.log('Following board menu, persistence intent, failure, drag scope and stale response tests passed');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
