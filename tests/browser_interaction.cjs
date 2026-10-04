const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {execFileSync} = require('node:child_process');

const appSource = fs.readFileSync(process.env.RADAR_TEST_APP_SOURCE || path.join(__dirname, "../github_radar/web_assets/app.js"), "utf8");
const i18nPath = path.join(__dirname, "../github_radar/web_assets/i18n.js");
const i18nSource = fs.existsSync(i18nPath) ? fs.readFileSync(i18nPath, "utf8") : "";

class FakeNode {
  constructor(id = "", ownerDocument = null) {
    this.id = id;
    this.ownerDocument = ownerDocument;
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.children = [];
    this.listeners = new Map();
    this.value = "";
    this.style={};this.isConnected=true;
    this.animations=[];
  }
  get firstChild() { return this.children[0] || null; }
  get pathname() { return this.href ? new URL(this.href, "http://127.0.0.1:12345").pathname : ""; }
  append(...items) { for(const item of items)item.parentNode=this; this.children.push(...items); }
  prepend(item){item.parentNode=this;this.children.unshift(item);}
  remove(){this.parentNode?.removeChild(this);}
  replaceChildren(...items){this.children=[];this.append(...items);}
  removeChild(item) { this.children.splice(this.children.indexOf(item), 1); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  setAttribute(name, value) { this[name] = value; }
  getAttribute(name) { return this[name]; }
  showModal(){this.open=true;}
  close(){this.open=false;}
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  animate(frames,options){const animation={frames,options,finished:Promise.resolve(),cancel(){this.cancelled=true;}};this.animations.push(animation);return animation;}
  querySelector(selector) { return selector === "button" ? new FakeNode("submit") : this.querySelectorAll(selector)[0]||null; }
  getBoundingClientRect(){return {width:this.hidden?0:120,height:this.hidden?0:40};}
  querySelectorAll(selector) {
    const name = selector.startsWith(".") ? selector.slice(1) : selector;
    const result = [];
    for (const child of this.children) {
      if ((child.className || "").split(" ").includes(name)) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
}

function scenario({ detail = false, initialPath = null, queuedFrames = false, removedDetailModules = false } = {}) {
  const nodes = new Map();
  const document = {
    listeners: new Map(), addEventListener(name, callback) { const previous=this.listeners.get(name);this.listeners.set(name,previous?(...args)=>{previous(...args);callback(...args);}:callback); },
    activeElement: null,
    documentElement: {lang: "zh-CN", dataset: {}, style: {setProperty(name, value) { this[name] = value; }}},
    getElementById: (id) => removedDetailModules && ['readme-full-toggle','readme-content','readme-original','readme-saved','detail-ai-source','source-evidence-load','source-evidence-content'].includes(id) ? null : node(id),
    createElement: (tag) => { const item = new FakeNode("", document); item.tagName = tag.toUpperCase(); return item; },
    querySelectorAll: (selector) => [...nodes.values()].flatMap((item) => item.querySelectorAll(selector)),
  };
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, new FakeNode(id, document));
    return nodes.get(id);
  };
  const timers = new Map();
  const frames = new Map();
  let nextTimer = 0;
  let healthAlive = true;
  let closeCalls = 0;
  let historyEntries = 1;
  let issue = {
    local_date: "2026-09-26", updated_at: "2026-09-26T09:00:00+08:00",
    status: "cached", busy: false, stale: true, message: "显示上次保存的结果",
    notes: [], coverage: null, keywords: [], keyword_groups: [],
    sections: [{id: "growth", cards: []}, {id: "keyword", cards: []}],
  };
  const location = {host: "127.0.0.1:12345", hash: "#token=test-token", search: "", pathname: initialPath || (detail ? "/project/1" : "/")};
  const requests = [];
  const translationCalls=[];
  let delayTranslation=false, releaseTranslation;
  const historyDates = [{date: "2026-09-27", count: 1}, {date: "2026-09-26", count: 1}];
  let settingsKeywords = [{id: 1, term: "MCP", enabled: true, min_stars: 1000}];
  let preferences = {language: "zh", font_size: "normal", font_scale: 1};
  let failPreference = false, delayPreference = false, releasePreference = null;
  let autoUpdate = {enabled: true, time: "09:00", registered: true,
    task_error: "", last_success_date: "2026-09-27", last_success_at: "2026-09-27T09:00:00+08:00",
    last_attempt: {attempts: 1, last_attempt_at: "2026-09-27T09:00:00+08:00", status: "success", reason: null}};
  let failNextSchedule = false;
  let confirmAnswer = true;
  let readme = {status:'empty', document:null, sections:[], selected_markdown:'', reason:null};
  let delayedReadme = false, releaseReadme;
  let detailFollowed=false;
  let backend=null;
  const historyCard = {repo_id: 1, history_date: "2026-09-27", title: "owner/repo", description: "Example", stars: "★ 900",
    growth: "+3 Star", growth_rank: 1, source: "近期 Star 增长", tags: []};
  const fetch = async (route, options = {}) => {
    requests.push(route);
    if(backend){const result=await backend(route,options);if(result)return result;}
    if(route==='/api/following/1'&&options.method==='POST')detailFollowed=JSON.parse(options.body).followed;
    if (route.startsWith('/api/readme/')) {
      const current = readme;
      if (delayedReadme) { delayedReadme = false; await new Promise(resolve => { releaseReadme = resolve; }); }
      if (options.method === 'POST') readme = {...readme, status:readme.document?'ready':'missing'};
      return {ok:true,status:200,json:async()=>current};
    }
    if (route === "/api/keywords/1/delete") {
      settingsKeywords = [];
      issue = {...issue, keywords: [], keyword_groups: [],
        sections: [{id: "growth", cards: []}, {id: "keyword", cards: []}]};
      return {ok: true, status: 200, json: async () => ({deleted: true})};
    }
    if (route === "/api/preferences" && options.method === "POST") {
      if (delayPreference) { delayPreference = false; await new Promise(resolve => { releasePreference = resolve; }); }
      if (failPreference) { failPreference = false; return {ok: false, status: 500, json: async () => ({error: "保存失败"})}; }
      preferences = {...preferences, ...JSON.parse(options.body)};
      return {ok: true, status: 200, json: async () => preferences};
    }
    if (route === "/api/auto-update" && options.method === "POST") {
      if (failNextSchedule) {
        failNextSchedule = false;
        return {ok: false, status: 503, json: async () => ({error: "Windows 定时任务未能保存，请检查权限后重试"})};
      }
      autoUpdate = {...autoUpdate, ...JSON.parse(options.body), registered: true};
      return {ok: true, status: 200, json: async () => autoUpdate};
    }
    if (route === "/api/keywords/1/settings") {
      settingsKeywords[0] = {...settingsKeywords[0], ...JSON.parse(options.body)};
      return {ok: true, status: 200, json: async () => ({keyword: settingsKeywords[0]})};
    }
    if (route === "/api/health" && !healthAlive) throw new Error("connection refused");
    const data = route === "/api/issue" ? issue
      : route.startsWith('/api/history/calendar?') ? {month:new URLSearchParams(route.split('?')[1]).get('month'),dates:historyDates}
      : route === "/api/history" || route.startsWith('/api/history?') ? {dates: historyDates}
      : route.startsWith("/api/history/") ? {date: route.split("/").at(-1),
        sections: [{id: "growth", cards: [historyCard]}, {id: "keyword", cards: []}], keyword_groups: [],
        coverage: {candidate_count: 42, scored_count: 17, stat_date: "2026-09-26",
          source_names: ["search"], metric_basis: "github_daily_new"}}
      : route === "/api/following" ? {cards: [{...historyCard, saved_at: "2026-09-27T08:00:00+08:00"}]}
      : route === "/api/settings" ? {keywords: settingsKeywords}
      : route === "/api/auto-update" ? autoUpdate
      : route === "/api/preferences" ? preferences
      : route === "/api/following/1" ? {followed: true}
      : route.startsWith("/api/project/1") ? {
        title: "owner/repo", description: "Description", source: "Star 增长",
        stars: "★ 1,000", growth: "+3 Star", observation: "GitHub 统计日：2026-09-26", language: "Python",
        growth_rank: 1, matched_keywords: ["MCP", "<script>"],
        tags: [], url: "https://github.com/owner/repo", followed: detailFollowed,
        insight: {summary: "Description", relevance: "未设置关键词", evidence: []},
        ai_explanation: {model_id: "gpt-6-luna", saved_at: "2026-09-27T09:00:00",
          relevance: "relevant", evidence: [],
          zh: {summary: "中文解读", purpose: "中文用途", scenarios: "场景", users: "用户", highlights: []},
          en: {summary: "English explanation", purpose: "English purpose", scenarios: "Cases", users: "Users", highlights: []}},
      }
      : route === "/api/quit" ? {status: "quitting"}
      : route === "/api/refresh" ? {busy: true}
      : {status: "ok"};
    return {ok: true, status: route === "/api/quit" ? 202 : 200, json: async () => data};
  };
  const context = vm.createContext({
    document,
    location, URL, URLSearchParams, fetch,
    sessionStorage: {getItem: () => "test-token", setItem: () => {}},
    history: {state: null, replaceState: (_state, _title, path) => {
      const value = new URL(path, "http://127.0.0.1:12345");
      location.pathname = value.pathname; location.search = value.search;
    }, pushState: (_state, _title, path) => {
      historyEntries++;
      const value = new URL(path, "http://127.0.0.1:12345");
      location.pathname = value.pathname; location.search = value.search;
    }},
    window: {RadarTranslation:{create:()=>({refresh:async(root)=>{translationCalls.push(['refresh',root.id]);if(delayTranslation){delayTranslation=false;await new Promise(r=>releaseTranslation=r);}},
      prepare(root){return this.refresh(root);},adopt(from,to){to.children=[];to.append(...from.children);to.textContent=from.textContent;},
      invalidate:()=>translationCalls.push(['invalidate']),close:()=>translationCalls.push(['close']),
      setOriginal:value=>translationCalls.push(['original',value])})}, confirm: () => confirmAnswer, close: () => {
      if (historyEntries === 1) closeCalls++;
    },
      scrollY: 0, scrollTo: (_x, y) => { context.window.scrollY = y; },
      listeners: new Map(), addEventListener(name, callback) { const previous=this.listeners.get(name);this.listeners.set(name,previous?(...args)=>{previous(...args);callback(...args);}:callback); },
      matchMedia: () => ({matches: true, addEventListener() {}})},
    requestAnimationFrame: (callback) => { if (!queuedFrames) return callback(); const id = ++nextTimer; frames.set(id, callback); return id; },
    cancelAnimationFrame: (id) => frames.delete(id),
    setTimeout: (callback) => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
    console,AbortController,
  });
  if (i18nSource) vm.runInContext(i18nSource, context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../github_radar/web_assets/history_following.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../github_radar/web_assets/history_calendar.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../github_radar/web_assets/following_board.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../github_radar/web_assets/card_transition.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../github_radar/web_assets/detail_classification.js'),'utf8'),context);
  vm.runInContext(appSource, context);
  return {
    node, context,
    setIssue: (value) => { issue = {...issue, ...value}; },
    stopHealth: () => { healthAlive = false; },
    closeCalls: () => closeCalls,
    requests: () => requests,
    translationCalls:()=>translationCalls,
    delayTranslation:()=>{delayTranslation=true;},releaseTranslation:()=>releaseTranslation(),
    backend:value=>{backend=value;},
    setConfirm: (value) => { confirmAnswer = value; },
    failNextSchedule: () => { failNextSchedule = true; },
    failNextPreference: () => { failPreference = true; },
    delayNextPreference: () => { delayPreference = true; },
    releasePreference: () => releasePreference(),
    preferences: () => preferences,
    frames,
    paint: () => { for (const [id, callback] of [...frames]) { frames.delete(id); callback(); } },
    setReadme: (value) => { readme = value; },
    delayReadme: () => { delayedReadme = true; },
    releaseReadme: () => releaseReadme(),
    tick: () => { const [id, callback] = timers.entries().next().value || []; if (callback) { timers.delete(id); callback(); } },
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

async function testDetailWaitsForPreparedReadmeAndTranslation() {
  const app=scenario();await flush();
  app.setReadme({status:'ready',document:{source_url:'https://github.com/owner/repo/blob/main/README.md',observed_at:'today'},sections:[{kind:'purpose',text:'Real author purpose'}]});
  app.context.location.pathname='/project/1';app.delayTranslation();
  let completed=false;
  const work=vm.runInContext("showDetail('1')",app.context).then(()=>{completed=true;});
  await flush();await flush();
  assert.equal(completed,false,'Card animation must wait until cached author content is translated');
  assert.ok(app.node('readme-purpose').children.length,'README is prepared before the final translation barrier');
  app.releaseTranslation();await work;assert.equal(completed,true);
}

async function testReadmeReplacementWaitsWithoutChangingVisibleText() {
  const app=scenario();await flush();app.context.location.pathname='/project/1';
  const old={status:'ready',document:{repo_id:1,source_url:'https://github.com/owner/repo/blob/main/README.md',observed_at:'today',content_hash:'old'},sections:[{kind:'purpose',text:'Old translated purpose'}]};
  app.context.old=old;vm.runInContext('renderReadme(old)',app.context);await flush();const original=app.node('readme-purpose').children[0];
  app.setReadme({...old,document:{...old.document,content_hash:'new'},sections:[{kind:'purpose',text:'New English purpose'}]});
  app.delayTranslation();const work=vm.runInContext("loadReadme('1')",app.context);await flush();await flush();
  assert.equal(app.node('readme-purpose').children[0],original,'Existing translated purpose stays visible until new translation is ready');
  app.releaseTranslation();await work;assert.notEqual(app.node('readme-purpose').children[0],original);
}

async function testDelayedExit() {
  const app = scenario();
  await flush();
  const quit = vm.runInContext("quitApp()", app.context);
  await flush();
  assert.equal(app.node("status-text").textContent, "正在结束更新…",
    "Exit must not claim completion while the local server still answers health checks");
  app.tick();
  await flush();
  assert.equal(app.node("status-text").textContent, "正在结束更新…");
  assert.equal(app.closeCalls(), 0, "The tab must stay open while shutdown is pending");
  app.stopHealth();
  app.tick();
  await quit;
  assert.equal(app.closeCalls(), 1, "Exit should ask the browser to close the app tab");
  assert.equal(app.node("status-text").textContent, "已退出 StarTrail，可以关闭这个标签页。");
}

async function testExitAfterInternalNavigation() {
  const app = scenario();
  await flush();
  vm.runInContext("navLink({button: 0, preventDefault() {}, currentTarget: {pathname: '/settings'}})", app.context);
  await flush();
  assert.equal(app.context.location.pathname, "/settings");
  const quit = vm.runInContext("quitApp()", app.context);
  await flush();
  app.stopHealth();
  app.tick();
  await quit;
  assert.equal(app.closeCalls(), 1,
    "Exit should still close the tab after visiting another Radar page");
}

async function testDetailStatusForUpdateAndExit() {
  const app = scenario({detail: true});
  await flush();
  app.setIssue({status: "error", message: "当前无法连接 GitHub"});
  await vm.runInContext("startRefresh()", app.context);
  assert.equal(app.node('update-status').textContent,'当前无法连接 GitHub');
  assert.equal(app.node('update-status').hidden,false,'Failure must be visible in the shared sidebar');
  assert.equal(app.node("detail-status-text").textContent, "当前无法连接 GitHub",
    "Update failure on detail page must be visible there");
  const quit = vm.runInContext("quitApp()", app.context);
  await flush();
  assert.equal(app.node("detail-status-text").textContent, "正在结束更新…",
    "Detail page must show pending exit");
  app.stopHealth();
  app.tick();
  await quit;
  assert.equal(app.node("detail-status-text").textContent,
    "已退出 StarTrail，可以关闭这个标签页。");
}

async function testRefreshAcknowledgementTimeoutAllowsRetry() {
  const app=scenario();await flush();
  app.backend((route,options)=>route==='/api/refresh'?new Promise((resolve,reject)=>{
    options.signal?.addEventListener('abort',()=>reject(Object.assign(new Error('abort'),{name:'AbortError'})));
  }):null);
  let settled=false;
  const update=vm.runInContext('startRefresh()',app.context).then(()=>{settled=true;});
  await flush();
  for(let i=0;i<8&&!settled;i++){app.tick();await flush();}
  assert.equal(settled,true,'Unresponsive local acknowledgement must not leave update disabled indefinitely');
  await update;
  assert.equal(app.node('refresh-button').disabled,false);
  assert.match(app.node('update-status').textContent,/响应超时/);
  app.backend(null);await vm.runInContext('startRefresh()',app.context);
  assert.equal(app.node('refresh-button').disabled,false);
}

async function testGroupedAIState() {
  const app = scenario();
  await flush();
  const groups = [
    {keyword_id: 1, term: "local ai", cards: [], checked_count: 20, ai_status: "error", ai_message: "额度不足"},
    {keyword_id: 2, term: "design", cards: [], checked_count: 0, ai_status: "idle", ai_message: ""},
  ];
  app.setIssue({keywords: [{id: 1, term: "local ai"}, {id: 2, term: "design"}], keyword_groups: groups});
  await vm.runInContext("loadIssue()", app.context);
  assert.equal(app.node("keyword-groups").querySelectorAll(".keyword-group").length, 2);
  assert.equal(app.node("keyword-count").textContent, "0 个项目");
}

async function testFullVerifiedKeywordGroupHasNoUselessAction() {
  const app = scenario();
  await flush();
  vm.runInContext('aiConnection = {...aiConnection, ready:true}',app.context);
  const cards = [1,2,3,4,5].map(i => ({repo_id:i,title:`owner/repo-${i}`,
    stars:'★ 1,000',description:'example',tags:[],growth:'',source:'关键词：ai',ai_status:'relevant',
    display_rank:i,rank_source:'keyword'}));
  const group = {keyword_id:1,term:'ai',cards,checked_count:20,ai_status:'checked'};
  app.setIssue({keywords:[{id:1,term:'ai'}],keyword_groups:[group]});
  await vm.runInContext('loadIssue()',app.context);
  const root = app.node('keyword-groups');
  assert.equal(root.querySelectorAll('.ai-refine-button')[0].hidden,true,
    'A verified full group must hide an action that cannot do anything');
  assert.doesNotMatch(root.querySelectorAll('.ai-group-status')[0].textContent,/继续查找/);
  assert.equal(root.querySelectorAll('.card-rank').length,5);
  assert.match(root.querySelectorAll('.card-headline')[0].className,/rank-1/);
  app.setIssue({keyword_groups:[{...group,cards:cards.slice(0,3)}]});
  await vm.runInContext('loadIssue()',app.context);
  assert.equal(root.querySelectorAll('.ai-refine-button')[0].hidden,false);
  assert.equal(root.querySelectorAll('.ai-refine-button')[0].disabled,false);
}

async function testKeywordUsesSavedNoncontiguousRanksAndSameDetailTitle() {
  const app = scenario();
  await flush();
  const card = vm.runInContext(`cardNode({repo_id:7,title:'owner/repo',description:'',tags:[],
    stars:'★ 1,000',growth:'',display_rank:8,rank_source:'keyword',growth_rank:null})`,app.context);
  assert.equal(card.querySelectorAll('.card-rank')[0]?.textContent,'8');
  assert.match(card.querySelectorAll('.card-headline')[0].className,/rank-other/);
}

async function testGroupButtonKeepsFocusWhenCardsChange() {
  const app = scenario();
  await flush();
  vm.runInContext("aiConnection.ready = true", app.context);
  const first = {keyword_id: 1, term: "local ai", cards: [], checked_count: 0,
    ai_status: "idle", ai_message: ""};
  app.setIssue({keywords: [{id: 1, term: "local ai"}], keyword_groups: [first]});
  await vm.runInContext("loadIssue()", app.context);
  const previous = app.node("keyword-groups").querySelectorAll(".ai-refine-button")[0];
  previous.focus();
  app.setIssue({keyword_groups: [{...first, cards: [{repo_id: 7, title: "owner/repo",
    description: "example", stars: "★ 1,000", growth: "", observation: "",
    source: "关键词：local ai", tags: [], ai_status: "relevant"}]}]});
  await vm.runInContext("loadIssue()", app.context);
  const current = app.node("keyword-groups").querySelectorAll(".ai-refine-button")[0];
  assert.notEqual(current, previous);
  assert.equal(app.context.document.activeElement, current,
    "Rebuilding cards must return keyboard focus to the same keyword action");
}

async function testDetailSwitchClearsAIText() {
  const app = scenario({detail: true});
  await flush();
  app.node("detail-ai-problem").textContent = "old project explanation";
  await vm.runInContext("showDetail('2')", app.context);
  assert.equal(app.node("detail-ai-problem").textContent, "待生成项目理解");
}

async function testGrowthRankAndKeywordLabelsAreText() {
  const app = scenario();
  await flush();
  const card = vm.runInContext(`cardNode({repo_id: 1, title: "owner/repo",
    source: "近期 Star 增长", description: "Example", stars: "★ 1,000",
    growth: "新增 25 Star", growth_rank: 1, observation: "GitHub 统计日：2026-09-26",
    matched_keywords: ["MCP", "<script>"], tags: []})`, app.context);
  assert.equal(card.querySelectorAll(".card-rank")[0].textContent, "1");
  assert.equal(card.querySelectorAll(".card-observation").length, 0,
    'Growth cards retain numeric metrics without a statistic-date row');
  assert.equal(card.querySelectorAll(".card-keyword-match")[0].textContent,
    "基础匹配：MCP、<script>");
  const pending = vm.runInContext(`cardNode({repo_id: 2, title: "owner/pending",
    source: "近期 Star 增长", description: "Example", stars: "★ 1,000",
    growth: "正在建立增长记录", growth_rank: null,
    matched_keywords: [], tags: []})`, app.context);
  assert.equal(pending.querySelectorAll(".card-rank").length, 0);
  const detailApp = scenario({detail: true});
  await flush();
  await vm.runInContext("showDetail('1')", detailApp.context);
  assert.equal(detailApp.node("detail-rank").textContent, 1);
  assert.equal(detailApp.node("detail-tags").querySelectorAll(".detail-keyword-match")[0].textContent,
    "基础匹配：MCP、<script>");
}

async function testHistoryGroupsAndFollowingRoute() {
  const app = scenario();
  await flush();
  app.context.location.pathname = "/history";
  await vm.runInContext("route()", app.context);
  await flush();
  await vm.runInContext("historyCalendar.choose('2026-09-27')", app.context);
  await flush();
  const groups = app.node("history-date-groups").querySelectorAll(".history-date-group");
  assert.equal(groups.length, 1);
  assert.equal(groups[0].querySelectorAll(".history-project-grid").length, 1);
  assert.equal(groups[0].querySelectorAll(".history-date-toggle").length, 0);
  app.context.window.scrollY = 240;
  const card = groups[0].querySelectorAll(".project-card")[0];
  card.listeners.get("click")({button: 0, preventDefault() {}});
  await flush();
  assert.equal(app.context.location.pathname, "/project/1");
  assert.equal(app.context.location.search, "?date=2026-09-27");
  assert.equal(app.node("back-link").href, "/history?day=2026-09-27");
  app.node("back-link").listeners.get("click")({button: 0, preventDefault() {}});
  await flush();
  assert.equal(app.context.location.pathname, "/history");
  assert.equal(app.context.window.scrollY, 240);
  app.context.location.pathname = "/";
  await vm.runInContext("route()", app.context);
  app.setIssue({keywords: [{id: 1, term: "MCP"}]});
  await vm.runInContext("loadIssue()", app.context);
  app.context.location.pathname = "/history";
  await vm.runInContext("route()", app.context);
  assert.equal(app.requests().filter((path) => path === "/api/history").length, 2,
    "History must reload after the saved issue changes");
  app.context.location.pathname = "/following";
  await vm.runInContext("route()", app.context);
  await flush();
  assert.equal(app.node("following-cards").querySelectorAll(".project-card").length, 1);
  const followedCard=app.node('following-cards').querySelectorAll('.project-card')[0];
  assert.ok(followedCard.className.includes('project-card-compact'),'Following uses the same compact repository fields as history');
  assert.equal(followedCard.querySelectorAll('.card-description').length,0);
  assert.equal(followedCard.querySelectorAll('.card-saved-at').length,0);
  assert.equal(followedCard.querySelectorAll('.card-folder-names').length,0);
  assert.equal(app.node("nav-following")["aria-current"], "page");
}

async function testHistoryWithoutDataDoesNotStartGitHubUpdate() {
  const app = scenario({initialPath: "/history"});
  app.setIssue({status: "empty"});
  await vm.runInContext("loadIssue()", app.context);
  assert.equal(app.requests().includes("/api/refresh"), false,
    "Opening history must not start a GitHub request");
  assert.equal(app.requests().includes("/api/ai/status"), false,
    "Opening history must not probe Codex");
}

async function testSettingsKeywordActions() {
  const app = scenario({initialPath: "/settings"});
  await flush();
  const rows = app.node("settings-keywords").querySelectorAll(".keyword-setting-row");
  assert.equal(rows.length, 1);
  const toggle = rows[0].querySelectorAll(".keyword-toggle")[0];
  await toggle.listeners.get("click")();
  assert.equal(toggle["aria-pressed"], "false");
  const input = rows[0].querySelectorAll(".keyword-minimum")[0];
  input.value = "2500";
  await rows[0].querySelectorAll(".keyword-save")[0].listeners.get("click")();
  assert.equal(app.node("settings-feedback").textContent, "最低 Star 已保存。");
  app.setConfirm(false);
  await rows[0].querySelectorAll(".keyword-delete")[0].listeners.get("click")();
  assert.equal(app.requests().includes("/api/keywords/1/delete"), false);
  app.setConfirm(true);
  app.node("keyword-feedback").textContent = "已关注“MCP”，正在寻找项目。";
  await rows[0].querySelectorAll(".keyword-delete")[0].listeners.get("click")();
  assert.equal(app.node("settings-keywords").querySelectorAll(".keyword-setting-row").length, 0);
  assert.equal(app.node("keyword-groups").querySelectorAll(".keyword-group").length, 0);
  assert.equal(app.node("keyword-feedback").textContent, "",
    "Removing a keyword must clear the old home-page success message");
}

async function testDailyScheduleSavesAndRestoresOnFailure() {
  const app = scenario({initialPath: "/settings"});
  await flush();
  await flush();
  assert.equal(app.node("auto-update-enabled").checked, true);
  assert.equal(app.node("auto-update-time").value, "09:00");
  app.node("auto-update-time").value = "10:30";
  await app.node("auto-update-form").listeners.get("submit")({preventDefault: () => {}});
  assert.equal(app.node("auto-update-feedback").textContent, "每日更新已保存。");
  app.failNextSchedule();
  app.node("auto-update-enabled").checked = false;
  app.node("auto-update-time").value = "11:00";
  await app.node("auto-update-form").listeners.get("submit")({preventDefault: () => {}});
  assert.equal(app.node("auto-update-enabled").checked, true);
  assert.equal(app.node("auto-update-time").value, "10:30");
  assert.equal(app.node("auto-update-feedback").textContent.includes("未能保存"), true);
}

async function testBackgroundCompletionAppearsThroughLocalPollingOnly() {
  const app = scenario();
  await flush();
  const before = app.requests().filter((route) => route === "/api/refresh").length;
  app.setIssue({updated_at: "2026-09-28T10:30:00+08:00", local_date: "2026-09-28",
    status: "cached", message: "显示已保存的今日结果"});
  await vm.runInContext("pollSavedIssue()", app.context);
  assert.equal(app.node("saved-time").textContent.includes("2026-09-28"), true);
  assert.equal(app.requests().filter((route) => route === "/api/refresh").length, before);
}

async function testLanguageAndFontChoicePersistsInInterface() {
  const app = scenario();
  await flush();
  await vm.runInContext("setLanguage('en')", app.context);
  assert.equal(app.context.document.documentElement.lang, "en");
  assert.equal(app.node("nav-history").textContent, "History");
  assert.equal(app.node("ai-model").children[0].textContent, "Automatic");
  assert.equal(app.node("ai-connection-status").textContent, "AI connection unavailable");
  assert.equal(app.context.document.title, "StarTrail · Find open source worth following");
  app.setIssue({message: "显示上次保存的结果", notes: ["首次采样：正在建立增长记录"],
    keywords: [{id: 1, term: "MCP"}],
    sections: [{id: "growth", cards: [{repo_id: 1, title: "owner/repo", source: "近期 Star 增长",
      stars: "★ 1,000", tags: [], description: "作者自己的中文描述", growth: "新增 25 Star"}]},
      {id: "keyword", cards: []}],
    keyword_groups: [{keyword_id: 1, term: "MCP", cards: [], checked_count: 0, ai_status: "idle"}]});
  await vm.runInContext("loadIssue()", app.context);
  assert.equal(app.node("status-text").textContent, "Showing the last saved results");
  assert.equal(app.node("growth-count").textContent, "1 project");
  const card = app.node("growth-cards").querySelectorAll(".project-card")[0];
  assert.equal(card.querySelectorAll(".card-source")[0].textContent, "Recent Star growth");
  assert.equal(card.querySelectorAll(".card-keyword-match").length, 0);
  assert.equal(card.querySelectorAll(".card-description")[0].textContent, "作者自己的中文描述");
  assert.equal(card.querySelectorAll(".card-growth")[0].textContent, "+25 new Stars");
  assert.equal(app.node("keyword-groups").querySelectorAll(".ai-refine-button")[0].textContent, "AI review and fill");
  assert.equal(app.node("saved-time").textContent.includes("Saved date:"), true);
  const beforeAI = app.requests().filter((route) => route.includes("/api/ai/projects/")).length;
  app.context.location.pathname = "/project/1";
  await vm.runInContext("showDetail('1')", app.context);
  assert.equal(app.node("detail-ai-users").textContent, "Users");
  assert.equal(app.node("detail-description").textContent, "Description");
  assert.equal(app.requests().filter((route) => route.includes("/api/ai/projects/")).length, beforeAI);
  await vm.runInContext("commitFontScale(1.125)", app.context);
  assert.equal(app.context.document.documentElement.style['--font-scale'], '1.125');
  assert.equal(app.requests().filter((route) => route === "/api/preferences").length > 0, true);
}

async function testContinuousFontPreviewAndSerializedSave() {
  const app = scenario(); await flush();
  const before = app.requests().filter(x => x === '/api/preferences').length;
  app.node('font-scale').value = '1.37';
  app.node('font-scale').listeners.get('input')();
  assert.equal(app.context.document.documentElement.style['--font-scale'], '1.37');
  assert.equal(app.requests().filter(x => x === '/api/preferences').length, before);
  await app.node('font-scale').listeners.get('change')();
  assert.equal(app.preferences().font_scale, 1.37);
  app.delayNextPreference();
  const first = vm.runInContext('commitFontScale(1.2)', app.context); await flush();
  const last = vm.runInContext('commitFontScale(2)', app.context); await flush();
  assert.equal(app.context.document.documentElement.style['--font-scale'], '2');
  app.releasePreference(); await first; await last;
  assert.equal(app.preferences().font_scale, 2);
  app.failNextPreference(); await vm.runInContext('commitFontScale(.8)', app.context);
  assert.equal(app.context.document.documentElement.style['--font-scale'], '2');
  await app.node('font-reset').listeners.get('click')();
  assert.equal(app.preferences().font_scale, 1);
  let prevented = false;
  await app.node('font-scale').listeners.get('keydown')({key:'ArrowRight', preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(app.preferences().font_scale, 1.01, 'Keyboard adjustment must have a perceptible step');
}

async function testCompactReturningGrowthAndSidebar() {
  const app = scenario(); await flush();
  const base = {repo_id: 9, title: 'owner/project', source:'近期 Star 增长', stars:'★ 9,000',
    growth:'+100 Star', growth_rank:8, description:'Author purpose', tags:['Python']};
  app.context.testCard = {...base, display_role:'old'};
  let card = vm.runInContext('cardNode(testCard)', app.context);
  assert.equal(card.querySelectorAll('.card-description').length, 0, 'Returning rows must be compact');
  assert.equal(card.querySelectorAll('.card-rank')[0].textContent, '8');
  assert.equal(card.querySelectorAll('.card-rank')[0].className.includes('rank-other'), true);
  for (const rank of [1,2,3,4,5]) {
    app.context.testCard = {...base, growth_rank:rank};
    const rankedCard = vm.runInContext('cardNode(testCard)', app.context);
    const number = rankedCard.querySelectorAll('.card-rank')[0];
    assert.equal(number.textContent, String(rank));
    assert.equal(number.className.includes(rank < 4 ? `rank-${rank}` : 'rank-other'), true);
    assert.equal(rankedCard.querySelectorAll('.card-headline')[0].className.includes(
      rank < 4 ? `rank-${rank}` : 'rank-other'), true,
      'Rank and title must share the same heading color');
  }
  app.context.testCard = {...base, growth_rank:null};
  const unranked = vm.runInContext('cardNode(testCard)', app.context);
  assert.equal(unranked.querySelectorAll('.card-headline')[0].className.includes('rank-other'), true);
  assert.equal(unranked.querySelectorAll('.card-rank').length, 0);
  assert.equal(card.querySelectorAll('.card-tags').length, 0);
  app.context.testCard = {...base, display_role:'new'};
  card = vm.runInContext('cardNode(testCard)', app.context);
  assert.equal(card.querySelectorAll('.card-description')[0].textContent, 'Author purpose');
  app.setIssue({sections:[{id:'growth',cards:[{...base,display_role:'old'},{...base,repo_id:10,display_role:'new'}]},
    {id:'keyword',cards:[]}]});
  await vm.runInContext('loadIssue()', app.context);
  assert.equal(app.node('growth-returning-cards').children.length, 1);
  assert.equal(app.node('growth-cards').children.length, 1);
  assert.equal(app.node('issue-notes').textContent.includes('目前 1 个'), true, 'Old rows do not fill five new slots');
  app.setIssue({sections:[{id:'growth',cards:[
    {...base,repo_id:1,display_role:'old'}, {...base,repo_id:2,display_role:'old'},
    ...[3,4,5,6,7].map(repo_id=>({...base,repo_id,display_role:'new'}))]},
    {id:'keyword',cards:[]}]});
  await vm.runInContext('loadIssue()', app.context);
  assert.equal(app.node('growth-returning-cards').children.length,2);
  assert.equal(app.node('growth-cards').children.length,5);
  assert.equal(app.node('growth-count').textContent,'5 个项目');
  vm.runInContext("language='en'",app.context);
  app.setIssue({status:'error',message:'旧榜统计日尚不可核实，保留已有项目；新发现补齐待重试'});
  await vm.runInContext('loadIssue()',app.context);
  assert.equal(/[\u4e00-\u9fff]/.test(app.node('status-text').textContent),false,
    'The new compatibility status must follow the selected interface language');
  vm.runInContext('setNavigationOpen(true)', app.context);
  assert.equal(app.node('nav-toggle').getAttribute('aria-expanded'), 'true');
  app.context.window.listeners.get('keydown')({key:'Escape',preventDefault(){}});
  assert.equal(app.node('sidebar').hidden, true);
  assert.equal(app.context.document.activeElement, app.node('nav-toggle'));
  vm.runInContext('setNavigationOpen(true); navLink({button:0,preventDefault(){},currentTarget:{pathname:"/history"}})', app.context);
  await flush();
  assert.equal(app.node('sidebar').hidden, true);
  assert.equal(app.node('nav-history').getAttribute('aria-current'), 'page');
}

async function testUpdatePollingPreservesTranslatedGrowthNodes() {
  const app=scenario();await flush();
  const card={repo_id:1,title:'owner/project',description:'Original description',tags:['Tool'],stars:'★ 2000',growth:'+200 Star'};
  app.setIssue({busy:true,status:'updating',sections:[{id:'growth',cards:[{...card,display_role:'new'},
    {...card,repo_id:2,display_role:'old'}]},{id:'keyword',cards:[]}]});
  await vm.runInContext('loadIssue()',app.context);await flush();
  const fresh=app.node('growth-cards').children[0], returning=app.node('growth-returning-cards').children[0];
  fresh.querySelectorAll('.card-description')[0].textContent='已经翻译的介绍';
  await vm.runInContext('loadIssue()',app.context);await flush();
  assert.equal(app.node('growth-cards').children[0],fresh,'Busy polling must keep the real project nodes');
  assert.equal(app.node('growth-returning-cards').children[0],returning,'Returning cards must also stay stable');
  assert.equal(fresh.querySelectorAll('.card-description')[0].textContent,'已经翻译的介绍');
  app.setIssue({busy:false,status:'ok'});
  await vm.runInContext('loadIssue()',app.context);await flush();
  assert.equal(app.node('growth-cards').children[0],fresh,'Completion alone does not reset unchanged text');
  app.setIssue({sections:[{id:'growth',cards:[{...card,stars:'★ 3000'}]},{id:'keyword',cards:[]}]});
  await vm.runInContext('loadIssue()',app.context);await flush();
  assert.notEqual(app.node('growth-cards').children[0],fresh,'Actual project data changes must appear');
  assert.equal(app.node('growth-cards').children[0].querySelectorAll('.card-metrics')[0].children[0].textContent,'★ 3000');
}

async function testDetailToolsWithoutRemovedModules() {
  const app=scenario({removedDetailModules:true});await flush();
  assert.equal(app.node('detail-tools').hidden,true,'Detail tools stay out of other pages');
  app.context.location.pathname='/project/1';
  await vm.runInContext("showDetail('1')",app.context);await flush();
  assert.equal(app.node('detail-tools').hidden,false,'AI controls live in the detail sidebar');
  assert.equal(app.node('detail-title').textContent,'owner/repo');
  assert.ok(!app.node('detail-metrics').children.some(n=>n.textContent.includes('GitHub 统计日')),'The removed statistical-date text stays out of project details too');
  app.context.author={status:'ready',document:{source_url:'https://github.com/owner/repo#readme',observed_at:'today'},sections:[{kind:'purpose',text:'Original author purpose'}]};
  vm.runInContext('renderReadme(author)',app.context);
  assert.equal(app.node('readme-purpose').children.length,2);
  assert.equal(app.node('readme-purpose').children[0].className,'project-kind','Type is the first purpose line');
  assert.equal(app.node('readme-status').textContent,'','A ready README is not a read-failure');
  assert.ok(!app.requests().some(p=>p.includes('/api/ai/projects/')),'Opening a detail never calls AI');
  await app.node('detail-ai-explain').listeners.get('click')();
  assert.equal(app.requests().filter(p=>p.includes('/api/ai/projects/')).length,1,'Sidebar generation remains manual');
  app.context.location.pathname='/following';await vm.runInContext('route()',app.context);await flush();
  assert.equal(app.node('detail-tools').hidden,true,'Leaving a detail hides its sidebar controls');
}

async function testCalendarSearchMutualExclusionAndCompactHistory() {
  const app=scenario({initialPath:'/history'});await flush();
  assert.equal(app.node('history-calendar').inert,false);
  assert.equal(app.node('history-date-groups').children.length,0,'Browse waits for the selected day');
  await vm.runInContext("historyCalendar.choose('2026-09-27')",app.context);await flush();
  assert.equal(app.context.location.search,'?day=2026-09-27','Selecting a day enters its own history page');
  assert.equal(app.node('history-calendar').hidden,true);
  assert.equal(app.node('history-search-form').hidden,true);
  assert.equal(app.node('history-calendar-back').hidden,false);
  let cards=app.node('history-date-groups').querySelectorAll('.project-card');
  assert.equal(cards.length,1);assert.ok(cards[0].className.includes('project-card-compact'));
  assert.equal(cards[0].querySelectorAll('.card-description').length,0);
  assert.equal(cards[0].querySelectorAll('.card-source').length,0);
  assert.equal(cards[0].querySelectorAll('.card-rank')[0].textContent,'1');
  app.context.location.search='';await vm.runInContext('loadHistory()',app.context);await flush();
  assert.equal(app.node('history-calendar').hidden,false);
  assert.equal(app.node('history-date-groups').querySelectorAll('.project-card').length,0,'Calendar page has no day projects below it');
  app.node('history-query').value='repo';await app.node('history-search-form').listeners.get('submit')({preventDefault(){}});await flush();
  assert.equal(app.node('history-calendar').inert,true);
  assert.equal(app.node('history-year').disabled,true);
  assert.equal(app.node('history-calendar-days').children.filter(n=>n.tagName==='BUTTON').every(n=>n.disabled),true);
  assert.equal(app.node('history-date-groups').querySelectorAll('.project-card').length,2,'All matching dates show cards directly');
  const month=vm.runInContext('historyCalendar.month()',app.context);
  await app.node('history-clear').listeners.get('click')();await flush();
  assert.equal(app.node('history-calendar').inert,false);
  assert.equal(vm.runInContext('historyCalendar.month()',app.context),month);
  assert.equal(app.node('history-date-groups').querySelectorAll('.project-card').length,0,'Clear restores the calendar page without day results');
  app.backend(async route=>route.startsWith('/api/history?q=')?{ok:false,status:503,json:async()=>({error:'offline'})}:null);
  app.node('history-query').value='failed';await app.node('history-search-form').listeners.get('submit')({preventDefault(){}});
  assert.equal(app.node('history-calendar').inert,true,'A failed search never re-enables calendar controls');
  assert.equal(app.node('history-query').value,'failed');
}

async function testCalendarLateResponsesCannotReplaceCurrentMonthOrDay() {
  const app=scenario({initialPath:'/history'});await flush();
  let release;
  app.backend(async route=>route.startsWith('/api/history/calendar?')?
    new Promise(resolve=>{release=()=>resolve({ok:true,status:200,json:async()=>({dates:[{date:'2026-10-01',count:99}]})});}):null);
  const pending=vm.runInContext('historyCalendar.load()',app.context);await flush();
  app.backend(async route=>route.startsWith('/api/history/calendar?')?
    {ok:true,status:200,json:async()=>({dates:[]})}:null);
  await app.node('history-next-month').listeners.get('click')();await flush();
  const currentMonth=app.node('history-month').value;
  release();await pending;await flush();
  assert.equal(app.node('history-month').value,currentMonth);
  assert.equal(app.node('history-calendar-days').querySelectorAll('.calendar-count').length,0,
    'The previous month response must not place its saved counts in the current month');
  app.backend(async route=>route.startsWith('/api/history/2026-09-27')?
    new Promise(resolve=>{release=()=>resolve({ok:true,status:200,json:async()=>({sections:[{id:'growth',cards:[]}],keyword_groups:[]})});}):null);
  const dayPending=vm.runInContext("historyCalendar.choose('2026-09-27')",app.context);await flush();
  await vm.runInContext("historyCalendar.choose('2026-09-26')",app.context);await flush();
  release();await dayPending;await flush();
  assert.equal(app.node('history-date-groups').querySelectorAll('.history-date-title')[0].textContent,'2026-09-26');
  assert.equal(app.node('history-date-groups').querySelectorAll('.project-card').length,1,
    'An older empty day response cannot erase the currently selected day');
}

async function testFollowingDragBlocksDetailUntilNextGesture() {
  const app=scenario({initialPath:'/following'});await flush();
  app.backend(async route=>route.startsWith('/api/following')?{ok:true,status:200,json:async()=>({cards:[{repo_id:1,title:'owner/repo',description:'Useful',folders:[]}],folders:[{id:1,name:'Reading',count:0}],all_count:1,unfiled_count:1})}:null);
  app.context.location.search='?folder=unfiled';await vm.runInContext('loadFollowing()',app.context);
  const card=app.node('following-cards').querySelectorAll('.project-card')[0];
  const event={button:0,preventDefault(){},dataTransfer:{setData(){}}};
  card.listeners.get('dragstart')(event);card.listeners.get('dragend')();card.listeners.get('click')(event);await flush();
  assert.equal(app.context.location.pathname,'/following','Drag completion must not open repository details');
  card.listeners.get('pointerdown')();card.listeners.get('click')(event);await flush();
  assert.equal(app.context.location.pathname,'/project/1','A fresh pointer gesture still opens details');
}

async function testReadmeRenderingSafetyAndNavigationRace() {
  const app = scenario(); await flush();
  app.context.testView = {status:'ready', document:{source_url:'https://github.com/owner/repo#readme',observed_at:'2026-09-30'},
    sections:[{kind:'purpose',title:'用途',text:'Author purpose',source_heading:'Overview'},
              {kind:'users',title:'适合谁',text:'',source_heading:null}],
    full_markdown:'# Heading\n\n<script>alert(1)</script>\n\n[bad](javascript:alert) [good](https://example.com)\n\n![image](https://example.com/image.png)\n\n```sh\necho unchanged\n```',reason:null};
  vm.runInContext('renderReadme(testView)', app.context);
  assert.ok(app.node('readme-purpose').children.length,'Author purpose is rendered separately from AI facts');
  const content = app.node('markdown-fixture');
  app.context.markdownFixture=content;
  vm.runInContext('markdownNodes(testView.full_markdown,markdownFixture,testView.document.source_url)',app.context);
  const flatten = item => [item,...item.children.flatMap(flatten)];
  const all = flatten(content);
  assert.equal(all.some(item => item.tagName === 'SCRIPT' || item.tagName === 'IMG'), false);
  assert.equal(all.filter(item => item.href).every(item => item.href.startsWith('https://')), true);
  assert.equal(all.some(item => item.tagName === 'CODE' && item.textContent === 'echo unchanged'), true);
  assert.ok(all.some(item=>item.className==='readme-link-arrow' && item.getAttribute('data-translation-literal')===''),
    'Link arrow is a literal decoration, not model input prose');
  app.context.testView.full_markdown=JSON.parse(execFileSync('python',['-X','utf8','-c',
    'import json; from tests.test_readme_content import document; from github_radar.readme_content import extract_readme; print(json.dumps(extract_readme(document("# Project\\n\\n## Installation\\nRun <code>echo `pwd`</code> safely.\\n\\n<pre><code>npm install example\\nnode app.js</code></pre>")).selected_markdown))'],
    {cwd:path.join(__dirname,'..'),encoding:'utf8'}));
  vm.runInContext('clear(markdownFixture);markdownNodes(testView.full_markdown,markdownFixture,testView.document.source_url)',app.context);
  assert.deepEqual(flatten(content).filter(item=>item.tagName==='CODE').map(item=>item.textContent),
    ['echo `pwd`','npm install example\nnode app.js'],'Nested HTML commands must render as complete protected code');
  app.context.testView.document.source_url = 'https://github.com/owner/repo/blob/main/README.md';
  app.context.testView.full_markdown = '[Install](docs/install.md) [Section](#installation) [Unsafe](javascript:alert)';
  vm.runInContext('clear(markdownFixture);markdownNodes(testView.full_markdown,markdownFixture,testView.document.source_url)', app.context);
  const relativeLinks = flatten(content).filter(item => item.href).map(item => item.href);
  assert.deepEqual(relativeLinks, ['https://github.com/owner/repo/blob/main/docs/install.md', 'https://github.com/owner/repo/blob/main/README.md#installation']);
  app.context.location.pathname = '/project/1';
  app.setReadme({...app.context.testView, status:'stale', reason:'断网，保留旧副本'});
  await vm.runInContext("showDetail('1')",app.context); await flush();
  assert.equal(app.node('readme-status').textContent.includes('断网'), true);
  const posts = app.requests().filter(route => route.includes('/api/ai/projects/')).length;
  await vm.runInContext('loadReadme("1", true)',app.context);
  assert.equal(app.requests().filter(route => route.includes('/api/ai/projects/')).length, posts);
  app.delayReadme();
  const pending = vm.runInContext('loadReadme("1")',app.context); await flush();
  app.context.location.pathname = '/history';
  vm.runInContext('route()',app.context); await flush();
  app.node('readme-status').textContent = 'other page';
  app.releaseReadme(); await pending;
  assert.equal(app.node('readme-status').textContent, 'other page', 'Late response must not touch another page');
}

async function testSixFactsAndFullReadmeTables() {
  const app=scenario();await flush();
  app.context.fullView={status:'ready',document:{source_url:'https://github.com/owner/repo/blob/main/README.md',observed_at:'today'},
    sections:[{kind:'purpose',text:'Author original purpose'}],selected_markdown:'Short excerpt',
    full_markdown:'# Start\n\nOpening\n\n| Name | Command |\n| --- | --- |\n| Engine | `run \\| test` |\n\n1. First step\n2. Second step\n\n## End\n\nTail marker'};
  vm.runInContext('renderReadme(fullView)',app.context);
  const flatten=n=>[n,...n.children.flatMap(flatten)];
  const markdownFixture=app.node('markdown-fixture');app.context.markdownFixture=markdownFixture;
  vm.runInContext('markdownNodes(fullView.full_markdown,markdownFixture,fullView.document.source_url)',app.context);
  const all=flatten(markdownFixture);
  assert.ok(all.some(n=>n.tagName==='TABLE'),'Full README preserves table structure');
  assert.ok(all.some(n=>n.tagName==='CODE'&&n.textContent==='run | test'));
  assert.ok(all.some(n=>n.tagName==='OL'));
  assert.ok(all.some(n=>n.textContent==='Tail marker'),'Full tail, not selected excerpt, is rendered');
  const firstBlock=app.node('readme-purpose').children[0];
  vm.runInContext("renderReadme({...fullView,status:'fetching'})",app.context);
  assert.equal(app.node('readme-purpose').children[0],firstBlock,'Polling the same README must retain its translated author-purpose DOM');
  app.context.detailFacts={repo_id:1,analysis_stale:true,ai_explanation:{schema_version:1,zh:{summary:'Old',purpose:'AI must not replace author',scenarios:'Examples',users:'Developers',highlights:['Core']}}};
  vm.runInContext('renderAIExplanation(detailFacts)',app.context);
  assert.equal(app.node('detail-ai-problem').textContent,'待生成项目理解');
  assert.equal(app.node('detail-ai-prerequisites').textContent,'待生成项目理解');
  assert.equal(app.node('detail-ai-users').textContent,'Developers');
  assert.ok(flatten(app.node('readme-purpose')).some(n=>n.textContent==='Author original purpose'));
  assert.ok(!app.requests().some(p=>p.includes('/api/ai/projects/')),'Reading never starts AI');
  app.context.detailFacts.ai_explanation.zh.problem='Actual problem';app.context.detailFacts.ai_explanation.zh.prerequisites='Python';
  vm.runInContext('renderAIExplanation(detailFacts)',app.context);
  assert.equal(app.node('detail-ai-problem').textContent,'Actual problem');
  assert.equal(app.node('detail-ai-prerequisites').textContent,'Python');
}

async function testExplicitMotionChoiceAndFailureRestore() {
  const app = scenario(); await flush();
  assert.equal(app.context.document.documentElement.dataset.motion, 'off', 'Follow reduced-motion by default');
  app.node('motion-preference').value = 'on';
  await app.node('motion-preference').listeners.get('change')();
  assert.equal(app.context.document.documentElement.dataset.motion, 'on');
  app.failNextPreference(); app.node('motion-preference').value = 'off';
  await app.node('motion-preference').listeners.get('change')();
  assert.equal(app.context.document.documentElement.dataset.motion, 'on', 'Failed save must keep saved choice');
  assert.equal(app.node('motion-preference').value, 'on');
  app.node('motion-preference').value = 'off';
  await app.node('motion-preference').listeners.get('change')();
  assert.equal(app.context.document.documentElement.dataset.motion, 'off');
}

async function testSharedPointerLightAndImmediateReduction() {
  const app = scenario({queuedFrames:true}); await flush();
  app.paint();
  const control = app.node('motion-preference');
  control.value = 'on'; await control.listeners.get('change')();
  const surface = className => ({className, disabled:false,
    style:{setProperty(name,value){this[name]=value;},removeProperty(name){delete this[name];}},
    closest(selector){return selector.split(',').some(part=>part.trim()==='.'+className)?this:null;},
    getBoundingClientRect(){return {left:10,top:20,width:200,height:100};},
    contains(other){return other===this;}});
  const move = app.context.document.listeners.get('pointermove');
  for (const className of (process.argv.includes('--pointer-reduction') ? ['project-card','button'] : ['project-card','button','history-date-toggle'])) {
    const target = surface(className);
    move({target,clientX:30,clientY:30});
    move({target,clientX:160,clientY:60});
    assert.equal(app.frames.size,1,className+' batches pointer movement into one paint');
    app.paint();
    assert.equal(target.style['--mx'],'75%',className+' follows pointer');
    assert.equal(target.style['--my'],'40%');
    app.context.document.listeners.get('pointerout')({relatedTarget:null});
    assert.equal(target.style['--mx'],undefined,'Leaving resets light');
  }
  const combined=surface('folder-tab'),name=surface('button');
  name.closest=selector=>selector==='.folder-tab'?combined:selector.includes('.folder-select')?name:selector.includes('.button')?name:null;
  move({target:name,clientX:160,clientY:60});app.paint();
  assert.equal(combined.style['--mx'],'75%','Unified folder glass follows pointer over its name or dots');
  const target = surface('button');
  move({target,clientX:160,clientY:60}); app.paint();
  control.value = 'off'; await control.listeners.get('change')();
  assert.equal(target.style['--mx'],undefined,'Reduced motion resets light without waiting for blur');
  move({target,clientX:50,clientY:50});
  assert.equal(app.frames.size,0,'Reduced motion schedules no pointer animation');
}

async function testNavigationRespondsToTextScaleAndAvailableWidth() {
  const app = scenario(); await flush();
  app.context.window.innerWidth = 1024;
  vm.runInContext('narrowNavigation.matches = false; setNavigationOpen(false)', app.context);
  assert.equal(app.node('sidebar').hidden, false);
  vm.runInContext('previewFontScale(2)', app.context);
  assert.equal(app.node('sidebar').hidden, false, 'Font scaling keeps the fixed-width sidebar');
  assert.equal(app.context.document.documentElement.dataset.navigation, 'wide');
  app.context.window.innerWidth = 800;
  vm.runInContext('setNavigationOpen(false)',app.context);
  assert.equal(app.node('sidebar').hidden,true,'A genuinely narrow window still collapses navigation');
  vm.runInContext('setNavigationOpen(true)', app.context);
  app.context.window.innerWidth = 2000;
  app.context.window.listeners.get('resize')();
  assert.equal(app.node('sidebar').hidden, false);
  assert.equal(app.node('nav-scrim').hidden, true);
  assert.equal(app.context.document.documentElement.dataset.navigation, 'wide');
}

async function testOfflineTranslationIntegration() {
  const app=scenario();await flush();
  assert.ok(app.translationCalls().some(c=>c[0]==='refresh'&&c[1]==='home-view'),'Home content translated');
  const card=vm.runInContext("cardNode({repo_id:1,title:'owner/repo',description:'Useful project',tags:['local AI']})",app.context);
  assert.equal(card.querySelectorAll('.card-description')[0].getAttribute('data-translate'),'');
  await app.node('translation-original').listeners.get('click')();
  assert.ok(app.translationCalls().some(c=>c[0]==='original'&&c[1]===true));
  vm.runInContext("history.replaceState({},'', '/following');route()",app.context);await flush();
  assert.ok(app.translationCalls().some(c=>c[0]==='invalidate'));
  assert.ok(app.translationCalls().some(c=>c[0]==='refresh'&&c[1]==='following-view'));
  assert.ok(!app.requests().some(p=>p.includes('/api/ai/explain')),'Translation must not call AI');
}

async function testFilteredHistoryReturnAndFolderControls() {
  const app=scenario({initialPath:'/history'});await flush();
  app.node('history-query').value='用户 %_';app.node('history-from').value='2026-09-01';
  assert.equal(typeof app.node('history-search-form').listeners.get('submit'),'function','Search form must submit');
  await app.node('history-search-form').listeners.get('submit')({preventDefault(){}});
  assert.equal(new URLSearchParams(app.context.location.search).get('q'),'用户 %_');
  app.context.window.scrollY=320;
  const card=vm.runInContext("cardNode({repo_id:1,history_date:'2026-09-27',title:'owner/repo',growth_rank:9})",app.context);
  card.listeners.get('click')({button:0,preventDefault(){}});await flush();
  assert.equal(new URL(app.node('back-link').href,'http://localhost').searchParams.get('q'),'用户 %_');
  app.node('back-link').listeners.get('click')({button:0,preventDefault(){}});await flush();
  assert.equal(app.context.window.scrollY,320);
  await app.node('history-clear').listeners.get('click')();assert.equal(app.context.location.search,'');
  app.context.location.pathname='/following';app.context.location.search='';vm.runInContext('route()',app.context);await flush();
  assert.ok(app.node('following-folders').children.some(n=>n.textContent.includes('未分类')),'Unfiled selector exists');
  assert.equal(typeof app.node('folder-editor-form').listeners.get('submit'),'function');
  const detail=scenario({detail:true});await flush();await detail.node('follow-button').listeners.get('click')();
  assert.equal(typeof detail.node('detail-classify').listeners.get('click'),'function');
  assert.equal(typeof detail.node('classification-form').listeners.get('submit'),'function');
}

async function testClassificationLoadFailureCannotClearMemberships(){
  const app=scenario({detail:true});await flush();
  await app.node('follow-button').listeners.get('click')();
  app.backend(async route=>route==='/api/following/1/folders'?{ok:false,status:503,json:async()=>({error:'load failed'})}:null);
  await app.node('detail-classify').listeners.get('click')();
  assert.equal(app.node('classification-save').disabled,true,'Failed load must not expose an empty replacement selection');
  assert.match(app.node('classification-status').textContent,/load failed/);
}
async function testFolderFailureRetentionAndLanguageDrafts(){
  const app=scenario({detail:true});await flush();let fail=false;
  const ok=data=>({ok:true,status:200,json:async()=>data});
  app.backend(async(route,options)=>{
    if(route==='/api/following/1/folders')return ok({ids:[1],folders:[{id:1,name:'中文 %_'},{id:2,name:'Project 中文'}]});
    if(route==='/api/following/1/classify')return fail?{ok:false,status:503,json:async()=>({error:'disk full'})}:ok({followed:true,ids:JSON.parse(options.body).ids});
    return null;
  });
  await app.node('follow-button').listeners.get('click')();
  await app.node('detail-classify').listeners.get('click')();
  const labels=app.node('classification-choices').children;
  assert.equal(labels[0].children[1].textContent,'中文 %_');assert.equal(labels[0].children[1].getAttribute('data-translate'),undefined);
  labels[1].children[0].checked=true;labels[1].children[0].listeners.get('change')();
  app.node('classification-name').value='New 中文';
  fail=true;await app.node('classification-form').listeners.get('submit')({preventDefault(){}});
  assert.match(app.node('classification-status').textContent,/disk full/);assert.equal(labels[1].children[0].checked,true);
  assert.equal(app.node('classification-name').value,'New 中文');
  fail=false;await app.node('classification-form').listeners.get('submit')({preventDefault(){}});
  assert.equal(app.node('classification-dialog').open,false);
  const history=scenario({initialPath:'/history'});await flush();history.node('history-query').value='未提交 中文';
  await vm.runInContext("setLanguage('en')",history.context);
  assert.equal(history.node('history-query').value,'未提交 中文','Language change must keep typed query');
}

async function testHistoryNavigationQueryIdentity(){
  const app=scenario({initialPath:'/history'});await flush();app.node('history-query').value='saved-filter';
  await app.node('history-search-form').listeners.get('submit')({preventDefault(){}});
  vm.runInContext("navLink({button:0,preventDefault(){},currentTarget:{pathname:'/following'}})",app.context);await flush();
  vm.runInContext("navLink({button:0,preventDefault(){},currentTarget:{pathname:'/history'}})",app.context);await flush();
  assert.equal(app.context.location.search,'');
  assert.equal(vm.runInContext('historySearch.filters().q',app.context),'','Cached results must agree with URL');
  assert.equal(app.node('history-query').value,'');
}
async function testEditsDuringPendingLanguageChange(){
  const app=scenario({initialPath:'/history'});await flush();app.node('history-query').value='before';app.delayNextPreference();
  const language=vm.runInContext("setLanguage('en')",app.context);await flush();
  app.node('history-query').value='typed while pending';app.releasePreference();await language;
  assert.equal(app.node('history-query').value,'typed while pending','Late typing belongs to the user');
  const detail=scenario({detail:true});await flush();
  const ok=data=>({ok:true,status:200,json:async()=>data});
  detail.backend(async route=>route==='/api/following/1/folders'?ok({ids:[1],folders:[{id:1,name:'A'},{id:2,name:'B'}]}):null);
  await detail.node('follow-button').listeners.get('click')();await detail.node('detail-classify').listeners.get('click')();detail.delayNextPreference();
  const change=vm.runInContext("setLanguage('en')",detail.context);await flush();
  const checkbox=detail.node('classification-choices').children[1].children[0];checkbox.checked=true;checkbox.listeners.get('change')();
  detail.releasePreference();await change;
  assert.equal(checkbox.checked,true,'Late folder choices are not overwritten');
}

async function testSharedExpansionRouteWiring(){
  for(const entry of ['/', '/history?day=2026-09-27','/following']){
    const app=scenario();await flush();let calls=0;
    vm.runInContext("motionPreference='on'",app.context);
    app.context.document.startViewTransition=update=>{
      calls++;const done=Promise.resolve().then(update);
      return {ready:done,updateCallbackDone:done,finished:done,skipTransition(){}};
    };
    vm.runInContext(`history.replaceState({},'',${JSON.stringify(entry)})`,app.context);
    const link=vm.runInContext("cardNode({repo_id:1,title:'owner/repo',stars:'★ 900',growth:'+3 Star',growth_rank:3,history_date:'2026-09-27'})",app.context);
    app.node(entry.startsWith('/history')?'history-dates':entry==='/following'?'following-cards':'growth-cards').append(link);
    await link.listeners.get('click')({button:0,preventDefault(){}});
    assert.equal(calls,0,'Real elements avoid raster snapshot handoff from '+entry);
    assert.equal(app.node('detail-article').animations.length,1,'Detail frame appears as a real node');
    assert.equal(app.context.location.pathname,'/project/1');
    assert.equal(vm.runInContext('detailReturnPath',app.context),entry);
    assert.equal(app.node('detail-title').style.viewTransitionName||'','','No stale transition names');
  }
}

async function testReturnSnapshotRestoresScrollBeforePaint(){
  const app=scenario({queuedFrames:true});await flush();app.paint();
  vm.runInContext("motionPreference='on'",app.context);
  const snapshots=[];
  app.context.document.startViewTransition=update=>{
    const done=Promise.resolve().then(update).then(()=>snapshots.push(app.context.window.scrollY));
    return {ready:done,updateCallbackDone:done,finished:done,skipTransition(){}};
  };
  app.node('home-view').append(app.node('growth-cards'));
  const link=vm.runInContext("cardNode({repo_id:1,title:'owner/repo',stars:'★ 900',growth:'+3 Star',growth_rank:3})",app.context);
  app.node('growth-cards').append(link);app.context.window.scrollY=320;
  await link.listeners.get('click')({button:0,preventDefault(){}});
  await app.node('back-link').listeners.get('click')({button:0,preventDefault(){},currentTarget:app.node('back-link')});
  assert.equal(snapshots.length,0,'Return does not create a raster snapshot');
  assert.equal(app.context.window.scrollY,320,'Return uses original scroll before the live animation');
}

async function testHomeSectionSwitchingWithoutRequests() {
  const app=scenario();await flush();
  app.setIssue({keywords:[{id:1,term:'MCP'},{id:2,term:'Design'}],keyword_groups:[
    {keyword_id:1,term:'MCP',cards:[]},{keyword_id:2,term:'Design',cards:[]}]});
  await vm.runInContext('loadIssue()',app.context);
  const tabs=app.node('home-section-tabs').querySelectorAll('.home-tab');
  assert.deepEqual(tabs.map(n=>n.textContent),['Star 增长','MCP','Design']);
  assert.equal(app.node('growth-results').hidden,false);
  assert.equal(app.node('keyword-results').hidden,true);
  const requests=app.requests().length;
  tabs[2].listeners.get('click')();
  assert.equal(app.node('growth-results').hidden,true);
  assert.equal(app.node('keyword-results').hidden,false);
  assert.equal(app.node('recommendation-title').textContent,'Design');
  const groups=app.node('keyword-groups').querySelectorAll('.keyword-group');
  assert.equal(groups[0].hidden,true);assert.equal(groups[1].hidden,false);
  assert.equal(app.requests().length,requests,'Tab switches never refresh or call AI');
  await vm.runInContext('showHome()',app.context);
  assert.equal(groups[1].hidden,false,'Returning from detail preserves selected keyword');
  await vm.runInContext('loadIssue()',app.context);
  assert.equal(app.node('recommendation-title').textContent,'Design','Polling preserves selection');
  app.node('home-section-more').open=true;
  await vm.runInContext('loadIssue()',app.context);
  assert.equal(app.node('home-section-more').open,true,'Polling does not close a keyword menu being used');
  app.setIssue({keywords:[{id:1,term:'MCP'}],keyword_groups:[{keyword_id:1,term:'MCP',cards:[]}]});
  await vm.runInContext('loadIssue()',app.context);
  assert.equal(app.node('growth-results').hidden,false,'Deleted selection safely falls back to growth');
}

async function testHomeOverflowMenuAndKeyboard() {
  const app=scenario();await flush();
  app.setIssue({keywords:[{id:1,term:'MCP'},{id:2,term:'Design'},{id:3,term:'Disabled',enabled:false}]});
  await vm.runInContext('loadIssue()',app.context);
  app.node('home-section-bar').clientWidth=260;
  vm.runInContext('layoutHomeSections()',app.context);
  const tabs=app.node('home-section-tabs').querySelectorAll('.home-tab');
  assert.equal(tabs.length,3);assert.equal(tabs[0].hidden,false);
  assert.equal(tabs[2].hidden,true);
  assert.equal(app.node('home-section-more').hidden,false);
  const choices=app.node('home-section-menu').querySelectorAll('.home-menu-choice');
  assert.deepEqual(choices.map(n=>n.textContent),['MCP','Design']);
  app.node('home-section-more').open=true;
  choices[1].listeners.get('click')();
  assert.equal(app.node('home-section-more').open,false);
  assert.equal(app.node('recommendation-title').textContent,'Design');
  app.node('home-section-bar').clientWidth=800;
  vm.runInContext('layoutHomeSections()',app.context);
  assert.equal(app.node('home-section-more').hidden,false,'All-keywords menu remains available even without overflow');
  tabs[0].listeners.get('keydown')({key:'ArrowRight',preventDefault(){}});
  assert.equal(app.node('recommendation-title').textContent,'MCP');
}

async function testMainPageTransitionsAndSavedKeywordSwitch(){
  const app=scenario();await flush();vm.runInContext("motionPreference='on'",app.context);
  let calls=0;const names=[];
  app.context.document.startViewTransition=update=>{
    calls++;names.push(vm.runInContext('currentView().style.viewTransitionName',app.context));
    const done=Promise.resolve().then(update);
    return {ready:done,updateCallbackDone:done,finished:done,skipTransition(){}};
  };
  for(const path of ['/history','/following','/settings','/']){
    await vm.runInContext(`navLink({button:0,preventDefault(){},currentTarget:{pathname:${JSON.stringify(path)},id:'nav-test'}})`,app.context);
    assert.equal(app.context.location.pathname,path);
  }
  assert.equal(calls,0,'Main navigation uses live elements instead of browser snapshots');
  for(const id of ['history-view','following-view','settings-view','home-view'])assert.equal(app.node(id).animations.length,1,'Real page fades: '+id);
  assert.equal(app.node('sidebar').animations.length,0,'Sidebar stays outside the transitioning view');
  app.setIssue({keywords:[{id:1,term:'MCP'}],keyword_groups:[{keyword_id:1,term:'MCP',cards:[]}]});
  await vm.runInContext('loadIssue()',app.context);const count=app.requests().length;
  await app.node('home-section-tabs').querySelectorAll('.home-tab')[1].listeners.get('click')();
  assert.equal(calls,0);assert.equal(app.node('keyword-results').animations.length,1);assert.equal(app.node('keyword-results').hidden,false);
  assert.equal(app.requests().length,count,'Animated keyword selection cannot start crawl or AI');
}

(async () => {
  const codexApp=scenario();await flush();
  vm.runInContext("aiConnection={ready:true,reason:'Codex 已连接',models:[]};renderModelOptions()",codexApp.context);
  assert.equal(codexApp.node('settings-codex-status').textContent,'Codex 已连接');
  assert.equal(codexApp.node('settings-codex-connect').hidden,true);
  assert.equal(typeof codexApp.node('settings-codex-check').listeners.get('click'),'function');
  vm.runInContext("aiConnection={ready:false,reason:'所选模型已不可用，请重新选择或使用自动选择',selected_model:'old',models:[{id:'available'}]};renderModelOptions()",codexApp.context);
  assert.equal(codexApp.node('settings-codex-auto').hidden,false);
  assert.equal(codexApp.node('ai-model').disabled,false);
  await testMainPageTransitionsAndSavedKeywordSwitch();
  await testHomeSectionSwitchingWithoutRequests();
  await testHomeOverflowMenuAndKeyboard();
  if(process.argv.includes('--card-expansion')){await testSharedExpansionRouteWiring();await testReturnSnapshotRestoresScrollBeforePaint();console.log('Expansion route integration passed');return;}
  await testSharedExpansionRouteWiring();
  await testReturnSnapshotRestoresScrollBeforePaint();
  if(process.argv.includes('--pointer-light')||process.argv.includes('--pointer-reduction')){await testSharedPointerLightAndImmediateReduction();console.log('Pointer light tests passed');return;}
  if(process.argv.includes('--language-race')){await testEditsDuringPendingLanguageChange();console.log('Language race tests passed');return;}
  await testDetailToolsWithoutRemovedModules();
  await testCalendarSearchMutualExclusionAndCompactHistory();
  await testCalendarLateResponsesCannotReplaceCurrentMonthOrDay();
  await testFollowingDragBlocksDetailUntilNextGesture();
  await testFilteredHistoryReturnAndFolderControls();
  await testClassificationLoadFailureCannotClearMemberships();
  await testFolderFailureRetentionAndLanguageDrafts();
  await testHistoryNavigationQueryIdentity();
  await testEditsDuringPendingLanguageChange();
  await testOfflineTranslationIntegration();
  await testNavigationRespondsToTextScaleAndAvailableWidth();
  await testExplicitMotionChoiceAndFailureRestore();
  await testSharedPointerLightAndImmediateReduction();
  await testReadmeRenderingSafetyAndNavigationRace();
  await testSixFactsAndFullReadmeTables();
  await testCompactReturningGrowthAndSidebar();
  await testUpdatePollingPreservesTranslatedGrowthNodes();
  await testContinuousFontPreviewAndSerializedSave();
  await testDelayedExit();
  await testExitAfterInternalNavigation();
  await testDetailStatusForUpdateAndExit();
  await testRefreshAcknowledgementTimeoutAllowsRetry();
  await testGroupedAIState();
  await testFullVerifiedKeywordGroupHasNoUselessAction();
  await testKeywordUsesSavedNoncontiguousRanksAndSameDetailTitle();
  await testGroupButtonKeepsFocusWhenCardsChange();
  await testDetailSwitchClearsAIText();
  await testGrowthRankAndKeywordLabelsAreText();
  await testSettingsKeywordActions();
  await testHistoryGroupsAndFollowingRoute();
  await testHistoryWithoutDataDoesNotStartGitHubUpdate();
  await testDailyScheduleSavesAndRestoresOnFailure();
  await testBackgroundCompletionAppearsThroughLocalPollingOnly();
  await testLanguageAndFontChoicePersistsInInterface();
  await testDetailWaitsForPreparedReadmeAndTranslation();
  await testReadmeReplacementWaitsWithoutChangingVisibleText();
  console.log("Browser interaction tests passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
