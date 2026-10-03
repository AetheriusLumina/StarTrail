"use strict";

const tokenKey = `github-radar-token:${location.host}`;
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has("token")) {
  sessionStorage.setItem(tokenKey, fragment.get("token"));
  history.replaceState(history.state, "", location.pathname + location.search);
}
const token = sessionStorage.getItem(tokenKey);
const byId = (id) => document.getElementById(id);
let latestIssue = null;
let homeScroll = 0;
let pollTimer = null;
let savedIssueTimer = null;
let quitting = false;
let didInitialRefresh = false;
let aiConnection = {ready: false, reason: "正在检查 AI 连接…", models: [], selected_model: null};
let aiPollTimer = null;
let detailPollTimer = null;
let detailRepoId = null;
let detailHasAnalysis=false, detailAnalysisStale=false;
let keywordCardSignature = "";
let keywordGroupNodes = new Map();
let homeSection = 'growth', homeTabSignature = '', homeTabNodes = new Map();
let lastAiAnnouncement = "";
let language = "zh";
let translationOriginal=false, translationRefreshQueued=false, translationLanguage='zh', detailPreparationKey=null, detailPreparationSequence=0;
const translationController=window.RadarTranslation.create({post,api,getLanguage:()=>language,onStatus:state=>{
  byId('translation-original').textContent=tr(translationOriginal?'显示译文':'查看原文');
  byId('translation-original').setAttribute('aria-pressed',String(translationOriginal));
  const label=state.state==='original'?'正在显示原文':state.state==='translating'?'正在本地翻译…'
    :state.failed?'部分内容未翻译，保留原文':state.reason?'译文缓存未保存':'本地机器翻译';
  const statusNode=byId('translation-status'),statusText=tr(label),reason=localizeServerText(state.reason || '');
  if(statusNode.textContent!==statusText)statusNode.textContent=statusText;
  if(statusNode.title!==reason)statusNode.title=reason;
  byId('translation-retry').hidden=!state.failed || translationOriginal;
}});
function translatable(node) { node.setAttribute('data-translate',''); return node; }
function translatePage() {
  if (translationRefreshQueued || quitting || detailPreparationKey===location.pathname+location.search) return;
  translationRefreshQueued=true;
  Promise.resolve().then(()=>{
    translationRefreshQueued=false;
    if (quitting || detailPreparationKey===location.pathname+location.search) return;
    const id=location.pathname.startsWith('/project/')?'detail-view':location.pathname==='/history'?'history-view'
      :location.pathname==='/following'?'following-view':location.pathname==='/settings'?'settings-view':'home-view';
    return translationController.refresh(byId(id));
  }).catch(()=>{byId('translation-status').textContent=tr('部分内容未翻译，保留原文');});
}
let fontScale = 1;
let savedPreferences = {language: "zh", font_scale: 1};
let desiredPreferences = {...savedPreferences};
let preferenceVersion = 0, pendingPreferences = null, savingPreferences = null;
let autoUpdateState = null;
let detailReturnPath = "/";
const pageScroll = new Map();
let historyDayGeneration=0;
let historyLoaded = false;
let historyLoadedQuery=null;
const historySearch=window.RadarHistoryFollowing.historyController({api,
  writeURL:url=>history.replaceState(history.state,'',url),onIndex:renderHistoryIndex});
const historyCalendar=window.RadarCalendar.create({api,byId,element,clear,tr,
  onDay:showCalendarDay,onMonth:clearHistoryDay,isCurrent:()=>location.pathname==='/history'});
const folderSelection=window.RadarHistoryFollowing.folderSelection({post});
let folderGeneration=0;
const followingBoard=window.RadarFollowingBoard.create({api,post,byId,element,clear,tr,cardNode,translatePage,
  isCurrent:()=>location.pathname==='/following',open:url=>transitionPage(()=>{history.replaceState(history.state,'',url);return loadFollowing();},
    {scope:()=>byId('following-results'),focus:false})});
let readmeGeneration = 0, readmePollTimer = null;
let motionPreference = 'system';
let resetPointerLight = () => {};
const systemMotion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : {matches:false};
const cardTransition=window.RadarCardTransition.create({document,enabled:motionEnabled,
  setTimer:setTimeout,clearTimer:clearTimeout});
function currentView(){return ['home-view','history-view','following-view','settings-view','detail-view'].map(byId).find(node=>!node.hidden);}
function transitionPage(update,{reverse=false,scope=currentView,focus=true}={}){
  const from=scope();return cardTransition.run({kind:'page',from:{frame:from,page:from},
    to:()=>{const page=scope();return {frame:page,page,focus:focus?page:{focus(){}}};},
    update:async()=>{await update();await translationController.refresh(currentView());},reverse});
}
let cardReturnEntry=null;
function cardParts(node){const metrics=node?.querySelector('.card-metrics');return {
  frame:node,title:node?.querySelector('.card-name'),rank:node?.querySelector('.card-rank'),
  stars:metrics?.children[0],growth:node?.querySelector('.card-growth'),description:node?.querySelector('.card-description')};}
function detailParts(){return {frame:byId('detail-article'),title:byId('detail-title'),rank:byId('detail-rank'),
  stars:byId('detail-metrics').children[0],growth:Array.from(byId('detail-metrics').children).find(n=>n.className.includes('accent')),
  description:byId('detail-description'),facts:byId('detail-facts-scroll'),actions:byId('detail-actions')};}
function projectListView(){return byId(location.pathname==='/history'?'history-view':location.pathname==='/following'?'following-view':'home-view');}
function rememberCard(node){const matches=Array.from(projectListView().querySelectorAll('.project-card')).filter(n=>n.href===node.href);
  cardReturnEntry={path:location.pathname+location.search,href:node.href,index:matches.indexOf(node),
    local:Array.from(projectListView().querySelectorAll('.history-results-scroll')).map(n=>({left:n.scrollLeft,top:n.scrollTop}))};}
function returningCard(){if(!cardReturnEntry||cardReturnEntry.path!==location.pathname+location.search)return null;
  const matches=Array.from(projectListView().querySelectorAll('.project-card')).filter(n=>n.href===cardReturnEntry.href);
  return matches[cardReturnEntry.index]||null;}
function restoreCardScroll(){Array.from(projectListView().querySelectorAll('.history-results-scroll')).forEach((node,i)=>{
  const saved=cardReturnEntry?.local[i];if(saved){node.scrollLeft=saved.left;node.scrollTop=saved.top;}});}
function motionEnabled() { return motionPreference === 'on' || (motionPreference === 'system' && !systemMotion.matches); }
function applyMotionPreference() {
  if(!motionEnabled())cardTransition.cancel({complete:true});
  resetPointerLight();
  document.documentElement.dataset.motion = motionEnabled() ? 'on' : 'off';
  byId('motion-preference').value = motionPreference;
  window.RadarThemeSelect?.refresh();
}
async function changeMotionPreference() {
  const control = byId('motion-preference'), next = control.value;
  control.disabled = true;
  try {
    await post('/api/preferences', {motion_preference:next});
    motionPreference = next; applyMotionPreference();
    byId('motion-feedback').textContent = tr('已保存。');
  } catch (error) {
    applyMotionPreference(); byId('motion-feedback').textContent = localizeServerText(error.message);
  } finally { control.disabled = false; }
}

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined && value !== null) node.textContent = String(value);
  if ((className || '').split(' ').some(name=>['card-description','card-source','card-observation','tag','readme-attribution'].includes(name))) translatable(node);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

async function api(path, options = {}) {
  if (!token) throw new Error(tr("页面缺少本次运行凭证。请重新打开 StarTrail。"));
  const controller=['/api/refresh','/api/issue','/api/health'].includes(path)?new AbortController():null;
  const timeout=controller?setTimeout(()=>controller.abort(),20000):null;
  try {
  const response = await fetch(path, {
    ...options,
    ...(controller?{signal:controller.signal}:{}),
    credentials: "omit",
    cache: "no-store",
    headers: { "X-Radar-Token": token, ...(options.headers || {}) },
  });
  let body;
  try { body = await response.json(); }
  catch(error) { if(error.name==='AbortError')throw error;throw new Error(tr("本地服务没有返回可读取的数据。")); }
  if (!response.ok) throw new Error(localizeServerText(body.error || `请求失败（${response.status}）`));
  return body;
  } catch(error) {
    if(error.name==='AbortError')throw new Error(tr('本地更新服务响应超时，请重试；不会重复启动已有更新。'));
    throw error;
  } finally { if(timeout!==null)clearTimeout(timeout); }
}

function post(path, payload = {}) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function setStatus(message, kind = "") {
  byId("status-text").textContent = localizeServerText(message);
  byId("status-indicator").className = `status-indicator ${kind}`;
  byId("detail-status-text").textContent = localizeServerText(message);
  byId("detail-status-indicator").className = `status-indicator ${kind}`;
  byId('update-status').textContent=localizeServerText(message);
  byId('update-status').className=`field-hint ${kind}`;
  byId('update-status').hidden=!message;
}

function setFeedback(message, error = false) {
  const node = byId("keyword-feedback");
  node.textContent = localizeServerText(message);
  node.className = error ? "form-feedback error" : "form-feedback";
}

function applyPreferences() {
  if (translationLanguage!==language) { translationController.invalidate();translationLanguage=language; }
  document.documentElement.lang = language === "en" ? "en" : "zh-CN";
  document.documentElement.style.setProperty("--font-scale", String(fontScale));
  setNavigationOpen(navigationOpen, false);
  byId("font-scale").value = String(fontScale);
  byId("font-scale-value").textContent = `${Number(fontScale.toFixed(3))} ×`;
  byId("font-scale").setAttribute("aria-valuetext", `${Number(fontScale.toFixed(3))} ${tr("倍")}`);
  for (const node of document.querySelectorAll("[data-i18n]"))
    node.textContent = tr(node.getAttribute("data-i18n"));
  for (const [id, label] of [["nav-home", "首页"], ["nav-history", "历史"],
                             ["nav-following", "我的关注"], ["nav-settings", "设置"]])
    byId(id).textContent = tr(label);
  byId("language-toggle").textContent = language === "zh" ? "English" : "中文";
  byId("language-toggle").setAttribute("aria-label", language === "zh" ? "Switch to English" : "切换到中文");
  byId("primary-nav").setAttribute("aria-label", tr("主要页面"));
  byId("brand-link").setAttribute("aria-label", `StarTrail ${tr("首页")}`);
  byId("font-size-controls").setAttribute("aria-label", tr("字号"));
  byId("font-scale-label").textContent = tr("字号倍率");
  byId("font-reset").textContent = tr("恢复 1 倍");
  byId("keyword-input").placeholder = tr("例如 local AI、design tools");
  if (location.pathname === "/") document.title = `StarTrail · ${tr("发现值得关注的开源项目")}`;
  else if (["/history", "/following", "/settings"].includes(location.pathname))
    document.title = `${tr(location.pathname === "/history" ? "历史" : location.pathname === "/following" ? "我的关注" : "设置")} · StarTrail`;
  renderModelOptions();
  if (autoUpdateState) renderAutoUpdate(autoUpdateState, false);
}

async function loadPreferences() {
  try {
    const saved = await api("/api/preferences");
    language = saved.language;
    fontScale = saved.font_scale ?? ({small: .9375, normal: 1, large: 1.125}[saved.font_size] || 1);
    savedPreferences = {language, font_scale: fontScale};
    desiredPreferences = {...savedPreferences};
    motionPreference = saved.motion_preference || 'system';
    applyMotionPreference();
    applyPreferences();
  } catch (error) {
    byId("preference-feedback").textContent = error.message;
  }
}

async function setLanguage(next) {
  if (next === language || !["zh", "en"].includes(next)) return;
  try {
    if (!await queuePreferenceSave({...desiredPreferences, language: next, font_scale: fontScale})) return;
    applyPreferences();
    if (latestIssue) await loadIssue();
    if (detailRepoId) {
      const scroll = window.scrollY;
      await showDetail(detailRepoId,{preserveFolders:true});
      window.scrollTo(0, scroll);
    } else if (location.pathname === "/history") {
      historyLoaded = false;
      await historySearch.search(historySearch.filters(),{write:false});
    } else if (location.pathname === "/following") await loadFollowing();
    else if (location.pathname === "/settings") await loadSettings(false);
  } catch (error) { byId("preference-feedback").textContent = localizeServerText(error.message); }
}

function previewFontScale(next) {
  const scale = Number(next);
  if (!Number.isFinite(scale) || scale < .8 || scale > 2) return false;
  fontScale = scale;
  desiredPreferences.font_scale = scale;
  preferenceVersion++;
  applyPreferences();
  return true;
}

function commitFontScale(next) {
  if (!previewFontScale(next)) return Promise.resolve(false);
  return queuePreferenceSave({...desiredPreferences, font_scale: fontScale});
}

function queuePreferenceSave(next) {
  desiredPreferences = {...next};
  pendingPreferences = {...next, version: ++preferenceVersion};
  if (savingPreferences) return savingPreferences;
  savingPreferences = (async () => {
    let success = true;
    while (pendingPreferences) {
      const intent = pendingPreferences;
      pendingPreferences = null;
      try {
        await post("/api/preferences", {language: intent.language, font_scale: intent.font_scale});
        savedPreferences = {language: intent.language, font_scale: intent.font_scale};
        success = true;
        if (intent.version === preferenceVersion) {
          language = intent.language; fontScale = intent.font_scale;
          applyPreferences();
          byId("preference-feedback").textContent = tr("字号已保存。");
        }
      } catch (error) {
        success = false;
        if (intent.version === preferenceVersion) {
          desiredPreferences = {...savedPreferences};
          language = savedPreferences.language; fontScale = savedPreferences.font_scale;
          applyPreferences();
          byId("preference-feedback").textContent = localizeServerText(error.message);
        }
      }
    }
    return success;
  })().finally(() => { savingPreferences = null; });
  return savingPreferences;
}

function cardNode(card,{compact=false}={}) {
  compact = compact || (card.display_role === "old" && !card.history_date);
  const link = element("a", compact ? "project-card project-card-compact" : "project-card");
  link.href = `/project/${encodeURIComponent(card.repo_id)}`
    + (card.history_date ? `?date=${encodeURIComponent(card.history_date)}` : card.recommendation_date ? `?context=${encodeURIComponent(card.recommendation_date)}` : "");
  link.setAttribute('data-project-link',link.href);
  const top = element("div", "card-top");
  const displayRank = card.display_rank || card.growth_rank;
  const rankClass = `rank-${displayRank && displayRank <= 3 ? displayRank : 'other'}`;
  const headline = element("div", `card-headline ${rankClass}`);
  if (displayRank) headline.append(element("span", `card-rank ${rankClass}`, displayRank));
  headline.append(element("h3", "card-name", card.title));
  top.append(headline, element("span", "card-arrow", "↗"));
  link.append(top);
  if (!compact) link.append(element("p", "card-source", localizeServerText(card.source)));
  if (!compact && card.matched_keywords && card.matched_keywords.length) {
    const shown = card.matched_keywords.slice(0, 2).join(language === "en" ? ", " : "、");
    const rest = card.matched_keywords.length > 2
      ? (language === "en" ? ` and ${card.matched_keywords.length - 2} more` : ` 等${card.matched_keywords.length}个`) : "";
    link.append(element("span", "card-keyword-match", `${tr("基础匹配")}${language === "en" ? ": " : "："}${shown}${rest}`));
  }
  if (!compact && card.ai_status) {
    const labels = {basic: "基础匹配", relevant: "AI 已核实", uncertain: "证据不足", irrelevant: "AI 判为不相关"};
    link.append(element("span", `ai-card-label ${card.ai_status}`,
      tr(labels[card.ai_status] || "基础匹配")));
  }
  if (!compact) link.append(element("p", "card-description", card.description));
  const bottom = element("div", "card-bottom");
  const metrics = element("div", "card-metrics");
  metrics.append(element("span", "", card.stars));
  const growth = compact && !/\d/.test(card.growth || '') ? tr('新增 Star 未记录') : localizeServerText(card.growth || '');
  if (growth) metrics.append(element("span", "card-growth", growth));
  const tags = element("div", "card-tags");
  for (const tag of card.tags || []) tags.append(element("span", "tag", tag));
  bottom.append(metrics);
  if (!compact) bottom.append(tags);
  link.append(bottom);
  if (!compact && card.saved_at) link.append(element("span", "card-saved-at",
    `${tr("本机保存于")} ${card.saved_at.replace("T", " ")}`));
  link.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if(link.getAttribute('data-drag-block')==='true'){event.preventDefault();return;}
    if (window.getSelection && String(window.getSelection()).trim()) { event.preventDefault(); return; }
    event.preventDefault();
    detailReturnPath = location.pathname === "/project/" + card.repo_id
      ? "/" : location.pathname + location.search;
    pageScroll.set(detailReturnPath, window.scrollY);
    if (detailReturnPath === "/") homeScroll = window.scrollY;
    rememberCard(link);
    return cardTransition.run({from:cardParts(link),to:()=>location.pathname===`/project/${card.repo_id}`?detailParts():null,
      update:()=>{history.replaceState({fromRadar:true,returnPath:detailReturnPath},'',link.href);return route({transition:true});}});
  });
  return link;
}

const renderedProjectLists=new WeakMap();
function renderProjectList(container,cards) {
  const signature=JSON.stringify([language,cards]),previous=renderedProjectLists.get(container);
  const current=Array.from(container.children);
  if(previous?.signature===signature && current.length===previous.nodes.length &&
      current.every((node,i)=>node===previous.nodes[i]))return;
  clear(container);
  for(const card of cards)container.append(cardNode(card));
  renderedProjectLists.set(container,{signature,nodes:Array.from(container.children)});
}
function renderCards(section, listId, emptyId, emptyMessage) {
  renderProjectList(byId(listId),section.cards || []);
  const empty = byId(emptyId);
  empty.hidden = Boolean(section.cards && section.cards.length);
  empty.textContent = empty.hidden ? "" : emptyMessage;
}

function describeGrowth(coverage, cards) {
  if (!coverage) return cards.length
    ? tr("展示已保存的增长候选项目；当前没有可用的覆盖统计。")
    : tr("还没有增长项目。首次更新会建立候选范围与增长记录。");
  if (coverage.metric_basis === "github_daily_new")
    return tr("已发现项目中的新增 Star 排名。数字来自 GitHub 可核实的每日统计。");
  if (coverage.metric_basis === "local_snapshot")
    return tr("已发现项目在本机两次观察之间的 Star 变化；它不是全站每日新增榜。");
  return tr("已发现的候选项目正在建立增长记录；取得可靠数字后会显示增长。");
}

function describeCoverage(coverage) {
  if (!coverage) return tr("覆盖范围尚未建立；该列表不能代表 GitHub 全站绝对排名。");
  const sourceLabels = {search: "GitHub 搜索", trending: "热门项目", tracked: "已跟踪项目"};
  const sources = (coverage.source_names || []).map((name) => tr(sourceLabels[name] || name));
  return language === "en"
    ? `${coverage.candidate_count} candidates found · ${coverage.scored_count} verified · `
      + `Statistical date ${coverage.stat_date || tr("待建立")} · Sources ${sources.join(", ") || tr("待建立")}`
    : `发现候选 ${coverage.candidate_count} 个 · 成功核算 ${coverage.scored_count} 个 · `
      + `统计日 ${coverage.stat_date || "待建立"} · 来源 ${sources.join("、") || "待建立"}`;
}

function renderKeywordGroups(issue) {
  const container = byId("keyword-groups");
  const groups = issue.keyword_groups || [];
  const signature = JSON.stringify([language, groups.map((group) => [group.keyword_id, group.term, group.cards])]);
  const cardsChanged = signature !== keywordCardSignature;
  const focusedKeywordId = [...keywordGroupNodes].find(([, nodes]) =>
    document.activeElement === nodes.button)?.[0];
  if (cardsChanged) {
    clear(container);
    keywordGroupNodes = new Map();
    keywordCardSignature = signature;
    for (const group of groups) {
      const section = element("section", "keyword-group");
      section.setAttribute("aria-label", `${tr("关键词")}${language === "en" ? ": " : "："}${group.term}`);
      const heading = element("div", "keyword-group-heading");
      const title = element("h3", "keyword-group-title", group.term);
      const count = element("p", "keyword-group-count");
      const button = element("button", "button button-quiet ai-refine-button");
      button.type = "button";
      button.addEventListener("click", () => startRefine(group.keyword_id));
      heading.append(title, count, button);
      const status = element("p", "ai-group-status");
      status.setAttribute("tabindex", "-1");
      const cards = element("div", "card-grid");
      for (const card of group.cards || []) cards.append(cardNode(card));
      section.append(heading, status, cards);
      if (!(group.cards || []).length) section.append(element("p", "empty-state",
        tr("这一组暂时没有符合条件的项目；不会为了凑满 5 个降低门槛。")));
      container.append(section);
      keywordGroupNodes.set(group.keyword_id, {section, count, button, status});
    }
  }
  const empty = byId("keyword-empty");
  empty.hidden = groups.length > 0;
  empty.textContent = groups.length ? "" : issue.keywords.length
    ? tr("关键词正在准备推荐结果。") : tr("添加一个关键词后，这里会显示你关注的项目。");
  for (const group of groups) {
    const {count, button, status} = keywordGroupNodes.get(group.keyword_id);
    count.textContent = language === "en"
      ? `${(group.cards || []).length} / 5 projects · ${group.checked_count || 0} candidates checked`
      : `${(group.cards || []).length} / 5 个项目 · 已检查 ${group.checked_count || 0} 个候选`;
    const verifiedFull = (group.cards || []).length >= 5
      && (group.cards || []).every(card => card.ai_status === "relevant");
    button.hidden = verifiedFull;
    button.textContent = tr((group.cards || []).length >= 5 ? "AI 核实" : "AI 核实并补齐");
    button.disabled = !aiConnection.ready || issue.busy || issue.ai_busy || quitting
      || verifiedFull;
    status.className = `ai-group-status ${group.ai_status || "idle"}`;
    status.textContent = group.ai_status === "running" ? tr("AI 正在检查本组候选，现有卡片继续显示。")
      : group.ai_status === "error" ? (language === "en" ? `Review failed: ${localizeServerText(group.ai_message || "请稍后重试")}` : `本组分析失败：${group.ai_message || "请稍后重试"}`)
      : group.ai_status === "done" ? (language === "en" ? `Review complete. ${group.checked_count || 0} candidates checked.` : `本组分析完成。已检查 ${group.checked_count || 0} 个候选。`)
      : verifiedFull ? tr("本组五个项目均已核实。")
      : group.checked_count ? tr("可手动核实下一批候选，补齐符合条件的新项目。") : tr("当前为基础匹配；点击后才进行 AI 核实。");
    if (["running", "done", "error"].includes(group.ai_status)) {
      const announcement = language === "en" ? `Keyword “${group.term}”: ${status.textContent}` : `关键词「${group.term}」：${status.textContent}`;
      if (announcement !== lastAiAnnouncement) {
        byId("ai-live").textContent = announcement;
        lastAiAnnouncement = announcement;
      }
    }
  }
  if (cardsChanged && focusedKeywordId !== undefined) {
    const restored = keywordGroupNodes.get(focusedKeywordId);
    if (restored) (restored.button.disabled || restored.button.hidden ? restored.status : restored.button).focus();
  }
}

function selectHomeSection(key,{closeMenu=true,animate=false}={}) {
  if(animate&&key!==homeSection)return transitionPage(()=>selectHomeSection(key,{closeMenu}),
    {focus:false,scope:()=>byId(homeSection==='growth'?'growth-results':'keyword-results')});
  homeSection = homeTabNodes.has(key) ? key : 'growth';
  const growth = homeSection === 'growth';
  byId('growth-results').hidden = !growth;
  byId('keyword-results').hidden = growth;
  const keywordId = growth ? null : Number(homeSection.slice(8));
  const group = latestIssue?.keyword_groups?.find(g=>g.keyword_id===keywordId);
  const rule = latestIssue?.keywords?.find(k=>k.id===keywordId);
  byId('recommendation-title').textContent = rule?.term || tr('关键词推荐');
  byId('keyword-count').textContent = language==='en' ? `${group?.cards?.length||0} projects` : `${group?.cards?.length||0} 个项目`;
  for(const [id,nodes] of keywordGroupNodes) nodes.section.hidden = id!==keywordId;
  byId('keyword-empty').hidden = !!group;
  for(const [id,nodes] of homeTabNodes) {
    nodes.tab.setAttribute('aria-selected',String(id===homeSection));
    nodes.tab.tabIndex = id===homeSection ? 0 : -1;
    nodes.choice?.setAttribute('aria-pressed',String(id===homeSection));
  }
  if(closeMenu || key!==homeSection)byId('home-section-more').open=false;
  translatePage();
}

function layoutHomeSections() {
  const bar=byId('home-section-bar'),more=byId('home-section-more');
  const width=bar.clientWidth;if(!width)return;
  const tabs=[...homeTabNodes.values()].map(n=>n.tab);
  for(const tab of tabs)tab.hidden=false;
  const sizes=tabs.map(n=>n.getBoundingClientRect().width);
  const hasKeywords=tabs.length>1;
  more.hidden=!hasKeywords;if(!hasKeywords)more.open=false;
  const available=width-(hasKeywords?40:0);let used=0;
  tabs.forEach((tab,i)=>{used+=sizes[i];tab.hidden=i>0&&used>available;});
  const selected=homeTabNodes.get(homeSection)?.tab;
  if(selected?.hidden){tabs[0].tabIndex=0;more.setAttribute('data-selected','true');}
  else more.setAttribute('data-selected','false');
}

function renderHomeSections(issue) {
  const entries=[{key:'growth',label:tr('Star 增长')},...issue.keywords.filter(k=>k.enabled!==false)
    .map(k=>({key:'keyword:'+k.id,label:k.term}))];
  const signature=JSON.stringify([language,entries]);
  if(signature!==homeTabSignature){
    homeTabSignature=signature;homeTabNodes=new Map();clear(byId('home-section-tabs'));clear(byId('home-section-menu'));
    for(const entry of entries){
      const tab=element('button','home-tab',entry.label);tab.type='button';tab.id='home-tab-'+entry.key.replace(':','-');
      tab.setAttribute('role','tab');tab.setAttribute('aria-controls',entry.key==='growth'?'growth-results':'keyword-results');
      tab.setAttribute('data-translation-literal','');
      tab.addEventListener('click',()=>selectHomeSection(entry.key,{animate:true}));
      tab.addEventListener('keydown',e=>{
        const visible=[...homeTabNodes.entries()].filter(([,n])=>!n.tab.hidden);
        const i=visible.findIndex(([key])=>key===entry.key);let next;
        if(e.key==='ArrowRight')next=visible[(i+1)%visible.length];
        if(e.key==='ArrowLeft')next=visible[(i-1+visible.length)%visible.length];
        if(e.key==='Home')next=visible[0];if(e.key==='End')next=visible.at(-1);
        if(next){e.preventDefault();selectHomeSection(next[0],{animate:true});next[1].tab.focus();}
      });
      let choice=null;
      if(entry.key!=='growth'){
        choice=element('button','home-menu-choice',entry.label);choice.type='button';choice.setAttribute('data-translation-literal','');
        choice.addEventListener('click',()=>{selectHomeSection(entry.key,{animate:true});const target=tab.hidden?byId('home-section-more-toggle'):tab;target.focus();});
        byId('home-section-menu').append(choice);
      }
      homeTabNodes.set(entry.key,{tab,choice});byId('home-section-tabs').append(tab);
    }
  }
  selectHomeSection(homeSection,{closeMenu:false});requestAnimationFrame(layoutHomeSections);
}

window.addEventListener('resize',layoutHomeSections);
if(typeof ResizeObserver==='function')new ResizeObserver(layoutHomeSections).observe(byId('home-section-bar'));
document.fonts?.ready?.then(layoutHomeSections);
document.addEventListener('click',e=>{if(!e.target?.closest?.('#home-section-more'))byId('home-section-more').open=false;});
byId('home-section-more').addEventListener('keydown',e=>{if(e.key==='Escape'){byId('home-section-more').open=false;byId('home-section-more-toggle').focus();}});

function renderModelOptions() {
  for (const id of ["ai-model", "detail-ai-model"]) {
    const select = byId(id);
    clear(select);
    const auto = element("option", "", tr("自动选择"));
    auto.value = "";
    select.append(auto);
    for (const model of aiConnection.models || []) {
      const option = element("option", "", model.display_name || model.id);
      option.value = model.id;
      select.append(option);
    }
    select.value = aiConnection.selected_model || "";
    select.disabled = quitting || !(aiConnection.models || []).length;
  }
  byId("ai-connection-status").textContent = localizeServerText(aiConnection.reason || "AI 连接不可用");
  byId('settings-codex-status').textContent=localizeServerText(aiConnection.reason || 'AI 连接不可用');
  byId('settings-codex-connect').hidden=Boolean(aiConnection.ready);
  byId('settings-codex-auto').hidden=aiConnection.ready || !(aiConnection.models || []).length || !aiConnection.selected_model;
  byId("ai-connect").hidden = Boolean(aiConnection.ready);
  byId("detail-ai-connect").hidden = Boolean(aiConnection.ready);
  byId("detail-ai-explain").disabled = quitting || !aiConnection.ready;
  if (latestIssue) renderKeywordGroups(latestIssue);
}

async function loadAIStatus() {
  try {
    const status = await api("/api/ai/status");
    aiConnection = {ready: Boolean(status.ready), reason: status.login_pending && !status.ready ? tr('请在新打开的页面完成 Codex 登录。') : status.reason || "AI 连接不可用",
      models: status.models || [], selected_model: status.selected_model || null};
    renderModelOptions();
    if (aiPollTimer) clearTimeout(aiPollTimer);
    if (status.login_pending && !status.ready && !quitting)
      aiPollTimer = setTimeout(loadAIStatus, 1800);
  } catch (error) {
    aiConnection = {ready: false, reason: error.message, models: [], selected_model: null};
    renderModelOptions();
  }
}

async function connectAI() {
  const loginTab = window.open ? window.open("", "_blank") : null;
  byId("ai-live").textContent = tr("正在打开 Codex 账号登录…");
  try {
    const result = await post("/api/ai/login");
    const url = new URL(result.auth_url);
    if (url.protocol !== "https:") throw new Error(tr("登录地址无效"));
    if (loginTab) loginTab.location.href = url.href;
    else window.location.assign(url.href);
    byId("ai-live").textContent = tr("请在新打开的页面完成 Codex 登录。");
    await loadAIStatus();
  } catch (error) {
    if (loginTab) loginTab.close();
    byId("ai-live").textContent = localizeServerText(error.message);
    byId("detail-ai-status").textContent = localizeServerText(error.message);
    byId('settings-codex-status').textContent=localizeServerText(error.message);
  }
}

async function checkAIConnection() {
  const button=byId('settings-codex-check');button.disabled=true;
  byId('settings-codex-status').textContent=tr('正在检查 AI 连接…');
  try {await loadAIStatus();} finally {button.disabled=quitting;}
}

async function changeModel(event) {
  const modelId = event.target.value || null;
  try {
    await post("/api/ai/model", {model_id: modelId});
    aiConnection.selected_model = modelId;
    await loadAIStatus();
    byId("ai-live").textContent = tr(modelId ? "已切换 AI 模型；下次点击 AI 按钮时使用。" : "已切换为自动选择。");
    await loadIssue();
    if (detailRepoId && !byId("detail-view").hidden) await showDetail(detailRepoId);
  } catch (error) {
    byId("ai-live").textContent = localizeServerText(error.message);
    byId('settings-codex-status').textContent=localizeServerText(error.message);
    renderModelOptions();
  }
}

async function startRefine(keywordId) {
  byId("ai-live").textContent = tr("正在启动 AI 精选；现有卡片会保留。");
  try {
    await post(`/api/ai/keywords/${encodeURIComponent(keywordId)}/refine`);
    await loadIssue();
  } catch (error) {
    byId("ai-live").textContent = localizeServerText(error.message);
  }
}

function renderIssue(issue) {
  latestIssue = issue;
  const growth = issue.sections.find((section) => section.id === "growth") || { cards: [] };
  const keyword = issue.sections.find((section) => section.id === "keyword") || { cards: [] };
  const busy = Boolean(issue.busy || issue.ai_busy);
  byId("refresh-button").disabled = busy || quitting;
  byId('refresh-button').textContent=tr(issue.busy?'正在更新…':'立即更新');
  byId('refresh-button').setAttribute('aria-busy',String(Boolean(issue.busy)));
  byId("keyword-input").disabled = busy || quitting;
  byId("keyword-form").querySelector("button").disabled = busy || quitting;
  setStatus(issue.message, issue.status === "error" ? "error" : busy ? "busy" : "");
  byId("saved-time").textContent = issue.updated_at
    ? (language === "en" ? `Saved date: ${issue.local_date} · Last update: ${issue.updated_at.replace("T", " ")}`
      : `保存日期：${issue.local_date} · 最近更新：${issue.updated_at.replace("T", " ")}`)
    : tr("尚无保存的数据");
  byId("keyword-list").textContent = issue.keywords.length
    ? issue.keywords.map((item) => item.term).join(language === "en" ? ", " : "、") : tr("暂无");
  const projectCount = (n) => language === "en" ? `${n} ${n === 1 ? "project" : "projects"}` : `${n} 个项目`;
  const returning = growth.cards.filter(card => card.display_role === "old");
  const discoveries = growth.cards.filter(card => card.display_role !== "old");
  byId("growth-count").textContent = projectCount(discoveries.length);
  byId("keyword-count").textContent = projectCount(keyword.cards.length);
  byId("growth-explainer").textContent = describeGrowth(issue.coverage, growth.cards);
  byId("coverage-line").textContent = describeCoverage(issue.coverage);
  byId("growth-returning").hidden = !returning.length;
  renderProjectList(byId("growth-returning-cards"),returning);
  renderCards({cards: discoveries}, "growth-cards", "growth-empty",
    tr(busy ? "正在寻找新项目，已保存的结果会继续显示在这里。" : "暂时没有符合条件的增长项目。可以点击“立即更新”。"));
  renderKeywordGroups(issue);
  renderHomeSections(issue);
  const shortage = [];
  if (growth.cards.length > 0 && discoveries.length < 5) shortage.push(language === "en"
    ? `Growth has ${discoveries.length} of 5 new projects` : `增长区新项目目前 ${discoveries.length} 个，不足 5 个`);
  for (const group of issue.keyword_groups || [])
    if (group.cards.length < 5) shortage.push(language === "en"
      ? `“${group.term}” has ${group.cards.length} of 5 projects` : `「${group.term}」目前 ${group.cards.length} 个，不足 5 个`);
  byId("issue-notes").textContent = [...new Set([...(issue.notes || []).map(localizeServerText), ...shortage])].join(" · ");
  if (pollTimer) clearTimeout(pollTimer);
  if (busy && !quitting) pollTimer = setTimeout(loadIssue, 1200);
  translatePage();
}

async function loadIssue() {
  try {
    const issue = await api("/api/issue");
    renderIssue(issue);
    historyLoaded = false;
    if (issue.status === "empty" && !didInitialRefresh && location.pathname === "/") {
      didInitialRefresh = true;
      await startRefresh();
    }
  } catch (error) {
    setStatus(error.message, "error");
    byId("refresh-button").disabled = !token;
    if(!quitting){if(pollTimer)clearTimeout(pollTimer);pollTimer=setTimeout(loadIssue,1200);}
  }
}

async function pollSavedIssue() {
  if (quitting) return;
  await loadIssue();
  if (!quitting) savedIssueTimer = setTimeout(pollSavedIssue, 60000);
}

async function startRefresh() {
  if (quitting) return;
  byId("refresh-button").disabled = true;
  setStatus(tr("正在连接 GitHub，已保存项目仍可浏览…"), "busy");
  try {
    await post("/api/refresh");
    await loadIssue();
  } catch (error) {
    setStatus(error.message, "error");
    byId("refresh-button").disabled = false;
  }
}

async function addKeyword(event) {
  event.preventDefault();
  const input = byId("keyword-input");
  const term = input.value.trim();
  if (!term) { setFeedback(tr("请输入关键词。"), true); input.focus(); return; }
  setFeedback(tr("正在保存关键词…"));
  try {
    const result = await post("/api/keywords", { term });
    input.value = "";
    setFeedback(language === "en" ? `Added “${result.keyword.term}”. Looking for projects.`
      : `已关注“${result.keyword.term}”，正在寻找项目。`);
    await loadIssue();
  } catch (error) {
    setFeedback(error.message, true);
  }
}

function safeGitHubUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "github.com" ? url.href : null;
  } catch { return null; }
}

function appendMetric(label, accent = false) {
  if (!label) return;
  byId("detail-metrics").append(element("span", accent ? "detail-metric accent" : "detail-metric", label));
}

let detailAIContentKey=null;
function clearAIExplanation() {
  detailAIContentKey=null;
  detailHasAnalysis=false;
  for (const id of ['detail-ai-problem','detail-ai-prerequisites','detail-ai-scenarios','detail-ai-users'])
    byId(id).textContent=tr('待生成项目理解');
  byId('detail-ai-explain').title=tr('点击后会把这个项目的公开 GitHub 资料发送给 Codex，并消耗账号使用额度。');
  clear(byId("detail-ai-highlights"));
  byId('detail-ai-highlights').append(element('li','',tr('待生成项目理解')));
  byId("detail-ai-status").textContent = "";
  byId("detail-ai-explain").textContent = tr("生成项目理解");
}

function renderAIExplanation(detail, prepared=null) {
  const saved = detail.ai_explanation;
  const contentKey=JSON.stringify([detail.repo_id,language,saved]);
  const changed=contentKey!==detailAIContentKey;
  if(changed){clearAIExplanation();detailAIContentKey=contentKey;}
  detailHasAnalysis=Boolean(saved);detailAnalysisStale=Boolean(detail.analysis_stale);
  byId("detail-ai-explain").textContent = tr(saved ? "重新生成项目理解" : "生成项目理解");
  const insight = saved && (saved[language] || saved.zh);
  if (insight) {
    byId("detail-ai-explain").title = language === "en"
      ? `AI generated · ${saved.model_id || tr("自动选择")} · Saved ${saved.saved_at || tr("本机")}`
        + (saved.source_limited ? ` · ${tr("公开资料有限")}` : "")
      : `AI 生成 · ${saved.model_id || "自动选择"} · 保存于 ${saved.saved_at || "本机"}`
        + (saved.source_limited ? " · 公开资料有限" : "");
    if(changed){
    for(const [field,id] of Object.entries({problem:'detail-ai-problem',prerequisites:'detail-ai-prerequisites',scenarios:'detail-ai-scenarios',users:'detail-ai-users'}))
      if(prepared)translationController.adopt(prepared[field],byId(id));else byId(id).textContent=insight[field] || tr('待生成项目理解');
    if(insight.highlights?.length)clear(byId('detail-ai-highlights'));
    if(prepared)translationController.adopt(prepared.highlights,byId('detail-ai-highlights'));
    else for (const value of (insight.highlights || []).slice(0,3))
      byId("detail-ai-highlights").append(translatable(element("li", "", value)));
    }
  }
  const job = detail.ai_job;
  byId("detail-ai-status").textContent = job && job.status === "running"
    ? tr("AI 正在解读这个项目；已保存资料继续可读。")
    : job && job.status === "error" ? (language === "en" ? `AI explanation failed: ${localizeServerText(job.message)}` : `AI 解读失败：${job.message}`)
    : saved ? tr(detailAnalysisStale?'已保存的理解来自旧版本，缺失信息可手动重新生成。':'已显示本机保存的项目理解。')
    : aiConnection.ready ? tr("点击“生成项目理解”后才会调用 AI。")
    : (language === "en" ? `AI unavailable: ${localizeServerText(aiConnection.reason)}` : `AI 暂不可用：${aiConnection.reason}`);
  byId("detail-ai-explain").disabled = quitting || !aiConnection.ready || Boolean(job && job.status === "running");
  if (detailPollTimer) clearTimeout(detailPollTimer);
  if (job && job.status === "running" && !quitting)
    detailPollTimer = setTimeout(() => refreshDetailAI(detail.repo_id), 1200);
  translatePage();
}

async function refreshDetailAI(repoId) {
  const routeKey=location.pathname+location.search;
  const targetLanguage=language, targetOriginal=translationOriginal;
  const current=()=>!quitting&&location.pathname===`/project/${repoId}`&&location.pathname+location.search===routeKey&&language===targetLanguage&&translationOriginal===targetOriginal;
  try {
    const detail = await api(`/api/project/${encodeURIComponent(repoId)}${location.search}`);
    if(!current())return;
    const insight=detail.ai_explanation&&(detail.ai_explanation[language]||detail.ai_explanation.zh);
    if(!insight||JSON.stringify([detail.repo_id,language,detail.ai_explanation])===detailAIContentKey){renderAIExplanation(detail);return;}
    const root=element('div'),prepared={};
    for(const field of ['problem','prerequisites','scenarios','users']){prepared[field]=translatable(element('p','',insight[field]||tr('待生成项目理解')));root.append(prepared[field]);}
    prepared.highlights=element('ul');for(const value of (insight.highlights||[]).slice(0,3))prepared.highlights.append(translatable(element('li','',value)));root.append(prepared.highlights);
    await translationController.prepare(root);
    if(current())renderAIExplanation(detail,prepared);
  } catch (error) {
    if (location.pathname === `/project/${repoId}` && location.pathname+location.search===routeKey)
      byId("detail-ai-status").textContent = localizeServerText(error.message);
  }
}

async function explainProject() {
  if (!detailRepoId || quitting) return;
  const repoId = detailRepoId;
  const routeKey=location.pathname+location.search;
  byId("detail-ai-status").textContent = tr("正在启动 AI 解读；项目原始资料继续可读。");
  byId("detail-ai-explain").disabled = true;
  try {
    await post(`/api/ai/projects/${encodeURIComponent(repoId)}/explain`,
      {force:detailHasAnalysis || detailAnalysisStale,
       context_date:new URLSearchParams(location.search).get('date') || new URLSearchParams(location.search).get('context') || null});
    if(location.pathname+location.search===routeKey)await refreshDetailAI(repoId);
  } catch (error) {
    if(location.pathname+location.search!==routeKey)return;
    byId("detail-ai-status").textContent = localizeServerText(error.message);
    byId("detail-ai-explain").disabled = !aiConnection.ready;
  }
}

function historyProjectGrid(cards){
  const scroll=element('div','history-results-scroll'),grid=element('div','history-project-grid');
  scroll.tabIndex=0;scroll.setAttribute('aria-label',tr('历史项目'));
  for(const card of cards)grid.append(cardNode(card,{compact:true}));scroll.append(grid);return scroll;
}
async function loadHistoryDate(day,content){
  const generation=historyDayGeneration,version=historySearch.version();
  const current=()=>location.pathname==='/history'&&generation===historyDayGeneration&&version===historySearch.version();
  try{
    const saved=await historySearch.day(day);if(!saved||!current())return;
    clear(content);
    const growth=saved.sections.find(section=>section.id==='growth')||{cards:[]};
    if(growth.cards.length){content.append(element('h3','history-section-title',tr('Star 增长')),historyProjectGrid(growth.cards));}
    for(const group of saved.keyword_groups||[]){
      content.append(element('h3','history-section-title',tr('关键词')+'：'+group.term),historyProjectGrid(group.cards));
    }
    if(!content.children.length)content.append(element('p','empty-state',tr('这一天没有保存项目。')));
    translatePage();
  }catch(error){if(!current())return;content.textContent=localizeServerText(error.message);
    const retry=element('button','button button-quiet',tr('重试'));retry.type='button';retry.addEventListener('click',()=>loadHistoryDate(day,content));content.append(retry);}
}
function clearHistoryDay(){
  historyDayGeneration++;clear(byId('history-date-groups'));byId('history-empty').hidden=true;byId('history-status').textContent=tr('选择日期查看历史项目。');
}
function historyDateGroup(day){
  const group=element('section','history-date-group');group.append(element('h2','history-date-title',day));
  const content=element('div','history-date-content');group.append(content);byId('history-date-groups').append(group);return content;
}
async function showCalendarDay(day){
  if(location.pathname!=='/history'||window.RadarCalendar.hasFilters(historySearch.filters()))return;
  return transitionPage(()=>{history.pushState(history.state,'','/history?day='+encodeURIComponent(day));return renderHistoryDayPage(day);});
}
function historyDayMode(active){
  byId('history-view').setAttribute('data-mode',active?'day':'calendar');
  byId('history-day-label').hidden=!active;byId('history-kicker').hidden=active;
  byId('history-search-form').hidden=active;byId('history-calendar').hidden=active;
  byId('history-intro').hidden=active;byId('history-calendar-back').hidden=!active;
}
async function renderHistoryDayPage(day){
  byId('history-day-label').textContent=day;
  historyDayMode(true);historyCalendar.invalidate();translationController.invalidate();
  clearHistoryDay();byId('history-status').textContent='';byId('history-more').hidden=true;
  await loadHistoryDate(day,historyDateGroup(day));
  if(location.pathname!=='/history'||new URLSearchParams(location.search).get('day')!==day)return;
  historyLoaded=true;historyLoadedQuery=location.search;window.scrollTo(0,0);byId('history-view').focus();
}
function historyInputs(value){
  for(const [key,id] of [['q','query'],['from','from'],['to','to'],['source','source']])byId('history-'+id).value=value[key]||(key==='source'?'all':'');
}
function historyInputValue(){return {q:byId('history-query').value,from:byId('history-from').value,to:byId('history-to').value,source:byId('history-source').value};}
async function renderHistoryIndex(data,append,version){
  if(location.pathname!=='/history'||version!==historySearch.version())return;
  const day=new URLSearchParams(location.search).get('day');
  if(day&&!window.RadarCalendar.hasFilters(historySearch.filters()))return renderHistoryDayPage(day);
  historyDayMode(false);
  const active=window.RadarCalendar.hasFilters(historySearch.filters()),dates=data.dates||[],container=byId('history-date-groups');
  historyCalendar.setSearch(active);
  if(!append){clear(container);const current=byId('history-source').value||historySearch.filters().source;clear(byId('history-source'));
    const sources=data.sources||[{value:'all',label:'全部来源'}];
    for(const source of sources){const option=element('option','',source.value==='all'?tr('全部来源'):source.value==='growth'?tr('Star 增长'):source.label);option.value=source.value;byId('history-source').append(option);}
    if(!sources.some(source=>source.value===current)){const option=element('option','',current);option.value=current;byId('history-source').append(option);}
    byId('history-source').value=current;
  }
  byId('history-more').hidden=!active||data.next_cursor==null;
  byId('history-empty').hidden=!active||Boolean(container.children.length||dates.length);
  byId('history-empty').textContent=tr(historySearch.emptyMessage(data));
  if(active){
    byId('history-status').textContent=language==='en'?`${data.matched_total??dates.length} matching saved projects`:`匹配 ${data.matched_total??dates.length} 条保存记录`;
    await Promise.all(dates.map(row=>loadHistoryDate(row.date,historyDateGroup(row.date))));
  }else{
    await historyCalendar.load();if(version!==historySearch.version()||location.pathname!=='/history')return;
    clearHistoryDay();
  }
  if(version!==historySearch.version())return;historyLoaded=true;historyLoadedQuery=location.search;translatePage();
}
async function loadHistory(){
  const value=historySearch.readURL(location.search);historyInputs(value);historyCalendar.setSearch(window.RadarCalendar.hasFilters(value));
  try{await historySearch.search(value,{write:false});}catch(error){byId('history-status').textContent=localizeServerText(error.message);}
}
async function submitHistory(event){
  event.preventDefault();historyDayGeneration++;historyLoaded=false;translationController.invalidate();
  const value=historyInputValue();historyCalendar.setSearch(window.RadarCalendar.hasFilters(value));clear(byId('history-date-groups'));byId('history-more').hidden=true;byId('history-empty').hidden=true;
  byId('history-status').textContent=tr('正在读取历史…');
  try{await historySearch.search(value);}catch(error){byId('history-status').textContent=localizeServerText(error.message);}
}
async function clearHistory(){historyInputs({});return submitHistory({preventDefault(){}});}

async function loadFollowing(){return followingBoard.load();}
function renderFolderChoices(){clear(byId('detail-folder-choices'));
  for(const folder of folderSelection.folders()){
    const label=element('label','folder-choice'),input=element('input');input.type='checkbox';input.checked=folderSelection.ids().includes(folder.id);
    input.addEventListener('change',()=>folderSelection.select(folder.id,input.checked));label.append(input,element('span','',folder.name));byId('detail-folder-choices').append(label);
  }
}
async function loadDetailFolders(repoId,followed){
  const version=++folderGeneration;folderSelection.unfollow();byId('detail-folders').hidden=!followed;
  byId('detail-folder-feedback').textContent='';clear(byId('detail-folder-choices'));byId('detail-folder-save').disabled=true;
  if(!followed)return;
  try{const data=await api('/api/following/'+encodeURIComponent(repoId)+'/folders');
    if(version!==folderGeneration||detailRepoId!==repoId)return;
    folderSelection.load(repoId,data.ids||[],data.folders||[]);renderFolderChoices();byId('detail-folder-save').disabled=false;
  }catch(error){if(version===folderGeneration){byId('detail-folder-feedback').textContent=error.message;
    const retry=element('button','button button-quiet',tr('重试'));retry.type='button';retry.addEventListener('click',()=>loadDetailFolders(repoId,true));byId('detail-folder-feedback').append(retry);}}
}
async function saveDetailFolders(event){event.preventDefault();const version=folderGeneration,button=byId('detail-folder-save');button.disabled=true;
  try{if(await folderSelection.save()&&version===folderGeneration)byId('detail-folder-feedback').textContent=tr('已保存。');}
  catch(error){if(version===folderGeneration)byId('detail-folder-feedback').textContent=error.message;}
  finally{if(version===folderGeneration)button.disabled=false;}
}
async function createDetailFolder(event){event.preventDefault();const version=folderGeneration,input=byId('detail-folder-name'),button=byId('detail-folder-create-form').querySelector('button');button.disabled=true;
  try{const folder=await folderSelection.create(input.value);if(folder&&version===folderGeneration){input.value='';renderFolderChoices();byId('detail-folder-feedback').textContent=tr('已创建并选中，请保存分类。');}}
  catch(error){if(version===folderGeneration)byId('detail-folder-feedback').textContent=error.message;}
  finally{button.disabled=false;}
}

function settingsFeedback(message, error = false) {
  const feedback = byId("settings-feedback");
  feedback.textContent = localizeServerText(message);
  feedback.className = error ? "form-feedback error" : "form-feedback";
}

function autoUpdateFeedback(message, error = false) {
  const feedback = byId("auto-update-feedback");
  feedback.textContent = localizeServerText(message);
  feedback.className = error ? "form-feedback error" : "form-feedback";
}

function renderAutoUpdate(state, updateControls = true) {
  autoUpdateState = state;
  if (updateControls) {
    byId("auto-update-enabled").checked = state.enabled;
    byId("auto-update-time").value = state.time;
  }
  byId("auto-update-task-status").textContent = state.task_error
    ? (language === "en" ? `Windows schedule needs attention: ${localizeServerText(state.task_error)}`
      : `Windows 定时任务需要处理：${state.task_error}`)
    : state.enabled
      ? tr(state.registered ? "定时任务已启用" : "定时任务尚未启用，手动更新仍可使用")
      : tr("每日更新已暂停");
  byId("auto-update-last-success").textContent = state.last_success_at
    ? `${tr("上次成功")}${language === "en" ? ": " : "："}${state.last_success_at.replace("T", " ")}`
    : tr("还没有成功更新记录");
  const attempt = state.last_attempt || {};
  byId("auto-update-last-attempt").textContent = attempt.last_attempt_at
    ? `${tr("最近尝试")}${language === "en" ? ": " : "："}${attempt.last_attempt_at.replace("T", " ")}`
      + (attempt.reason ? ` · ${localizeServerText(attempt.reason)}` : "")
    : tr("还没有自动更新尝试");
}

async function loadAutoUpdate() {
  try { renderAutoUpdate(await api("/api/auto-update")); }
  catch (error) { autoUpdateFeedback(error.message, true); }
}

function validateAutoUpdateTime() {
  const input = byId("auto-update-time");
  const valid = /^([01]\d|2[01]):[0-5]\d$/.test(input.value);
  byId("auto-update-time-error").textContent = valid ? "" : tr("请选择 00:00–21:59 之间的时间。");
  input.setAttribute("aria-invalid", String(!valid));
  return valid;
}

async function saveAutoUpdate(event) {
  event.preventDefault();
  if (!validateAutoUpdateTime()) {
    byId("auto-update-time").focus();
    return;
  }
  const button = byId("auto-update-save");
  button.disabled = true;
  autoUpdateFeedback(tr("正在保存…"));
  try {
    const state = await post("/api/auto-update", {
      enabled: byId("auto-update-enabled").checked,
      time: byId("auto-update-time").value,
    });
    renderAutoUpdate(state);
    autoUpdateFeedback(tr("每日更新已保存。"));
  } catch (error) {
    if (autoUpdateState) renderAutoUpdate(autoUpdateState);
    autoUpdateFeedback(error.message, true);
    byId("auto-update-feedback").focus();
  } finally { button.disabled = false; }
}

function renderKeywordSettings(keywords) {
  const container = byId("settings-keywords");
  clear(container);
  byId("settings-empty").hidden = keywords.length > 0;
  for (const rule of keywords) {
    const row = element("section", "keyword-setting-row");
    row.setAttribute("aria-label", `${tr("关键词")}${language === "en" ? ": " : "："}${rule.term}`);
    const header = element("div", "keyword-setting-header");
    header.append(element("h3", "keyword-setting-term", rule.term));
    const toggle = element("button", "button button-quiet keyword-toggle",
      tr(rule.enabled ? "已开启" : "已暂停"));
    toggle.type = "button";
    toggle.setAttribute("aria-pressed", String(Boolean(rule.enabled)));
    header.append(toggle);

    const controls = element("div", "keyword-setting-controls");
    const inputId = `minimum-star-${rule.id}`;
    const label = element("label", "", tr("最低 Star"));
    label.setAttribute("for", inputId);
    const input = element("input", "keyword-minimum");
    input.id = inputId;
    input.type = "number";
    input.min = "0";
    input.step = "1";
    input.value = String(rule.min_stars);
    const save = element("button", "button button-quiet keyword-save", tr("保存门槛"));
    save.type = "button";
    const remove = element("button", "button button-quiet keyword-delete", tr("删除关键词"));
    remove.type = "button";
    controls.append(label, input, save, remove);
    row.append(header, controls);
    container.append(row);

    let busy = false;
    const setBusy = (value) => {
      busy = value;
      for (const control of [toggle, input, save, remove]) control.disabled = value;
    };
    toggle.addEventListener("click", async () => {
      if (busy) return;
      setBusy(true);
      settingsFeedback(tr("正在保存…"));
      try {
        const next = !rule.enabled;
        await post(`/api/keywords/${rule.id}/settings`, {enabled: next});
        rule.enabled = next;
        toggle.textContent = tr(next ? "已开启" : "已暂停");
        toggle.setAttribute("aria-pressed", String(next));
        settingsFeedback(tr("关键词状态已保存。"));
        await loadIssue();
      } catch (error) { settingsFeedback(error.message, true); }
      finally { setBusy(false); }
    });
    save.addEventListener("click", async () => {
      if (busy) return;
      const raw = input.value.trim();
      if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw))) {
        settingsFeedback(tr("最低 Star 请输入非负整数。"), true);
        input.value = String(rule.min_stars);
        return;
      }
      setBusy(true);
      settingsFeedback(tr("正在保存…"));
      try {
        const minStars = Number(raw);
        await post(`/api/keywords/${rule.id}/settings`, {min_stars: minStars});
        rule.min_stars = minStars;
        settingsFeedback(tr("最低 Star 已保存。"));
      } catch (error) {
        input.value = String(rule.min_stars);
        settingsFeedback(error.message, true);
      } finally { setBusy(false); }
    });
    remove.addEventListener("click", async () => {
      if (busy || !window.confirm(language === "en"
        ? `Remove keyword “${rule.term}”? Home recommendations will stop; history stays saved.`
        : `删除关键词「${rule.term}」？首页将停止推荐，历史记录会保留。`)) return;
      setBusy(true);
      settingsFeedback(tr("正在删除…"));
      try {
        await post(`/api/keywords/${rule.id}/delete`);
        settingsFeedback(language === "en" ? `Removed “${rule.term}”; history was kept.` : `已删除「${rule.term}」，历史记录已保留。`);
        await loadSettings(false);
        await loadIssue();
        setFeedback("");
      } catch (error) {
        settingsFeedback(error.message, true);
        setBusy(false);
      }
    });
  }
}


let githubPollTimer = null;
async function loadGitHubAccount() {
  if (githubPollTimer) clearTimeout(githubPollTimer);
  try {
    const state = await api("/api/github/status");
    byId("github-status").textContent = state.state === "connected"
      ? (language === "en" ? `Connected to GitHub: ${state.login}` : `GitHub 已连接：${state.login}`)
      : localizeServerText(state.reason || "GitHub 未连接");
    byId("github-connect").disabled = state.state === "unavailable" || state.state === "pending" || quitting;
    byId("github-disconnect").hidden = state.state !== "connected";
    if (state.state === "connected" || state.state === "expired") byId("github-authorization").hidden = true;
    if (state.state === "pending" && !quitting) githubPollTimer = setTimeout(loadGitHubAccount, 2000);
  } catch (error) { byId("github-status").textContent = localizeServerText(error.message); }
}
async function connectGitHub() {
  const loginTab = window.open ? window.open("", "_blank") : null;
  byId("github-status").textContent = tr("正在打开 GitHub 官方授权页…");
  try {
    const auth = await post("/api/github/connect");
    if (auth.verification_uri !== "https://github.com/login/device") throw new Error(tr("登录地址无效"));
    byId("github-user-code").value = auth.user_code;
    byId("github-auth-link").href = auth.verification_uri;
    byId("github-authorization").hidden = false;
    if (loginTab) loginTab.location.href = auth.verification_uri;
    // If popups are blocked the visible official link remains available.
    await loadGitHubAccount();
  } catch (error) {
    if (loginTab) loginTab.close();
    byId("github-status").textContent = localizeServerText(error.message);
  }
}
async function loadSettings(clearFeedback = true) {
  if (clearFeedback) settingsFeedback("");
  await loadAutoUpdate();
  await loadGitHubAccount();
  try {
    const settings = await api("/api/settings");
    renderKeywordSettings(settings.keywords || []);
  } catch (error) { settingsFeedback(error.message, true); }
}

async function toggleFollow() {
  if (!detailRepoId || quitting) return;
  const button = byId("follow-button");
  button.disabled = true;
  try {
    const followed = button.getAttribute("aria-pressed") !== "true";
    await post(`/api/following/${encodeURIComponent(detailRepoId)}`, {followed});
    button.setAttribute("aria-pressed", String(followed));
    button.textContent = tr(followed ? "已关注" : "关注");
    await loadDetailFolders(detailRepoId,followed);
    historyLoaded=false;
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function cancelReadmePolling() {
  readmeGeneration++;
  if (readmePollTimer) clearTimeout(readmePollTimer);
  readmePollTimer = null;
}

function safeReadmeUrl(value, base) {
  try {
    const url = new URL(value, base);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function markdownInline(node, text, base) {
  text=text.replace(/\[!\[([^\]]*)\]\([^\s)]+\)\]\(([^\s)]+)\)/g,'[$1]($2)');
  const pattern = /(?<!`)(`+)([\s\S]*?)(?<!`)\1(?!`)|!?\[([^\]]+)\]\(([^\s)]+)\)/g;
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > end) node.append(element('span', '', text.slice(end, match.index)));
    if (match[1]) {
      let value=match[2];
      if (value.startsWith(' ') && value.endsWith(' ') && value.trim()) value=value.slice(1,-1);
      node.append(element('code','',value));
    }
    else {
      const url = safeReadmeUrl(match[4], base);
      if (url) {
        const link = element('a', 'readme-external-link');
        link.append(element('span','',match[3]));
        const decoration=element('span','readme-link-arrow',match[0][0] === '!' ? ` (${tr('图片链接')})` : ' ↗');
        decoration.setAttribute('data-translation-literal','');decoration.setAttribute('aria-hidden','true');link.append(decoration);
        link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.setAttribute('data-translate-link',''); node.append(link);
      } else node.append(element('span', '', match[3]));
    }
    end = match.index + match[0].length;
  }
  if (end < text.length) node.append(element('span', '', text.slice(end)));
}

function markdownNodes(text, container, base) {
  let paragraph = [], code = null, fence = '', list = null;
  const flushParagraph = () => {
    if (paragraph.length) { const p = translatable(element('p')); markdownInline(p, paragraph.join('\n'), base); container.append(p); paragraph = []; }
  };
  const lines=text.split('\n');
  const cells=line=>{
    const result=[];let cell='',ticks=0;
    for(let i=0;i<line.length;i++){
      if(line[i]==='\\'&&line[i+1]==='|'){cell+='|';i++;continue;}
      if(line[i]==='`'){let run=1;while(line[i+run]==='`')run++;ticks=ticks===run?0:ticks||run;cell+='`'.repeat(run);i+=run-1;continue;}
      if(line[i]==='|'&&!ticks){result.push(cell.trim());cell='';}else cell+=line[i];
    }
    result.push(cell.trim());if(!result[0])result.shift();if(!result.at(-1))result.pop();return result;
  };
  for(let lineIndex=0;lineIndex<lines.length;lineIndex++){
    const line=lines[lineIndex];
    const marker = /^\s*(`{3,}|~{3,})/.exec(line);
    if (code !== null) {
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length) {
        const pre = element('pre'); pre.append(element('code', '', code.join('\n'))); container.append(pre); code = null;
      } else code.push(line);
      continue;
    }
    if (marker) { flushParagraph(); list = null; code = []; fence = marker[1]; continue; }
    if(line.includes('|') && lines[lineIndex+1] && cells(lines[lineIndex+1]).length>=2 && cells(lines[lineIndex+1]).every(c=>/^:?-{3,}:?$/.test(c))){
      flushParagraph();list=null;
      const scroll=element('div','readme-table-scroll'),table=element('table'),thead=element('thead'),tbody=element('tbody');
      const row=(values,tag,parent)=>{const trNode=element('tr');for(const value of values){const cell=translatable(element(tag));if(tag==='th')cell.setAttribute('scope','col');markdownInline(cell,value,base);trNode.append(cell);}parent.append(trNode);};
      row(cells(line),'th',thead);lineIndex++;
      while(lines[lineIndex+1]?.trim() && lines[lineIndex+1].includes('|'))row(cells(lines[++lineIndex]),'td',tbody);
      table.append(thead,tbody);scroll.append(table);container.append(scroll);continue;
    }
    const heading = /^#{1,6}\s+(.+)/.exec(line);
    const bullet = /^\s*([-*+]|\d+[.)])\s+(.+)/.exec(line);
    if (heading) { flushParagraph(); list = null; const h = translatable(element('h3')); markdownInline(h, heading[1], base); container.append(h); }
    else if (bullet) {
      const tag=/^\d/.test(bullet[1])?'ol':'ul';
      flushParagraph(); if (!list || list.tagName.toLowerCase()!==tag) { list = element(tag);if(tag==='ol')list.setAttribute('start',parseInt(bullet[1],10));container.append(list); }
      const item = translatable(element('li')); markdownInline(item, bullet[2], base); list.append(item);
    } else if (!line.trim()) { flushParagraph(); list = null; }
    else { list = null; paragraph.push(line); }
  }
  flushParagraph();
  if (code !== null) { const pre = element('pre'); pre.append(element('code', '', code.join('\n'))); container.append(pre); }
}

let readmeRenderedKey=null;
function readmeContentKey(view){return JSON.stringify([view.document?.repo_id,view.document?.source_url,view.document?.content_hash || view.full_markdown || '',view.sections || []]);}
function fillReadmePurpose(container,view,base){
  clear(container);const purpose=(view.sections||[]).find(section=>section.kind==='purpose');
  if(purpose?.text)markdownNodes(purpose.text,container,base);else container.append(element('p','readme-unspecified',tr('README 未说明')));
}
async function renderPreparedReadme(view,current){
  if(readmeContentKey(view)===readmeRenderedKey){renderReadme(view);return;}
  const prepared=element('div');const source=safeGitHubUrl(view.document?.source_url);
  const base=source&&/^https:\/\/github\.com\/[^/]+\/[^/#]+(?:#readme)?$/.test(source)?source.replace(/#readme$/,'')+'/blob/HEAD/README.md':source||undefined;
  fillReadmePurpose(prepared,view,base);const targetLanguage=language,targetOriginal=translationOriginal;
  await translationController.prepare(prepared);
  if(current()&&language===targetLanguage&&translationOriginal===targetOriginal)renderReadme(view,prepared);
}
function renderReadme(view,prepared=null) {
  const source = view.document && safeGitHubUrl(view.document.source_url);
  // Older caches recorded the repository root rather than the README file.
  const base = source && /^https:\/\/github\.com\/[^/]+\/[^/#]+(?:#readme)?$/.test(source)
    ? source.replace(/#readme$/, '') + '/blob/HEAD/README.md' : source || undefined;
  const labels = {empty:'尚未读取 README。', fetching:'正在读取 README…', ready:'',
    stale:'显示上次保存的 README。', missing:'该项目没有可用的 README。', error:'README 读取未完成。'};
  byId('readme-status').textContent = [tr(labels[view.status] ?? 'README 读取未完成。'), localizeServerText(view.reason || '')].filter(Boolean).join(' ');
  byId('readme-refresh').disabled = view.status === 'fetching' || quitting;
  byId('readme-refresh').title = view.document ? `${tr('本机保存于')} ${view.document.observed_at.replace('T',' ')}` : '';
  const key=readmeContentKey(view);
  if(key!==readmeRenderedKey){
  readmeRenderedKey=key;
  if(prepared)translationController.adopt(prepared,byId('readme-purpose'));else fillReadmePurpose(byId('readme-purpose'),view,base);
  }
  if(view.status==='ready'&&view.document?.truncated)byId('readme-status').textContent=tr('README 超出读取上限，仅展示已读取部分。');
  translatePage();
}

async function loadReadme(repoId, refresh = false, startIfEmpty = false, waitUntilReady = false) {
  const generation = ++readmeGeneration;
  if (readmePollTimer) clearTimeout(readmePollTimer);
  let view = null;
  const current = () => !quitting && generation === readmeGeneration && location.pathname === `/project/${repoId}`;
  try {
    view = await api(`/api/readme/${encodeURIComponent(repoId)}`);
    if (!current()) return;
    if(!waitUntilReady)await renderPreparedReadme(view,current);
    if (refresh || (startIfEmpty && view.status === 'empty')) {
      view = await post(`/api/readme/${encodeURIComponent(repoId)}`, {refresh});
      if (!current()) return;
      if(!waitUntilReady)await renderPreparedReadme(view,current);
    }
    if(waitUntilReady){
      const deadline=Date.now()+22000;
      while(view.status==='fetching' && current() && Date.now()<deadline){
        await new Promise(resolve=>setTimeout(resolve,200));
        if(!current())return;
        view=await api(`/api/readme/${encodeURIComponent(repoId)}`);
      }
      if(!current())return;
      renderReadme(view);
    }
    if (view.status === 'fetching') readmePollTimer = setTimeout(() => loadReadme(repoId), 700);
    else if(view.document && current() && !waitUntilReady)refreshDetailAI(repoId);
    return view;
  } catch (error) {
    if (current()) renderReadme({...view, status:view?.document ? 'stale':'error', reason:error.message});
  }
}

async function showDetail(repoId,{preserveFolders=false}={}) {
  const routeKey=location.pathname+location.search;
  const preparation=++detailPreparationSequence;
  const current=()=>preparation===detailPreparationSequence && location.pathname+location.search===routeKey;
  detailPreparationKey=routeKey;
  document.documentElement.dataset.translationPreparing='detail';
  byId('translation-status').textContent=tr('正在准备译文…');
  cancelReadmePolling();
  byId('detail-tools').hidden=false;
  renderReadme({status:'empty', sections:[], selected_markdown:'', document:null});
  detailRepoId = repoId;
  if(!preserveFolders){folderGeneration++;folderSelection.unfollow();byId('detail-folders').hidden=true;}
  loadAIStatus();
  if (detailPollTimer) clearTimeout(detailPollTimer);
  for (const id of ["home-view", "history-view", "following-view", "settings-view"])
    byId(id).hidden = true;
  byId("detail-view").hidden = false;
  byId("back-link").href = detailReturnPath;
  byId("back-link").textContent = language === "en"
    ? (detailReturnPath.startsWith('/history') ? "← Back to History" : detailReturnPath.startsWith('/following') ? "← Back to Following" : "← Back to Home")
    : (detailReturnPath.startsWith('/history') ? "← 返回历史" : detailReturnPath.startsWith('/following') ? "← 返回我的关注" : "← 返回首页");
  byId("detail-title").textContent = tr("正在读取项目…");
  byId("detail-description").textContent = "";
  clear(byId("detail-metrics"));
  clear(byId("detail-tags"));
  clearAIExplanation();
  byId('detail-rank').hidden=true;
  byId("github-link").hidden = true;
  window.scrollTo(0, 0);
  try {
    const detail = await api(`/api/project/${encodeURIComponent(repoId)}${location.search}`);
    if (location.pathname !== `/project/${repoId}` || !current()) return;
    document.title = `${detail.title} · StarTrail`;
    byId("detail-source").textContent = localizeServerText(detail.source);
    byId("detail-title").textContent = detail.title;
    const displayRank = detail.display_rank || detail.growth_rank;
    byId("detail-headline").className = `detail-headline rank-${displayRank && displayRank <= 3 ? displayRank : 'other'}`;
    byId('detail-rank').hidden=!displayRank;byId('detail-rank').textContent=displayRank || '';
    byId("detail-description").textContent = detail.description;
    byId("follow-button").setAttribute("aria-pressed", String(Boolean(detail.followed)));
    byId("follow-button").textContent = tr(detail.followed ? "已关注" : "关注");
    if(!preserveFolders||!detail.followed)await loadDetailFolders(repoId,Boolean(detail.followed));
    if(location.pathname!==`/project/${repoId}` || detailRepoId!==repoId || !current())return;
    appendMetric(detail.stars);
    appendMetric(localizeServerText(detail.growth), true);
    if (!(detail.observation || '').startsWith('GitHub 统计日：'))
      appendMetric(localizeServerText(detail.observation));
    if (detail.history_date) appendMetric(language === "en" ? `Saved date: ${detail.history_date}` : `保存日期：${detail.history_date}`);
    if (detail.saved_at) appendMetric(`${tr("本机保存于")} ${detail.saved_at.replace("T", " ")}`);
    appendMetric(detail.language ? (language === "en" ? `Primary language: ${detail.language}` : `主要语言：${detail.language}`) : "");
    for (const tag of detail.tags || []) byId("detail-tags").append(element("span", "tag", tag));
    if (detail.matched_keywords && detail.matched_keywords.length)
      byId("detail-tags").append(element("span", "detail-keyword-match",
        `${tr("基础匹配")}${language === "en" ? ": " : "："}${detail.matched_keywords.join(language === "en" ? ", " : "、")}`));
    renderAIExplanation(detail);
    const safeUrl = safeGitHubUrl(detail.url);
    if (safeUrl) {
      byId("github-link").href = safeUrl;
      byId("github-link").hidden = false;
    }
    await loadReadme(repoId, false, true, true);
    if(!current())return;
    await translationController.refresh(byId('detail-view'));
  } catch (error) {
    if(!current())return;
    byId("detail-title").textContent = tr("暂时无法读取项目");
    byId("detail-description").textContent = localizeServerText(error.message);
  } finally {if(current()){detailPreparationKey=null;delete document.documentElement.dataset.translationPreparing;}}
}

function showHome() {
  byId("detail-tools").hidden=true;
  cancelReadmePolling();
  detailRepoId = null;
  loadAIStatus();
  if (detailPollTimer) clearTimeout(detailPollTimer);
  document.title = `StarTrail · ${tr("发现值得关注的开源项目")}`;
  byId("detail-view").hidden = true;
  byId("home-view").hidden = false;
  byId("history-view").hidden = true;
  byId("following-view").hidden = true;
  byId("settings-view").hidden = true;
  window.scrollTo(0, pageScroll.get("/") || homeScroll);
  const loading=!latestIssue?loadIssue():undefined;
  translatePage();
  return loading;
}

function showSecondary(path) {
  byId("detail-tools").hidden=true;
  cancelReadmePolling();
  detailRepoId = null;
  byId("home-view").hidden = true;
  byId("detail-view").hidden = true;
  for (const [name, id] of [["/history", "history-view"],
                            ["/following", "following-view"],
                            ["/settings", "settings-view"]]) byId(id).hidden = path !== name;
  document.title = `${tr(path === "/history" ? "历史" : path === "/following" ? "我的关注" : "设置")} · StarTrail`;
  const scrollKey=path+location.search;
  window.scrollTo(0, pageScroll.get(scrollKey) || 0);
  if (path === "/history" && (!historyLoaded||historyLoadedQuery!==location.search))
    return loadHistory().then(() => {if(location.pathname+location.search===scrollKey)window.scrollTo(0, pageScroll.get(scrollKey) || 0);});
  if (path === "/following") return loadFollowing().then(()=>{if(location.pathname+location.search===scrollKey)window.scrollTo(0,pageScroll.get(scrollKey)||0);});
  if (path === "/settings") return loadSettings();
}

let navigationOpen = false;
const narrowNavigation = window.matchMedia ? window.matchMedia("(max-width: 900px)") : {matches: false};
function setNavigationOpen(open, restoreFocus = true) {
  const wasOpen = navigationOpen;
  const narrow = narrowNavigation.matches || (Number.isFinite(window.innerWidth) && window.innerWidth < 900);
  document.documentElement.dataset.navigation = narrow ? 'narrow' : 'wide';
  navigationOpen = Boolean(open && narrow);
  byId("sidebar").hidden = narrow && !navigationOpen;
  byId("nav-scrim").hidden = !navigationOpen;
  byId("nav-toggle").setAttribute("aria-expanded", String(navigationOpen));
  if (navigationOpen && !wasOpen) byId("nav-home").focus();
  else if (wasOpen && restoreFocus) byId("nav-toggle").focus();
}

async function route({transition=false}={}) {
  const routeKey=location.pathname+location.search;
  detailPreparationKey=null;
  delete document.documentElement.dataset.translationPreparing;
  if(!transition)cardTransition.cancel();
  historySearch.invalidate();historyCalendar.invalidate();historyDayGeneration++;followingBoard.invalidate();folderGeneration++;
  translationController.invalidate();
  setNavigationOpen(false);
  for (const [path, id] of [["/", "nav-home"], ["/history", "nav-history"],
                            ["/following", "nav-following"], ["/settings", "nav-settings"]])
    byId(id).setAttribute("aria-current", location.pathname === path ? "page" : "false");
  const match = /^\/project\/(\d+)$/.exec(location.pathname);
  if (match) {
    if (history.state && history.state.returnPath) detailReturnPath = history.state.returnPath;
    await showDetail(match[1]);
  }
  else if (["/history", "/following", "/settings"].includes(location.pathname))
    await showSecondary(location.pathname);
  else await showHome();
  if(location.pathname+location.search===routeKey)await translationController.refresh(currentView());
}

function homeLink(event) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const destination=event.currentTarget&&event.currentTarget.id==='brand-link'?'/':detailReturnPath;
  if(event.currentTarget?.id==='back-link'&&cardReturnEntry?.path===destination){
    let restored=false;
    return cardTransition.run({from:detailParts(),reverse:true,to:()=>{
      if(!restored){window.scrollTo(0,pageScroll.get(destination)||0);restoreCardScroll();restored=true;}
      const node=returningCard();return {...cardParts(node),focus:node||projectListView()};},
      update:()=>{history.replaceState({},'',destination);return route({transition:true});}});
  }
  return transitionPage(()=>{history.replaceState({},'',destination);return route({transition:true});},{reverse:event.currentTarget?.id==='back-link'});
}

function navLink(event) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const destination = event.currentTarget.pathname;
  pageScroll.set(location.pathname+location.search, window.scrollY);
  return transitionPage(()=>{history.replaceState({}, "", destination);return route({transition:true});},
    {reverse:event.currentTarget.id==='history-calendar-back'});
}

async function quitApp() {
  if (quitting || !window.confirm(tr("退出 StarTrail？正在运行的 AI 分析会停止，数据更新会先完成。"))) return;
  quitting = true;
  translationController.close();
  cancelReadmePolling();
  byId("quit-button").disabled = true;
  setStatus(tr("正在结束更新…"), "busy");
  if (pollTimer) clearTimeout(pollTimer);
  if (savedIssueTimer) clearTimeout(savedIssueTimer);
  if (aiPollTimer) clearTimeout(aiPollTimer);
  if (detailPollTimer) clearTimeout(detailPollTimer);
  try {
    await post("/api/quit");
    while (true) {
      await new Promise((resolve) => setTimeout(resolve, 350));
      try { await api("/api/health"); }
      catch { break; }
    }
    setStatus(tr("已退出 StarTrail，可以关闭这个标签页。"), "");
    byId("refresh-button").disabled = true;
    byId("keyword-input").disabled = true;
    byId("keyword-form").querySelector("button").disabled = true;
    try { window.close(); }
    catch { /* The browser may refuse to close a tab it did not create. */ }
  } catch (error) {
    quitting = false;
    byId("quit-button").disabled = false;
    setStatus(error.message, "error");
  }
}

byId("language-toggle").addEventListener("click", () => setLanguage(language === "zh" ? "en" : "zh"));
byId('translation-original').addEventListener('click',()=>{
  translationOriginal=!translationOriginal;translationController.setOriginal(translationOriginal);translatePage();
});
byId("nav-toggle").addEventListener("click", () => setNavigationOpen(!navigationOpen));
byId("nav-scrim").addEventListener("click", () => setNavigationOpen(false));
byId("skip-content").addEventListener("click", event => {
  event.preventDefault(); setNavigationOpen(false, false);
  for (const id of ["home-view", "history-view", "following-view", "settings-view", "detail-view"])
    if (!byId(id).hidden) { byId(id).focus(); break; }
});
window.addEventListener("keydown", event => {
  if (!navigationOpen) return;
  if (event.key === "Escape") { event.preventDefault(); setNavigationOpen(false); }
  if (event.key === "Tab") {
    const controls = ["brand-link","nav-home","nav-history","nav-following","nav-settings","language-toggle","quit-button"].map(byId);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
if (narrowNavigation.addEventListener) narrowNavigation.addEventListener("change", () => setNavigationOpen(false));
window.addEventListener('resize', () => {cardTransition.cancel({complete:true});setNavigationOpen(false);});
setNavigationOpen(false, false);
byId("auto-update-form").addEventListener("submit", saveAutoUpdate);
byId("github-connect").addEventListener("click", connectGitHub);
byId("github-disconnect").addEventListener("click", async () => {
  try { await post("/api/github/disconnect"); await loadGitHubAccount(); }
  catch (error) { byId("github-status").textContent = localizeServerText(error.message); }
});
byId("github-cancel").addEventListener("click", async () => {
  try { await post("/api/github/cancel"); byId("github-authorization").hidden = true; await loadGitHubAccount(); }
  catch (error) { byId("github-status").textContent = localizeServerText(error.message); }
});
byId("github-copy-code").addEventListener("click", async () => {
  const input = byId("github-user-code");
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard) await navigator.clipboard.writeText(input.value);
    else { input.select(); document.execCommand("copy"); }
    byId("github-status").textContent = tr("验证码已复制。");
  } catch { input.focus(); input.select(); byId("github-status").textContent = tr("请复制选中的验证码。"); }
});
byId('readme-refresh').addEventListener('click', () => detailRepoId && loadReadme(detailRepoId, true));
byId('translation-retry').addEventListener('click',()=>translationController.retry());
byId("auto-update-time").addEventListener("blur", validateAutoUpdateTime);
byId("font-scale").addEventListener("input", () => previewFontScale(byId("font-scale").value));
byId("font-scale").addEventListener("change", () => commitFontScale(byId("font-scale").value));
byId("font-scale").addEventListener("keydown", event => {
  const increments = {ArrowRight: .01, ArrowUp: .01, ArrowLeft: -.01, ArrowDown: -.01, PageUp: .1, PageDown: -.1};
  if (!(event.key in increments) && !["Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const next = event.key === "Home" ? .8 : event.key === "End" ? 2 : fontScale + increments[event.key];
  return commitFontScale(Math.max(.8, Math.min(2, Number(next.toFixed(4)))));
});
byId("font-reset").addEventListener("click", () => commitFontScale(1));
byId('motion-preference').addEventListener('change', changeMotionPreference);
if (systemMotion.addEventListener) systemMotion.addEventListener('change', applyMotionPreference);
byId("refresh-button").addEventListener("click", startRefresh);
byId("keyword-form").addEventListener("submit", addKeyword);
byId("quit-button").addEventListener("click", quitApp);
byId("back-link").addEventListener("click", homeLink);
byId("brand-link").addEventListener("click", homeLink);
byId("ai-connect").addEventListener("click", connectAI);
byId('settings-codex-connect').addEventListener('click',connectAI);
byId('settings-codex-check').addEventListener('click',checkAIConnection);
byId('settings-codex-auto').addEventListener('click',()=>changeModel({target:{value:''}}));
byId("detail-ai-connect").addEventListener("click", connectAI);
byId("ai-model").addEventListener("change", changeModel);
byId("detail-ai-model").addEventListener("change", changeModel);
byId("detail-ai-explain").addEventListener("click", explainProject);
byId("follow-button").addEventListener("click", toggleFollow);
byId('history-search-form').addEventListener('submit',submitHistory);
byId('history-clear').addEventListener('click',clearHistory);
byId('history-calendar-back').addEventListener('click',navLink);
byId('history-more').addEventListener('click',async()=>{const button=byId('history-more');button.disabled=true;
  try{await historySearch.more();}catch(error){byId('history-status').textContent=error.message;}finally{button.disabled=false;}});
byId('detail-folder-form').addEventListener('submit',saveDetailFolders);
byId('detail-folder-create-form').addEventListener('submit',createDetailFolder);
for (const id of ["nav-home", "nav-history", "nav-following", "nav-settings"])
  byId(id).addEventListener("click", navLink);
window.addEventListener("popstate", ()=>transitionPage(()=>route({transition:true}),{reverse:true}));
// Release the native snapshot before the next activation. In Chromium a click
// during snapshot preparation can otherwise be consumed by the transition.
const interruptMotion=event=>{
  if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;
  if(event.target.closest?.('button, summary, a, input, select')&&
     (document.documentElement.dataset.cardTransition||document.documentElement.dataset.pageTransition))
    cardTransition.cancel({complete:true});
};
document.addEventListener('pointerdown',interruptMotion,true);document.addEventListener('keydown',interruptMotion,true);
// Only content changed after a user interaction receives in-place feedback.
// This observes results; it never repeats clicks, submissions or API requests.
function hasFeedbackContentChange(record){
  if(record.type==='characterData')return true;
  return [...(record.addedNodes||[]),...(record.removedNodes||[])].some(node=>
    !node.classList?.contains('radar-morph-surface')&&Boolean(node.textContent?.trim()));
}
if(typeof MutationObserver!=='undefined'&&document.body){
  let feedbackUntil=0;
  const arm=event=>{if(event.target.closest?.('button, summary, a, input, select, form'))feedbackUntil=Date.now()+60000;};
  document.addEventListener('click',arm,true);document.addEventListener('submit',arm,true);document.addEventListener('change',arm,true);
  const feedbackSelector='.form-feedback, .field-error, .ai-live, .empty-state, [role="status"], #translation-status, #following-status, #history-status, #detail-description, .readme-section-body, #growth-cards, #growth-returning-cards, #keyword-groups, #following-cards, #history-date-groups, #settings-keywords';
  new MutationObserver(records=>{
    if(Date.now()>feedbackUntil||!motionEnabled()||document.documentElement.dataset.cardTransition||document.documentElement.dataset.pageTransition)return;
    const changed=new Set();
    for(const record of records){if(!hasFeedbackContentChange(record))continue;const target=record.target.nodeType===3?record.target.parentElement:record.target;
      const node=target?.closest?.(feedbackSelector);if(node&&node.textContent.trim()&&!node.closest('[hidden]'))changed.add(node);}
    for(const node of changed)if(![...changed].some(parent=>parent!==node&&parent.contains(node)))cardTransition.reveal(node);
  }).observe(document.body,{childList:true,characterData:true,subtree:true});
}
// Pointer light is decorative; clicking and keyboard focus work without it.
if (document.addEventListener) {
  const pointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  let frame = null, lightTarget = null, point = null;
  document.addEventListener('pointermove', event => {
    if (!motionEnabled() || !pointer.matches) return;
    const folderControl=event.target.closest('.folder-select, .folder-menu-button');
    const target = folderControl?event.target.closest('.folder-tab'):event.target.closest('.project-card, .button, .history-date-toggle');
    if (!target || target.disabled) { resetPointerLight(); return; }
    if (lightTarget && lightTarget !== target) {
      lightTarget.style.removeProperty('--mx'); lightTarget.style.removeProperty('--my');
    }
    lightTarget = target;
    const box = target.getBoundingClientRect();
    point = [100 * (event.clientX - box.left) / box.width, 100 * (event.clientY - box.top) / box.height];
    if (frame === null) frame = requestAnimationFrame(() => {
      frame = null;
      if (lightTarget && motionEnabled()) {
        lightTarget.style.setProperty('--mx', point[0] + '%'); lightTarget.style.setProperty('--my', point[1] + '%');
      }
    });
  });
  resetPointerLight = () => {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    if (lightTarget) { lightTarget.style.removeProperty('--mx'); lightTarget.style.removeProperty('--my'); }
    lightTarget = null;
  };
  document.addEventListener('pointerout', event => { if (lightTarget && !lightTarget.contains(event.relatedTarget)) resetPointerLight(); });
  systemMotion.addEventListener('change', resetPointerLight);
  byId('motion-preference').addEventListener('blur', resetPointerLight);
}
if (!token) setStatus(tr("页面缺少本次运行凭证。请重新打开 StarTrail。"), "error");
else loadPreferences().then(() => {
  route();
  loadIssue();
  savedIssueTimer = setTimeout(pollSavedIssue, 60000);
});

/* Forest select widgets */
window.RadarThemeSelect=(()=>{
  const widgets=new Map();let active=null;
  function close(restoreFocus=false){if(!active)return;const old=active;active=null;old.popup.remove();old.widget.button.setAttribute('aria-expanded','false');if(restoreFocus)old.widget.button.focus();}
  function enhance(select){if(widgets.has(select))return widgets.get(select);
    const wrapper=document.createElement('span'),button=document.createElement('button'),caption=document.createElement('span'),labels=Array.from(select.labels||[]);
    wrapper.className='theme-select';button.className='theme-select-control';button.type='button';button.id=select.id+'-control';
    caption.className='theme-select-label';button.append(caption);
    button.setAttribute('role','combobox');button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');
    const described=select.getAttribute('aria-describedby');if(described)button.setAttribute('aria-describedby',described);
    wrapper.hidden=select.hidden;select.parentNode.insertBefore(wrapper,select);wrapper.append(select,button);select.hidden=true;select.setAttribute('aria-hidden','true');select.tabIndex=-1;
    for(const label of labels)label.setAttribute?.('for',button.id);
    const widget={select,button,caption,wrapper,sync(){const option=Array.from(select.options).find(o=>o.value===select.value),text=option?.textContent||'';
      if(caption.textContent!==text)caption.textContent=text;button.title=text;
      button.disabled=select.disabled;button.setAttribute('aria-label',select.getAttribute('aria-label')||labels.map(l=>l.textContent).join(' ')||select.id);
      if(active?.widget===widget&&select.disabled)close();}};widgets.set(select,widget);
    function open(){if(select.disabled)return;if(active?.widget===widget){close();return;}close();widget.sync();
      const popup=document.createElement('div');popup.className='theme-select-menu';popup.id=button.id+'-list';popup.setAttribute('role','listbox');popup.setAttribute('aria-label',button.getAttribute('aria-label'));button.setAttribute('aria-controls',popup.id);
      const options=Array.from(select.options),choices=[];
      const choose=index=>{const option=options[index];if(!option||option.disabled||select.disabled)return;
        select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));widget.sync();close(true);};
      for(const [index,option] of options.entries()){const choice=document.createElement('button');choice.type='button';choice.className='theme-select-option';choice.textContent=option.textContent;choice.disabled=option.disabled;choice.tabIndex=-1;
        choice.setAttribute('role','option');choice.setAttribute('aria-selected',String(option.value===select.value));choice.addEventListener('click',()=>choose(index));
        choice.addEventListener('keydown',event=>{let next=index;
          if(event.key==='Escape'){event.preventDefault();close(true);return;}
          if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(index);return;}
          if(event.key==='Tab'){close();return;}
          if(event.key==='Home')next=0;else if(event.key==='End')next=choices.length-1;
          else if(event.key==='ArrowDown')next=Math.min(index+1,choices.length-1);else if(event.key==='ArrowUp')next=Math.max(index-1,0);else return;
          event.preventDefault();const direction=next>=index?1:-1;while(choices[next]?.disabled&&next>=0&&next<choices.length)next+=direction;choices[next]?.focus();});
        choices.push(choice);popup.append(choice);}
      const rect=button.getBoundingClientRect(),space=Math.max(100,window.innerHeight-16),width=Math.min(Math.max(rect.width,200),window.innerWidth-16);
      popup.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';popup.style.width=width+'px';popup.style.maxHeight=Math.min(320,space)+'px';
      if(window.innerHeight-rect.bottom>=Math.min(200,space)){popup.style.top=rect.bottom+4+'px';popup.style.maxHeight=Math.min(320,window.innerHeight-rect.bottom-12)+'px';}
      else {popup.style.bottom=Math.max(8,window.innerHeight-rect.top+4)+'px';popup.style.maxHeight=Math.max(80,Math.min(320,rect.top-12))+'px';}
      const modal=select.closest?.('dialog[open]');
      if(modal){const host=modal.getBoundingClientRect();popup.style.position='absolute';popup.style.left=(Math.max(8,Math.min(rect.left,window.innerWidth-width-8))-host.left-(modal.clientLeft||0))+'px';popup.style.bottom='auto';
        popup.style.top=(rect.bottom-host.top-(modal.clientTop||0)+4)+'px';
        if(window.innerHeight-rect.bottom<Math.min(200,space)){popup.style.top=(rect.top-host.top-(modal.clientTop||0)-4)+'px';popup.style.transform='translateY(-100%)';}}
      popup.style.font=window.getComputedStyle(button).font;(modal||document.body).append(popup);active={widget,popup,signature:JSON.stringify(options.map(o=>[o.value,o.textContent,o.disabled]))};button.setAttribute('aria-expanded','true');
      cardTransition.reveal(popup);(choices.find(c=>c.getAttribute('aria-selected')==='true'&&!c.disabled)||choices.find(c=>!c.disabled))?.focus({preventScroll:true});}
    button.addEventListener('click',open);button.addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();open();}else if(e.key==='Escape')close(true);});
    select.addEventListener('change',widget.sync);widget.sync();
    if(typeof MutationObserver==='function')new MutationObserver(()=>{if(active?.widget===widget&&active.signature!==JSON.stringify(Array.from(select.options).map(o=>[o.value,o.textContent,o.disabled])))close();widget.sync();}).observe(select,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['disabled','aria-label']});
    return widget;
  }
  function refresh(){for(const w of widgets.values())w.sync();}
  function discover(){for(const select of document.querySelectorAll('select'))enhance(select).sync();}
  document.addEventListener('pointerdown',e=>{if(active&&!active.popup.contains(e.target)&&!active.widget.button.contains(e.target))close();});
  window.addEventListener('resize',()=>close());window.addEventListener('scroll',e=>{if(active&&!active.popup.contains(e.target))close();},true);
  discover();if(typeof MutationObserver==='function')new MutationObserver(discover).observe(document.body,{subtree:true,childList:true});
  function setHidden(select,hidden){const w=enhance(select);w.wrapper.hidden=hidden;select.hidden=true;if(hidden&&active?.widget===w)close();}
  function focus(select){enhance(select).button.focus();}
  return {enhance,refresh,close,setHidden,focus};
})();
