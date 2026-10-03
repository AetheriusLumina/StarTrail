const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{constructor(x=0,y=0,w=120,h=40){this.rect={left:x,top:y,width:w,height:h};this.style={};this.hidden=false;this.isConnected=true;this.animations=[];this.classList={add(){},remove(){}};}getBoundingClientRect(){return this.rect;}focus(){this.focused=true;}append(node){this.child=node;}remove(){this.removed=true;}setAttribute(){}animate(frames,options){let end;const done=new Promise(r=>end=r);const a={frames,options,finished:done,cancel(){this.cancelled=true;end();},end};this.animations.push(a);return a;}}
function setup(){let enabled=true,shots=0;const nodes=[],document={documentElement:{dataset:{},style:{setProperty(){}}},createElement(){const n=new Node();nodes.push(n);return n;},startViewTransition(){shots++;throw Error('Raster snapshots must not be used');}};
const context=vm.createContext({window:{},Promise});vm.runInContext(fs.readFileSync(__dirname+'/../github_radar/web_assets/card_transition.js','utf8'),context);
return {c:context.window.RadarCardTransition.create({document,enabled:()=>enabled,setTimer:setTimeout,clearTimer:clearTimeout}),document,nodes,shots:()=>shots,setEnabled:v=>enabled=v};}
async function tick(){for(let i=0;i<8;i++)await Promise.resolve();}
async function run(){
const css=fs.readFileSync(__dirname+'/../github_radar/web_assets/app.css','utf8');
assert.ok(css.includes('html[data-motion-pending="growth-results"] #keyword-results'),'Growth-to-keyword preparation hides the incoming keyword results');
assert.ok(css.includes('html[data-motion-pending="keyword-results"] #growth-results'),'Keyword-to-growth preparation hides the incoming growth results');
const appSource=fs.readFileSync(__dirname+'/../github_radar/web_assets/app.js','utf8');
const filterSource=appSource.slice(appSource.indexOf('function hasFeedbackContentChange('),appSource.indexOf("if(typeof MutationObserver!=='undefined'&&document.body)"));
const filter=vm.runInNewContext(filterSource+';hasFeedbackContentChange');
assert.equal(filter({type:'childList',addedNodes:[],removedNodes:[{textContent:'',classList:{contains:()=>true}}]}),false,'Removing a decorative morph surface cannot trigger a second result animation');
assert.equal(filter({type:'childList',addedNodes:[{textContent:'New content'}],removedNodes:[]}),true);
assert.equal(filter({type:'characterData'}),true);
const s=setup(),old=new Node(0,90),next=new Node(0,0);let updates=0;
const pending=s.c.run({kind:'page',from:{frame:old,page:old},to:()=>({frame:next,page:next}),update:()=>updates++});await tick();
assert.equal(next.animations.length,1,'Fade the actual page node, not a browser screenshot');assert.equal(s.shots(),0);assert.equal(updates,1);assert.equal(next.animations[0].options.duration,350);
next.animations[0].end();await pending;assert.equal(next.focused,true);assert.equal(s.document.documentElement.dataset.pageTransition,undefined);
const feedback=new Node();s.c.reveal(feedback);assert.equal(feedback.animations[0].frames[0].transform,undefined,'In-place feedback must not shift surrounding content');feedback.animations[0].end();await tick();
const titleOld=new Node(10,100),titleNew=new Node(30,30),article=new Node(0,0,600,700);const open=s.c.run({from:{frame:old,title:titleOld},to:()=>({frame:article,title:titleNew}),update:()=>updates++});await tick();
assert.equal(titleNew.animations.length,1,'Move the real title while glass expands');assert.match(titleNew.animations[0].frames[0].transform,/-20px.*70px/);assert.equal(s.shots(),0);
s.c.cancel({complete:true});await open;assert.equal(updates,2);assert.ok(titleNew.animations[0].cancelled);assert.equal(s.document.documentElement.dataset.cardTransition,undefined);
let release;const stale=s.c.run({kind:'page',from:{frame:old,page:old},to:()=>({frame:next,page:next}),update:()=>new Promise(r=>release=r)});await tick();s.c.cancel();release();await stale;assert.equal(next.animations.length,1,'Late result never starts another animation');
s.setEnabled(false);await s.c.run({from:{frame:old},to:()=>({frame:next}),update:()=>updates++});assert.equal(next.animations.length,1);assert.equal(updates,3);
await assert.rejects(s.c.run({from:{frame:old},to:()=>({frame:next}),update:()=>{throw Error('failed');}}),/failed/);assert.equal(s.document.documentElement.dataset.cardTransition,undefined);
console.log('Live motion: real nodes, 350ms, geometry, single update, interruption, stale result and reduced motion passed');}
run().catch(e=>{console.error(e);process.exitCode=1;});
