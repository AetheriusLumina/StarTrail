const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({window:{},Promise});vm.runInContext(fs.readFileSync(__dirname+'/../github_radar/web_assets/card_transition.js','utf8'),context);
async function run(){
let enabled=true,shots=0,updates=0;
const document={documentElement:{dataset:{},style:{}},startViewTransition(){shots++;throw Error('Must not take snapshots');}};
const c=context.window.RadarCardTransition.create({document,enabled:()=>enabled});
const node={style:{},getBoundingClientRect(){return {width:120,height:40};},focus(){this.focused=true;}};
await c.run({from:{frame:node},to:()=>({frame:node}),update:()=>updates++});assert.equal(updates,1);assert.equal(node.focused,true);assert.equal(shots,0,'Missing Web Animations directly shows final content');
let end,cancels=0,reveals=0;node.animate=()=>{reveals++;return {finished:new Promise(r=>end=r),cancel(){cancels++;end();}};};
c.reveal(node);c.reveal(node);assert.equal(cancels,1);enabled=false;c.cancel();assert.equal(cancels,2);c.reveal(node);assert.equal(reveals,2);
await c.run({from:{frame:node},to:()=>({frame:node}),update:()=>updates++});assert.equal(updates,2);assert.equal(reveals,2,'Reduced motion bypasses animation');
await assert.rejects(c.run({from:{frame:node},to:()=>({frame:node}),update(){throw Error('data failed');}}),/data failed/);
assert.equal(document.documentElement.dataset.cardTransition,undefined);console.log('Live motion fallback, original nodes, feedback cancellation, reduced motion and error propagation passed');}
run().catch(e=>{console.error(e);process.exitCode=1;});
