const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {execFileSync}=require('node:child_process');
class Node {
  constructor(tag='SPAN',text=''){this.tagName=tag.toUpperCase();this.nodeType=tag==='#text'?3:1;this.value=text;this.childNodes=[];this.parentNode=null;this.attrs={};this.isConnected=true;}
  get textContent(){return this.nodeType===3?this.value:this.childNodes.map(n=>n.textContent).join('');}
  set textContent(v){this.replaceChildren(new Node('#text',v));}
  append(...nodes){for(const n of nodes){n.parentNode=this;this.childNodes.push(n);}}
  replaceChildren(...nodes){this.childNodes=[];this.append(...nodes);}
  setAttribute(k,v){this.attrs[k]=v;}
  hasAttribute(k){return k in this.attrs;}
  matches(s){return s==='[data-translate]'?'data-translate' in this.attrs:s==='[data-translate-link]'?'data-translate-link' in this.attrs:false;}
  closest(s){for(let n=this;n;n=n.parentNode){if(s==='[hidden]'&&n.hidden)return n;if(s==='pre,code'&&['PRE','CODE'].includes(n.tagName))return n;}return null;}
  querySelectorAll(s){return this.childNodes.flatMap(n=>[...(s.split(',').some(t=>n.matches(t))?[n]:[]),...n.querySelectorAll(s)]);}
}
const context={window:{},document:{createTextNode:t=>new Node('#text',t)},URL,setTimeout,TextEncoder,
  element:(tag,cls,text)=>{const n=new Node(tag);if(text)n.textContent=text;return n;},translatable:n=>{n.setAttribute('data-translate','');return n;},tr:s=>s};
vm.createContext(context);
const source=fs.readFileSync(path.join(__dirname,'../github_radar/web_assets/app.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function safeReadmeUrl('),source.indexOf('let readmeRenderedKey=')),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../github_radar/web_assets/translation.js'),'utf8'),context);
const fixtures=['# Intro\n\nUse `npm install` and [Guide](./guide.md).\n\n| Name | Use |\n| --- | --- |\n| A | Run |\n\n```sh\nnever translate command\n```',
  'Long prose. '.repeat(1000),'😀'.repeat(3000)+' English ending.',
  '- Use `` code ` literal `` safely\n- [bad](javascript:alert) and ![image](https://example.com/a.png)'];
(async()=>{
  for(const text of fixtures){
    const base='https://github.com/owner/repo/blob/main/README.md',root=new Node('MAIN'),requests=[];
    context.markdownNodes(text,root,base);
    const controller=context.window.RadarTranslation.create({post:async(_,payload)=>{requests.push(...payload.items.map(i=>i.parts));return {status:'ready',items:payload.items.map(i=>({...i,status:'original'}))};},api:async()=>{},getLanguage:()=> 'zh',onStatus:()=>{}});
    await controller.refresh(root);
    const expected=JSON.parse(execFileSync(process.env.RADAR_TEST_PYTHON||'python',['-X','utf8','-c',
      'import sys,json;from dataclasses import asdict;from github_radar.project_translation import markdown_items;d=json.load(sys.stdin);print(json.dumps([[asdict(p) for p in i.parts] for i in markdown_items(d["text"],d["base"],"zh")],ensure_ascii=True))'],
      {cwd:path.join(__dirname,'..'),input:JSON.stringify({text,base}),encoding:'utf8'}));
    assert.deepEqual(JSON.parse(JSON.stringify(requests)),expected,'Prewarm cache keys must match real Markdown DOM translation requests');
  }
  console.log('Markdown pretranslation cache contract passed: paragraphs, long Unicode, tables, code and links');
})().catch(e=>{console.error(e);process.exitCode=1;});
