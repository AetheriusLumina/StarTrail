# 多来源 AI 检索、快照与关注归类 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动融合程序采集与AI主动搜索，修复排名/当天快照，完成关注拖动、详情归类和类型定性，并交付对应的Windows预览包。

**Architecture:** 新检索职责分成值类型、持久化、Codex执行、来源、排名和任务协调，保留标准库后端与原生网页。网络/AI在写锁外运行，短事务发布有真实观察时间的推荐快照；浏览器、CLI与Windows任务复用同一协调入口。关注归类独立事务与前端模块，可单独测试；类型定性扩展现有解读结构，不新开AI调用。

**Tech Stack:** Windows 10/11 x64；Python 3.13；SQLite；原生HTML/CSS/JavaScript；Node.js 22+交互测试；现有Codex CLI、CPU本地翻译、PyInstaller与Inno Setup 6。

**Spec:** [已审阅设计及类型定性补充](../specs/2026-10-04-keyword-discovery-design.md)。此计划为待执行记录，勾选只代表有实际验证证据的完成步骤。后续新增的GitHub发行版检测/软件升级属于独立子系统，正在讨论，尚不由本计划冒充覆盖；确定设计后接续单独升级计划，再统一完成最终交付。

## Global Constraints

1. 每个关键词或增长任务单次最多新增核实200个候选，单批最多20个；扩词/初始搜索/补充搜索分别计数。每模块最多一次初始AI搜索和一次有明确缺口的补充搜索；有效缓存不计新增核实。
2. 每个模块单次任务总时限1800秒，采集阶段最多600秒，Codex单调用最多120秒且不得超过任务剩余时间。GitHub单查询最多1000条，通过日期/Star分区扩展；不把1000当持久候选库总上限。
3. 每天检查来源和真实元数据，不设固定20个日常截止；同一天重复更新不轮换下一组项目、不重跑已有成功搜索/有效判断。不能承诺全站零遗漏。
4. 增长只按最近已结束完整UTC日的官方正日增排序，平手按累计Star降序及repo_id；历史前五紧凑展示不占最多五个新增名额。关键词按真实累计Star降序及repo_id，历史排重与其他组占位保留。
5. 自动搜索/核实可见、可关闭；无Codex/不足额度如实部分完成，不自动登录、无限重试或自动生成所有候选的项目解读。
6. 网络/AI不持跨进程写锁；代次、取消、日上下文和关键词版本都在发布前验证。手动更新优先，重复请求复用在途任务；进度不动画整页。
7. 保存推荐与真实当天快照原子完成，不能只改旧时间伪造新鲜数据；历史详情不被后来元数据覆盖。
8. 全部关注卡片不可拖，未分类/自建文件夹卡片可拖；删除卡片底部“移动到”。详情归类支持多选/创建并归类，成功时关注，取消不变更。
9. 类型定性必须作为“仓库原用途”卡片首行第一句话高亮，六卡三列两行不变；与项目解读同一次AI调用，资料不足明确待确认，旧缓存不自动付费补做。
10. 保留默认浏览器、森林透明主题、艺术字体、固定侧栏、主题滚动条、最多350ms真实DOM渐显、已有预译缓存和其他功能。兼容内部包名、安装AppId、可执行文件名、Windows任务身份和UserData。
11. 每个行为任务先失败回归再实现；公共日志/代码/测试只用脱敏或虚构事实。每次交付更新统一CHANGELOG、AI_HANDOFF、用户手册和双语README，私人材料不进GitHub。
12. 在指定活动Repo执行，复用忽略的开发/构建目录；不另复制一套活动源码。执行前按worktree技能核对已有隔离环境；若没有则记录此处原位执行的既有目录偏好及保护方式，不擅自生成零散工作目录。

## Review Focus

1. 北京时间07:59→08:00跨UTC日、时钟跳变及长任务跨本机午夜：不能发布错误统计日或旧代次结果（Tasks 5、6）。
2. 仓库重命名/大小写别名、Star下降与旧观察时间：真实ID合并，旧缓存不覆盖新事实，不把未确认身份上榜（Tasks 3、5）。
3. 进程崩溃或磁盘不足发生在批次保存/发布/创建并归类之间：最后好结果、原关注和分类完整保留，重启可继续（Tasks 1、5、9）。
4. 返回/切换项目后迟到响应，以及拖动释放产生点击：不污染新详情、不误打开、不重新翻译静态界面（Tasks 7、10、11）。
5. 中文关键词/超长或恶意README/主题名称含脚本：仍按原意检索，不能注入查询结构、任意URL、HTML或命令（Tasks 2、3、4、11）。

## 文件与接口分工

| 文件 | 职责 |
|---|---|
| 新建 `github_radar/search_types.py` | 下述检索值类型、状态与规则版本，不依赖网络/SQLite |
| 新建 `github_radar/search_storage.py` | 新表迁移与检索缓存/队列/代次/发布事务；通过RadarStore连接调用 |
| 新建 `github_radar/codex_runner.py` | 受约束Codex子进程、JSONL搜索事件、超时/取消；已有解读共用执行器 |
| 新建 `github_radar/search_provider.py` | 扩词、主动网页搜索、增长证据判断；关键词判断复用现有judge_batch |
| 新建 `github_radar/search_sources.py` | 分区分页、公开主题映射、真实ID合并与来源覆盖 |
| 新建 `github_radar/search_ranking.py` | 已判断候选前沿、关键词/增长选择与发布数据组装 |
| 新建 `github_radar/search_coordinator.py` | 分阶段检索、证据、判断、进度、缓存、预算与发布 |
| 新建 `github_radar/search_jobs.py` | 本机后台队列、跨进程短租约、代次和手动优先 |
| 新建 `github_radar/web_assets/search_controls.js` | 搜索状态、扩词查看/纠正、继续与设置开关 |
| 新建 `github_radar/web_assets/detail_classification.js` | 所有详情入口的主题归类弹窗与状态隔离 |
| 修改 `storage.py`、`service.py`、`browser_server.py`、`daily_update.py`、`__main__.py` | 薄接入层与旧快照修复，避免将整个新流程塞入已有大文件 |
| 修改 `follow_folders.py`、`following_board.js` | 原子移动/归类、拖动范围与来源语义 |
| 修改 `ai_types.py`、`ai_provider.py`、`ai_service.py`、`interpretation.py`、`presentation.py` | schema v3类型定性、旧缓存兼容与展示数据 |
| 修改 `web_assets/index.html`、`app.js`、`app.css`、`i18n.js` | 入口/主题样式/静态翻译，复用原布局和动效 |
| 新增/修改 `tests/test_search_*.py` 与对应交互 `.cjs` | 网络/Codex替身、隔离数据库、并发和浏览器行为回归 |

检索新值类型均为有类型dataclass：
- `SearchScope(section: str, keyword_id: int|None, local_date: str, stat_date: str|None, term: str, min_stars: int, model_id: str|None, rules_version: str)`；section仅growth/keyword，增长keyword_id为空；版本常量`SEARCH_RULES_VERSION="1"`。
- `QueryExpansion(original: str, terms: tuple[str,...], topics: tuple[str,...], version: str)`；保留original，额外terms最多6个，topics必须来自实际公开目录。
- `AISearchResult(candidates: tuple[DiscoveryCandidate,...], coverage: tuple[str,...], notes: tuple[str,...], web_search_calls: int)`；数值不能成为Star事实。
- `ObservedRepository(repo: Repository, observed_at: str)`；来自实际获取，缓存读取不得重置observed_at。
- `PreparedCandidate(observation: ObservedRepository, source: AIRepositoryInput, evidence: tuple[SourceEvidence,...], semantic_fingerprint: str, evidence_fingerprint: str, official_day: StarDay|None)`；语义指纹排除Star、日期，证据指纹含目标日/真实统计与来源证据。
- `GrowthAssessment(repo_id: int, status: str, reason: str)`；status仅supported/conflict/uncertain，不能返回替代日增。
- `SearchProgress(job_id: str, section: str, keyword_id: int|None, local_date: str, status: str, stage: str, collected: int, unique: int, cache_hits: int, newly_checked: int, pending: int, expansion_calls: int, search_calls: int, judgment_calls: int, limited: bool, notes: tuple[str,...])`；status queued/running/done/partial/paused/error/cancelled。
- `Publication(scope: SearchScope, generation: int, observations: tuple[ObservedRepository,...], snapshots: tuple[StarSnapshot,...], recommendations: tuple[Recommendation,...], coverage: GrowthCoverage|None)`；所有选中项目必须有可追溯真实观察。

## Task 1: 持久化候选、缓存与实际展示历史

**Files:** Create `search_types.py`、`search_storage.py`、`tests/test_search_storage.py`；Modify `storage.py`初始化、`seen_repo_ids`/`seen_repo_ids_before`。
**Interfaces:** `initialize_search_schema(db: sqlite3.Connection)->None`；`SearchStore(store: RadarStore)`提供`load_expansion(scope)->QueryExpansion|None`、`save_expansion(scope,value)`、`save_candidates(scope,observations,evidence)`、`save_unresolved(scope,candidates:tuple[DiscoveryCandidate,...])->None`、`candidate_page(scope,after_id:int|None,limit:int=100)->tuple[ObservedRepository,...]`、`load_judgment(scope,repo_id,fingerprint,kind:str)->RelevanceVerdict|GrowthAssessment|None`、`save_judgments(scope,prepared,verdicts,assessments,checked_at)`、`save_progress(progress)->None`、`progress(job_id)->SearchProgress|None`。kind为semantic/evidence。

- [ ] 写失败测试`test_migration_preserves_follows_and_history`、`test_candidate_not_displayed_can_later_be_selected`、`test_replaced_displayed_repo_stays_seen`、`test_partial_batch_restart_reuses_verdicts`：迁移运行两次不清数据；仅核实不进入已见集合，曾展示后替换仍进入；批次写入故障回滚。
- [ ] 运行 `python -m unittest tests.test_search_storage -v`，确认新行为失败，记录失败原因。
- [ ] 增加`query_expansions`、`search_candidates`、`search_judgments`、`search_runs`、`search_cursors`、`displayed_repositories`表，复合键包括模块、关键词、版本/模型。使用已有source_evidence存真实出处；迁移recommendations/ai_removed为实际展示历史，旧无指纹判断需补核。按页读写，不全库载入RAM。
- [ ] 重跑上述命令及 `python -m unittest tests.test_storage tests.test_discovery_storage -v`，必须全通过。
- [ ] 只暂存对应文件及日志记录，提交 `feat: persist search candidates and display history`。

## Task 2: Codex实时搜索与结构化扩词

**Files:** Create `codex_runner.py`、`search_provider.py`、`tests/test_search_provider.py`；Modify `ai_provider.py`的`_run`/`cancel`。
**Interfaces:** `CodexRunner(connection).run(instruction:str,data:dict,schema:dict,model_id:str|None,*,web_search:bool=False,timeout:float=120,cancel_event:threading.Event|None=None,on_web_search:Callable[[str],None]|None=None)->dict`及`cancel()->None`。`SearchProvider(provider: CodexProvider)`提供`expand_keyword(term,model_id,topics:tuple[str,...])->QueryExpansion`、`search_repositories(scope,expansion,known_names:tuple[str,...],gap:str|None,*,timeout:float,cancel_event)->AISearchResult`、`assess_growth_batch(inputs:tuple[PreparedCandidate,...],scope)->tuple[GrowthAssessment,...]`。

- [ ] 写`test_search_enables_live_mode_and_requires_search_events`、`test_expansion_preserves_original_and_limits_six`、`test_wrong_host_and_invented_ids_are_rejected`、`test_readme_instructions_cannot_enable_commands`、`test_cancel_new_job_uses_new_runner`；伪子进程确认只读、ignore-user-config/rules、live仅搜索、无搜索事件不报成功、恶意字段/超长JSON拒绝。
- [ ] 运行 `python -m unittest tests.test_search_provider tests.test_ai_provider -v`，确认新增断言失败。
- [ ] 提取现有受限执行器，搜索模式读取`--json`事件，只保存搜索动作计数/必要ID。有界逐行解析，不无限积累stdout/stderr；验证本机实际CLI事件再锁定解析。失败/取消关闭子进程，下一任务用新runner实例；已有手动解读保持相同输出/超时边界。扩词/schema结果严格闭合、字段长度受限；网络结果不可信。
- [ ] 重跑命令全通过；不得为单元测试调用真实账号。
- [ ] 提交 `feat: add bounded Codex web discovery`。

## Task 3: 多来源分页、主题映射与真实ID合并

**Files:** Create `search_sources.py`、`tests/test_search_sources.py`；Modify `discovery.py`分区复用、`trendshift.py`公开主题查询。
**Interfaces:** `SearchSources(client:GitHubClient,store:RadarStore,trending,trendshift,clock).collect(scope,expansion,ai_result:AISearchResult|None,budget:RequestBudget,cancel_event,on_progress)->None`持久保存并更新游标；`resolve_candidate(candidate:DiscoveryCandidate,budget)->ObservedRepository|None`通过`get_repository`确认身份。

- [ ] 写`test_more_than_thousand_partitioned_not_pool_truncated`、`test_same_day_overflow_splits_star_range`、`test_reopened_sources_detect_new_projects`、`test_aliases_merge_by_real_id`、`test_topic_synonym_uses_real_directory`、`test_slow_unfinished_page_remains_partial`，fixture含中文/脚本主题名与403/限流/重定向。
- [ ] 运行 `python -m unittest tests.test_search_sources tests.test_discovery tests.test_trendshift -v`确认新行为失败。
- [ ] GitHub固定安全查询结构（词值转义），name/description/README范围一致；日期区间超过1000先拆日期，单日超过再拆互斥Star区间，无法再拆明确有限覆盖。先读公开榜单/主题再分页；仅跟随验证过的公开分页，不猜隐藏API。真实ID合并并保留出处，旧观察不得覆盖新值。600秒预算按实际clock检查。
- [ ] 重跑命令通过；来源单项失败不清其余候选，所有不完整范围/游标保存。
- [ ] 提交 `feat: merge budgeted public and AI search sources`。

## Task 4: 证据准备与跨日语义复用

**Files:** Create `tests/test_search_cache.py`；Modify `search_coordinator.py`（创建证据部分）、`search_types.py`、`search_storage.py`、`readme_service.py`仅所需接入。
**Interfaces:** `prepare_candidate(observation:ObservedRepository,scope:SearchScope,readme_service,evidence:tuple[SourceEvidence,...],official_day:StarDay|None)->PreparedCandidate`；`semantic_fingerprint(scope,repo,readme_hash:str|None,extraction_version:str)->str`；`evidence_fingerprint(scope,evidence,official_day:StarDay|None)->str`。

- [ ] 写`test_star_only_change_reuses_semantic_verdict`、`test_content_or_model_change_requires_review`、`test_growth_stat_day_is_not_semantic_cache_key`、`test_missing_excerpt_is_uncertain_not_irrelevant`、`test_old_cache_time_stays_old`、`test_malicious_large_readme_is_bounded`；已采集未入选不预译、不自动生成解读。
- [ ] 运行 `python -m unittest tests.test_search_cache tests.test_readme_service -v`观察失败。
- [ ] 准备用途/功能/安装证据的有界片段，保留截断与内容哈希；语义指纹含原词/扩词版本、模型、规则、description/topics/language、正文语义内容，排除Star与日期。增长数字/日期证据单独失效；复用已有判断时仍获取真实当前元数据。
- [ ] 重跑命令通过；旧无指纹记录不能当作跨日有效缓存。
- [ ] 提交 `feat: reuse content-based search judgments`。

## Task 5: 统一重新排名、原子快照与详情日期修复

**Files:** Create `search_ranking.py`、`tests/test_search_ranking.py`、`tests/test_search_publication.py`；Modify `ranking.py`必要纯函数、`storage.py::commit_ai_batch`兼容路径、`browser_server.py::_project_payload`。
**Interfaces:** `select_keyword_candidates(scope,candidates:tuple[PreparedCandidate,...],verdicts:dict[int,RelevanceVerdict],seen_before:set[int],occupied:set[int],own_ids:set[int])->tuple[Recommendation,...]`；`select_growth_candidates(scope,candidates,daily:dict[int,StarDay],assessments:dict[int,GrowthAssessment],seen_before,occupied)->tuple[Recommendation,...]`复用`select_growth_slots`；`SearchStore.publish(publication:Publication)->bool`短事务核对代次/关键词/日期，过期返回False且不写入。

- [ ] 写`test_high_star_replaces_low_star_incumbent`（1396被62706相关未展示项目替换）、`test_same_day_repeat_does_not_rotate`、`test_growth_old_five_do_not_use_new_slots`、`test_utc_boundary_and_tie_order`、`test_new_ai_recommendation_detail_uses_today_snapshot`（10/2=1500，10/4=2500）、`test_history_detail_not_overwritten`、`test_publication_disk_failure_rolls_back`。
- [ ] 运行 `python -m unittest tests.test_search_ranking tests.test_search_publication tests.test_stat_date_correction -v`确认旧快照/占位缺陷失败。
- [ ] 关键词不再按剩余空位补卡，而在有效判断候选+当前本组重取前五；当天本组可保留，但以前日期实际展示与当日其他组排除。增长只用目标UTC日官方正日增，不接受AI数字替换。原子保存repositories/snapshots/recommendations/展示历史/来源上下文，选中项目缺真实观察不发布。旧`commit_ai_batch`补真实快照且不伪造旧缓存时间。发布事务再核对其他组占位并按增长优先/关键词ID顺序处理，不用采集前的过期occupied覆盖并发结果。详情明确当天context或历史date，不用任意最新snapshot覆盖。
- [ ] 重跑命令及 `python -m unittest tests.test_ranking tests.test_ai_service tests.test_browser_server -v`通过。
- [ ] 提交 `fix: publish ranked recommendations with matching daily snapshots`。

## Task 6: 分阶段协调、预算、取消与手动优先

**Files:** Create `search_jobs.py`、`tests/test_search_jobs.py`、`tests/test_search_coordinator.py`；Complete `search_coordinator.py`；Modify `service.py`接入点、`update_lock.py`仅短提交兼容。
**Interfaces:** `provider_factory: Callable[[],SearchProvider]`每个新任务创建独立取消上下文；`RadarService.collect_for_search(scope:SearchScope,budget:RequestBudget,cancel_event,on_progress)->tuple[ObservedRepository,...]`只采集/核算，不发布未核实新推荐。`SearchCoordinator(service:RadarService,store:RadarStore,provider_factory,clock).run(scope:SearchScope,generation:int,cancel_event,on_progress,*,continuing:bool=False)->SearchProgress`；`coordinator_factory: Callable[[],SearchCoordinator]`；`SearchJobs(store,coordinator_factory).start(section:str,keyword_id:int|None,now:datetime,*,manual:bool,continuing:bool=False)->SearchProgress`、`status()->tuple[SearchProgress,...]`、`cancel(job_id:str)->bool`、`shutdown()->None`。`SearchStore.claim_run(scope,manual:bool,now:str)->tuple[str,int,bool]`、`renew_run(job_id,generation,now)->bool`、`invalidate_run(job_id,reason)->None`。

- [ ] 写`test_201_candidates_stop_at_200_in_ten_batches`、`test_21_candidates_continue_after_first_batch`、`test_initial_plus_gap_search_max_two`、`test_cached_judgments_not_newly_counted`、`test_manual_preempts_auto_without_stale_publish`、`test_cross_process_claim_is_single`、`test_cancel_midnight_and_disabled_keyword_do_not_publish`、`test_deadline_caps_call_timeout`、`test_restart_after_dead_process_resumes_checkpoint`。
- [ ] 运行 `python -m unittest tests.test_search_jobs tests.test_search_coordinator tests.test_update_lock -v`确认新行为失败。
- [ ] 不先运行会发布旧词面新名单的完整旧refresh；仅复用其采集/官方核算职责，联网AI完成后由Task 5统一发布。未连接Codex时允许原公开数据降级，但必须标识未经AI核对。固定阶段扩词→程序/AI搜索→ID合并→真实证据→最多20个候选每批判断→排序/原子发布→仅入选预译。初始搜索1次、必要补充1次，单模块最多10个新增判断批次；失败停止当前模块并保留断点。缓存/来源游标/判定每批持久化。数据库租约带owner/generation/heartbeat，失联可接续；外部调用无长写锁。手动先取消自动代次，在途同任务复用；同进程统一串行消费请求预算；跨进程先取得唯一数据目录任务租约，避免两个采集器分别消费旧quota。其他模块排队但保留独立200上限，不并行抢占额度。
- [ ] 重跑命令通过；每模块全部成功才done，来源/前沿缺证据则partial，未连接/限额paused；失败不报空结果成功。
- [ ] 提交 `feat: coordinate incremental search with priority and cancellation`。

## Task 7: 搜索API、进度与扩词编辑

**Files:** Create `web_assets/search_controls.js`、`tests/search_controls_interaction.cjs`、`tests/test_search_api.py`；Modify `browser_server.py`GET/POST鉴权路由和更新入口、`web_assets/app.js`、`index.html`、`app.css`、`i18n.js`。
**Interfaces:** GET `/api/search/status`→`{jobs:[SearchProgress...]}`；POST `/api/search/start`接受`{section,keyword_id,continuing}`→202进度，同任务复用；POST `/api/search/jobs/{job_id}/cancel`接受`{}`；GET/POST `/api/search/settings`→`{enabled:bool}`。GET `/api/search/keywords/{id}/expansion`→`{original,terms,topics,version}`；POST同路径接受`{terms:[str...]}`，最多6个额外表达，清空恢复原词范围并使该词旧代次失效。`RadarSearchControls.create({api,post,byId,tr,onPublished}).load()/invalidate()`。

- [ ] 写HTTP权限/类型/未知字段/失效关键词/重复受理回归；交互`test_progress_updates_only_status`、`test_expansion_edit_preserves_original`、`test_automatic_cost_and_disable_visible`、`test_late_poll_after_navigation_is_ignored`、`test_continue_does_not_claim_complete_coverage`。
- [ ] 运行 `python -m unittest tests.test_search_api -v`及`node tests/search_controls_interaction.cjs`观察失败。
- [ ] 新API沿用Origin、会话token、8KiB请求限制，不暴露私人日志。状态数字有真实来源，不拿批次20冒充候选总数；显示缺口/缓存/新增核实/搜索与判断调用性质。设置自动AI默认启用但仅实际连接可用才调用，清楚显示账号消耗与关闭入口；不自动登录。既有立即更新通过任务协调受理，不被定时完成标志阻止。
- [ ] 重跑两个命令及`node tests/browser_interaction.cjs`通过；状态轮询不翻译不变文字，不重建静态标题。
- [ ] 提交 `feat: expose automatic search progress and controls`。

## Task 8: Windows无界面与浏览器更新一致

**Files:** Modify `daily_update.py::run_scheduled_update`、`__main__.py`、`browser_server.py::_refresh_worker/_scheduled_worker/_run_ai_job`、`ai_service.py`接入；Test `test_daily_update.py`、`test_cli.py`、`test_browser_server.py`、`test_auth_recovery.py`。
**Interfaces:** `run_scheduled_update(store,service,now:datetime,*,jobs:SearchJobs|None=None)->str`保持旧返回success/skipped/busy/error；浏览器/CLI由同一factory构建SearchJobs，`--scheduled-refresh`有界等待完成并退出，现有handoff优先复用运行实例。`AIService.refine_keyword`入口委派统一关键词任务，不再使用五卡即跳过的旧判断捷径。

- [ ] 写`test_manual_works_after_scheduled_success`、`test_scheduler_without_browser_runs_same_ai_search`、`test_no_codex_retains_public_partial_data`、`test_existing_browser_handoff_does_not_double_call`、`test_manual_detail_ai_does_not_hold_long_update_lock`、`test_cli_shutdown_stops_workers`。
- [ ] 运行 `python -m unittest tests.test_daily_update tests.test_cli tests.test_browser_server tests.test_auth_recovery -v`确认新增断言失败。
- [ ] 手动/定时复用同一Coordinator和模型选择；日常规则仍每日本机时间一次自动尝试，不把AI暂停当完整搜索完成，manual不看auto_due。旧详情手动AI长调用移到写锁外，保存前检查当前代次；运行时退出收尾子进程/租约/线程，避免悬挂任务和永远“更新中”。
- [ ] 重跑命令通过；已完成当天再次manual刷新仍扫描新候选/元数据、复用有效缓存。
- [ ] 提交 `fix: unify browser and scheduled update execution`。

## Task 9: 关注移动与创建归类原子事务

**Files:** Modify `follow_folders.py`、`storage.py`薄包装、`browser_server.py`路由；Test `test_follow_folders.py`、`test_browser_server.py`。
**Interfaces:** `move_project(db,repo_id:int,source:str,target:str)->None`；source/target为`unfiled`或正整数文件夹ID字符串，all拒绝；`classify_project(db,repo_id:int,ids:list[int],create_name:str|None,at:str)->dict`。RadarStore对应同名短事务方法。POST `/api/following/{id}/folders`新增`{action:"move",source,target}`保持旧ids/move_unfiled；POST `/api/following/{id}/classify`接受`{ids,create_name:null|str}`→`{followed:true,ids,folders}`。

- [ ] 写`test_move_keeps_other_folder_memberships`、`test_drop_to_unfiled_keeps_follow`、`test_all_source_rejected`、`test_removed_target_rolls_back`、`test_classify_unfollowed_atomically_follows`、`test_create_and_classify_failure_leaves_no_folder_or_follow`、`test_duplicate_name_and_concurrent_request_do_not_duplicate`。接口验证token/Origin和错误状态。
- [ ] 运行 `python -m unittest tests.test_follow_folders tests.test_browser_server -v`观察失败。
- [ ] 当前自建源→目标只删除源归属并添加目标，目标重复membership使用幂等插入；unfiled目标清所有归属不删follow。分类接受当前选择全集，create_name成功创建后自动加入新ID，BEGIN IMMEDIATE同事务关注+文件夹+归属；先校验仓库存在、ID、名称和200上限，同名冲突400不残留半操作。原文件夹排序/删除语义不变。
- [ ] 重跑命令通过；503不假报保存成功，未知响应重新读取真实状态后允许重试。
- [ ] 提交 `feat: classify and move followed projects atomically`。

## Task 10: 拖动范围与所有详情的右上角归类

**Files:** Create `web_assets/detail_classification.js`、`tests/detail_classification_interaction.cjs`；Modify `following_board.js`、`history_following.js`必要复用、`app.js`、`index.html`、`app.css`、`i18n.js`、`following_board_interaction.cjs`、`test_browser_ui.py`。
**Interfaces:** `RadarDetailClassification.create({api,post,byId,tr,onSaved}).open(repoId:number,followed:boolean)/invalidate()`；onSaved捕获repoId并刷新关注按钮/数量。详情右上角新增`detail-classify`按钮，主题dialog取代侧栏旧重复分类块。

- [ ] 写All不可拖、unfiled/custom可拖、同源不写、目标/多归属移动、拖动释放不打开详情和无bottom“移动到”断言。归类测试每个入口/未关注保存/取消/新建即生效/迟到项目切换/错误保留输入/Escape与焦点返回。
- [ ] 运行 `node tests/following_board_interaction.cjs`、`node tests/detail_classification_interaction.cjs`、`python -m unittest tests.test_browser_ui -v`观察失败。
- [ ] 复用现有cardNode拖动抑制标记，拖动源随开始捕获，前端不乐观移走卡片。右上角与仓库/README/关注并排，弹窗多选、创建并归类、保存/取消；200项可内部主题滚动，200%字号不扩侧栏。删除旧move按钮/旧侧栏重复入口，保留非拖动分类路径。成功按captured ID更新，旧代次不渲染；真实DOM渐显≤350ms。
- [ ] 重跑上述命令及`node tests/browser_interaction.cjs`通过，真实界面检查颜色/空列表/长名/键盘/减少动态。
- [ ] 提交 `feat: simplify watchlist drag and detail classification`。

## Task 11: AI类型定性、旧解读兼容与六卡高亮

**Files:** Modify `ai_types.py`、`ai_provider.py`、`ai_service.py`、`storage.py::_explanation`、`interpretation.py`、`presentation.py`、`browser_server.py::_project_payload`、`project_translation.py`、`web_assets/app.js`、`index.html`、`app.css`、`i18n.js`；Test `test_ai_provider.py`、`test_ai_service.py`、`test_interpretation.py`、`test_project_translation.py`、`browser_interaction.cjs`。
**Interfaces:** 新`ProjectKind(primary:str,secondary:tuple[str,...],zh:str,en:str,evidence:tuple[str,...],uncertain:bool)`，`ProjectExplanation.project_kind:ProjectKind|None=None`置于现有默认字段之后；schema_version=3。primary/secondary枚举software/skill/algorithm/model/library/framework/dataset/documentation/curated_list/plugin/service/other/unknown，secondary最多2项，说明每语最多300字符、evidence最多3项每项300。

- [ ] 写`test_explanation_requires_kind_and_evidence`、`test_skill_not_called_installable_app`、`test_curated_list_classified_as_content`、`test_multiple_roles_and_insufficient_evidence`、`test_legacy_v1_v2_explanation_still_loads`、`test_type_cached_with_same_explanation_no_extra_call`、`test_highlight_in_purpose_card_not_seventh_card`、`test_kind_text_is_escaped_and_ready_before_reveal`。
- [ ] 运行 `python -m unittest tests.test_ai_provider tests.test_ai_service tests.test_interpretation tests.test_project_translation -v`及`node tests/browser_interaction.cjs`观察失败。
- [ ] 同一次explain严格闭合schema返回ProjectKind，prompt要求依据公开资料定性并解释术语；unknown/uncertain写清不足。新解读缓存版本3，旧读取项目类型为空并显示“旧解读尚未注明类型，可更新解读补充”，不自动重做。六卡第一张首行第一句话以主题高亮完整定性句（说明这是什么、必要时区别于独立软件），再用途正文；双语直接字段与预译准备复用，保存后展开前准备完，避免抖动。
- [ ] 重跑命令通过，六卡数/网格/内部滚动与手动调用额度边界保持。
- [ ] 提交 `feat: identify project type in AI explanations`。

## Task 12: 完整回归、实际边界验证与独立复核

**Files:** 修改以上对应测试中的遗漏回归；脱敏结果写`docs/CHANGELOG.md`，原始材料只进入现有私有验收总册附件入口。
**Interfaces:** `RADAR_TEST_PYTHON`指向仓库.venv；所有真实检索用隔离数据，正式UserData不写。真实Codex验证共最多3次：一次公开关键词搜索、一次最多2仓库判断、一次项目类型解读；不真实跑200批量。

- [ ] 设置 `$env:RADAR_TEST_PYTHON=(Resolve-Path .venv/Scripts/python.exe).Path`，执行 `./.venv/Scripts/python -m unittest discover -s tests`，必须exit0；`./.venv/Scripts/python scripts/check_public.py --tracked`必须0flagged；`git diff --check`必须无错误。
- [ ] 在隔离数据/浏览器验证两榜单、扩词、继续/取消/并发manual、跨日、详情类型、拖动归类、中英文200%字号、减少动态、返回位置和静态文字不抖；不用正式数据作fixture，不仅测试镜像实现。
- [ ] 最多3次有界真实Codex调用，确认实际web_search事件、出处、真实ID补全和类型schema；任一次失败停止并记录，不无限重试。查公开来源真实页面验证分页能力，不因为mock通过就声称全量；未完成覆盖如实显示。
- [ ] 使用同一合成≥1000候选输入记录分页阶段后台工作集与预译峰值/缓存重开，对比原有按需翻译；查进度轮询/退出线程和进程残留。不用安装包体积代替内存，不写未经测试的整产品上限。
- [ ] 按保留的Native方法请一次独立审查覆盖整个分支，核对规范、并发/证据/迁移/缓存/隐私；修复反馈后仅重跑受影响检查，变化影响整体时再跑全套。此复核必须有实际独立结果；没有就明确尚未完成，不能自己审查冒充独立复核。

## Task 13: 版本、注释与统一用户/开发文档

**Files:** Modify `pyproject.toml`、`github_radar/__init__.py`、`codex_connection.py`客户端版本、`packaging/GitHubRadar.iss`、`README.md`、`README.en.md`、`docs/USER_GUIDE.md`、`DEVELOPMENT.md`、`AI_HANDOFF.md`、`CHANGELOG.md`；同步现有私有开发手册/验收总册。
**Interfaces:** 新运行版本0.5.0，拟发布`v0.5.0-preview.1`；标签存在时停止核对，不重写已发布标签。升级身份全部保持；桌面快捷方式引用安装内StarTrail软件图标，不能引用临时/开发目录。

- [ ] 更新版本一致性测试`test_public_distribution`和installer data safety断言，运行 `python -m unittest tests.test_public_distribution tests.test_installer_data_safety -v`观察旧版本不一致断言失败。
- [ ] 实现版本同步，关键函数注释说明UTC/指纹/预算/事务/取消原因。文档只写实际完成行为、准确AI成本/有限覆盖、排名口径、归类/类型操作、迁移/回退和实际未测限制；作者口吻、七栏README、独立英文及已有截图保留，不复制重复讲解。
- [ ] AI_HANDOFF列新增模块、API请求/响应/错误/幂等、表结构、代次/租约、缓存失效与Windows入口；每个任务实际命令/结果集中CHANGELOG，不另散建报告。
- [ ] 重跑版本测试、全体Markdown本地链接检查、公开扫描和差异，必须通过；未构建的安装包不能写成已交付。
- [ ] 提交 `docs: document StarTrail 0.5 search and classification`。

## Task 14: 新安装包与隔离升级验收

**Files:** 按需修复`packaging/build.ps1`、`GitHubRadar.spec`、`GitHubRadar.iss`与对应测试；产物仅忽略目录`.build`和既有私有用户交付入口。
**Interfaces:** 使用现有.venv/.tools模型；`pwsh -File packaging/build.ps1`输出`.build/StarTrail_Setup.exe`；`Get-FileHash -Algorithm SHA256`生成实际摘要，安装与源码0.5.0对应。

- [ ] 全套回归仍通过后本机构建，核对冻结程序包含新模块/JS/主题与schema，构建失败修复并重跑相关检查；不得拿旧preview.1包交付新功能。
- [ ] 隔离安装目录/虚构UserData验证启动、浏览器、更新/定时/无Codex、真实元素动效、本地翻译、归类/类型、退出、旧格式数据库升级保留历史/关注/关键词、二次运行和卸载保留数据。不得覆盖日常安装或正式数据库。
- [ ] 中文路径/System32-only PATH运行冻结程序翻译测试，版本/模型/第三方许可核验通过；核对桌面快捷方式显示当前软件图标、升级刷新旧图标、目标仍为本安装可执行文件。只清理确认指向此安装的旧重复入口，不碰其他快捷方式；记录真实包尺寸和SHA256。第二台干净Windows/未签名/长期实测缺口保留说明。
- [ ] 将本机核验包和对应用户手册放固定“给用户”入口；如果最终云构建替代，本机包先归档，最后该入口只保留实际公开且校验过的新资产。
- [ ] 在现有验收总册记录构建源码、哈希、隔离环境和结果，不提交二进制/私人验收到源码Git。

## Task 15: 文件整理、最终GitHub源码与发布验证

**Files:** 更新现有私有清理与卸载README/开发手册/验收总册/项目记录；公开Release说明由CHANGELOG实际条目整理。
**Interfaces:** 现有手动build工作流权限与触发条件不扩大。发布目标是本地已验证源码；GitHub仓库StarTrail，main普通push只运行check，手动dispatch实际新标签构建/发布。

- [ ] 核对工作区/活动Repo/旧构建清单，将确认已替代且未注册的旧包、重复材料和本次临时产物移入既有可删除备份，逐项记录来源；移动前解析绝对目标并验证在授权目录。活动.venv/.tools、正式UserData、注册安装路径不移动；旧验收软件只整理可靠卸载入口，由使用者自行卸载。
- [ ] 审查 `git status --short`、暂存差异和全部待推提交内容，确认没有凭证、私人路径/邮箱、数据库、日志、二进制、宣传图/论文或旧私有历史。运行公开扫描及 `git diff --cached --check`；提交安全源码/文档，最后才推送main。
- [ ] 创建指向同一已验证提交的新标签和Release草稿，核对标签未存在，普通push不发布。手动运行已授权Windows installer工作流；工作流全套检查/构建/资产摘要校验必须真实成功，不绕过失败。等候状态用退避且保持有意义更新。
- [ ] 下载实际公开资产并核对SHA256及对应源码版本，隔离安装复核必要变化；核对README入口、手册和下载。云端不同构建哈希必须用对应实际云资产与记录，不能套本机包摘要。
- [ ] 同步固定“给用户”入口为最终校验的公开安装包/手册，集中日志写实际版本/提交/CI和检查限制；最终交付给出源码、下载、手册与可删除入口，并明确未测事项。

## 自查与实施接续

1. 设计覆盖：主动搜索/扩词/多源合并 Tasks 2–3；语义/证据缓存 Task 4；两榜单排序/展示历史/快照 Tasks 1、5；自动预算/并发/日常 Task 6–8；拖动与归类 Tasks 9–10；类型定性 Task 11；核验/文档/安装/清理/公开 Tasks 12–15。
2. 接口类型以本计划顶部为准，新增方法属于SearchStore/Runner/Coordinator/Jobs，不在旧类中复制同名独立实现。旧接口兼容路径逐项测试，SQLite写入仍由RadarStore管理连接。
3. 以上步骤均未勾选。执行前审阅本计划；沿用当前对话由主代理逐项实现、末尾一次独立复核的Native方式，不另建对话或每任务重复派工。
