# 产品对接与 AI 开发接续

此文档以当前源码为准；工程变化集中在 [开发日志](CHANGELOG.md)，环境和打包见 [开发指南](DEVELOPMENT.md)，用户操作见 [用户手册](USER_GUIDE.md)。接续时先读根目录 AGENTS.md，再核对 Git 状态和实际代码。

## 当前实现与性能验收

默认使用本机无审核检索路径。AI 只保留关键词扩词，固定站点直接采集；增长无 AI，整组和逐条审核协议仅保留兼容测试，不在自动更新中调用。当前交付验证、真实调用耗时与尚未完成的验收统一见开发日志；不要把模拟计时或小样本当作完整整轮证明。

## 本轮修复接续

数据库锁、候选分页和单轮额度收尾修复已完成本地验收并发布 preview.10，发行源码84fd5fc。重复启动在数据库初始化前转交健康实例；当前结构重新打开不申请迁移写锁，锁冲突返回受控原因。额度事件跨榜共享，首次不足后不再新增采集，已发出的请求收尾并复用有效缓存；并发预算拒绝不冒充网络故障。搜索页穿插日增采集，同星数候选使用分段索引分页，跨规则只合并100身份的小页，重复导入不写相同内容。schema8加入目录repo_id／last_scored_at索引，避免候选×目录的全扫联结和逐项更新全表扫描。当前正式入口默认25路有界连接，接口上限仍100，secondary限流继续降档。

`prepared` 是等待其他榜单，实际保存才用 `publishing`；波次内进度即时回传，来源失败即时传递并停止其他模块。批量只读元数据连接中断最多重试一次，不重试额度、授权或格式错误；租约心跳遇暂时锁冲突下轮重试，真实过期或其他数据库错误仍取消。软件发布元数据的网络失败不再被 JSON 异常捕获而误报格式错误。排序、UTC日界、排重、主题匹配和升级身份保持；验收以开发日志为准。真实隔离联网整轮已保存两榜10项，587.730秒，首次额度边界结束；增长6576份有效证据含6574缓存，AI扩词复用0秒，约18万候选尚有待处理，不能当成首次无缓存速度或完整覆盖。847项回归（跳过1）、最终schema8冻结程序和独立复核通过。正式安装升级、跨夜睡眠不能由隔离回归冒充。

## 接续入口与整理状态

1. 当前工作区只维护活动仓库，不使用旧私有源码发布。构建与隔离验收可以在忽略目录`.build`重建；虚拟环境、离线模型及正式安装不随整理移动。
2. 当前实现新增简洁状态、明确前台任务关联和发表快照诊断；历史preview.9交付证据见CHANGELOG。本轮preview.10源码检查及安装包构建通过并公开；当前交付为已验收同源本地包，完整云包下载未完成，不能混用摘要。云端构建发布成功；本机完整云包下载因TLS/读取超时未核验，本地交付为已验证同源构建，不能把它写成云包实际下载或安装成功。旧preview.8验收与整理记录只作为历史资料。
3. 交接顺序：AGENTS → README → DEVELOPMENT → 本文 → CHANGELOG → 对应模块和测试。先检查未提交差异，保护用户数据；待验收范围见下文，不将历史报告当现行完成证明。

## 当前现场核对与展示契约

1. 本次只读核对确认曾成功保存新的一轮两榜；任务结果ok、当天完成记录和实际推荐同时存在。现场核对只证明保存和所见事实，不能替代全站覆盖、首次运行速度或每条早期数据的独立证明。
2. 当前前台状态通过foreground_active/foreground_progress关联整轮refresh和手动continue_keyword；后者使用AI worker，busy可能为false。API的search_cancellable与active_search_progress还核对running refine，排除项目解释和后台准备，不读取旧磁盘running记录作为现场进度。不得把后台600秒有限prepare或旧模块任务当成“正在更新”。前台无整轮固定分钟截止，额度到预留线按已验证范围结束。旧进度中的时间保护不是quota_limited。
3. 用户首页只呈现简短阶段、必要数量、保存时间和有限覆盖提示；完整source_status、notes和历史耗时保留后端及私有诊断。额度提示要求最近尝试status=ok、quota_scoped=true且published_at与当前保存时间完全相等；失败/取消的continue也写status=error、quota_scoped=false、published_at=null，避免沿用旧成功。
4. search_publication_log保存发表时快照，与两榜原子提交共用事务。记录当前官方缓存与关键词依据，不补写旧证据，不改变排名；只读导出见DEVELOPMENT。

## 1. 当前状态与交付门槛

1. Python标准库后端、原生网页、SQLite、默认浏览器，Windows x64优先；旧包名、EXE、AppId和数据标识保持兼容。
2. 当前preview.10运行源码84fd5fc，默认25路有界共享连接（开发接口最多100路）；稳定ID恢复、名称／主题匹配、无审核直接采集及单轮额度范围发布保持。847项完整回归（环境跳过1）、最终冻结程序旧库迁移、独立复核、云端检查和构建通过；真实隔离缓存续跑587.730秒，两榜10项发表并经只读证据复核。AI扩词复用0秒，不代表首次无缓存速度。软件匿名更新发现通过，完整云包下载未完成，本地交付同源已验收构建；摘要和发布证据见开发日志。
3. 正式安装及UserData不用于写入测试，公开元数据仅从只读连接导入隔离库。默认路径关键词仅扩词、增长无AI；未知项目不得标为AI已审核。本轮不运行安装器，详细证据见CHANGELOG。
4. 验收范围、源码标签、实际云包SHA、构建与检查结果统一写开发日志；第二台干净Windows、真实睡眠跨夜和大候选真实AI仍未验证。

### 更新恢复、状态与通知契约

1. SearchSources 读取 RadarStore.recent_growth_repositories(local_date, limit=200)，不得使用不存在的 latest_growth_repositories。SearchCoordinator 在创建 RequestBudget 前调用真实 GitHubClient.renew_expired_quotas；只能重新探测已过服务器重置时刻的额度。
2. settings.search_incomplete_date 和 search_incomplete_owner 保存整次更新状态及 JSON [job_id, generation, owner]。mark_search_incomplete 在 BEGIN IMMEDIATE 中验证活动租约和现有所有权；整次成功须在释放 refresh 租约前结束标记。任何单模块 daily_runs 记录都不能替代整体完成。
3. auto_update_due 的 incomplete 参数只绕过“当天已有结果/曾成功”门禁，仍守住三个尝试与一小时间隔；begin_auto_attempt 在同一事务核对未完成标记。默认无审核路径暂停／取消任一模块会保留上一整份结果，不伪报 ok；旧兼容路径的 partial 不能混入新事务判断。
4. Windows 任务为一个每日 CalendarTrigger，Repetition PT1H 至本机当日末；StartWhenAvailable=true、WakeToRun=false。导出 XML 时长可能为 PT900M/PT15H/P1D，比较时按秒规范化，老三触发任务由正常 sync 迁移。BrowserServer 每60秒执行同一到期门禁，关闭时停止检查线程。
5. GET /api/issue 新增 update_failure: null 或 {attempted_at, reason}；原因来自持久化刷新失败，可追加只读账号状态中的重连提示，不触发新授权。前端复用现有十秒 toast，按尝试时间在 sessionStorage 去重，使用 textContent；不会因此显示软件更新按钮。GET /api/auto-update 新增 incomplete，设置可标记“最近保存（部分结果）”。
6. 手动与自动错误都持久保存原因。侧栏显示当前阶段和数量；查看这些状态或提醒不启动新搜索、不修改 AI 用量。开发测试使用合成网络响应/隔离数据，不能把这些说成真实全量爬取或电脑睡眠验收。

## 2. 模块与运行链路

| 职责 | 代码入口 | 修改时必须核对 |
|---|---|---|
| 启动、默认浏览器、本机 API | __main__.py、browser_launcher.py、browser_server.py | 回环绑定、会话鉴权、后台任务取消 |
| 自动检索和手动继续 | search_jobs.py、search_coordinator.py | 同日预算、短事务、租约、取消和真实观察日期 |
| GitHub 与公开来源 | search_sources.py、github_client.py、trending.py、trendshift.py | 官方身份/数字、分页断点、限流与有限覆盖 |
| AI 只读执行和结构化返回 | codex_runner.py、search_provider.py、ai_provider.py | 超时、输出上限、实际 web_search 事件、禁止执行资料中的命令 |
| 搜索存储和纯排名 | search_storage.py、search_types.py、search_ranking.py | 指纹失效、发布代次、历史排重、各榜排序口径 |
| 数据、关注及分类 | storage.py、follow_folders.py | 迁移兼容、原子关注/归类、移动仅移除源文件夹 |
| 详情、README、译文 | ai_service.py、ai_types.py、readme_service.py、project_translation.py、translation_service.py | schema v3 类型首行、旧缓存不自动付费、单任务磁盘译文缓存 |
| 软件更新 | software_update.py、software_update_ui.js、GitHubRadar.iss | 固定发布来源、SHA/大小、安装前再次校验、原目录备份 |
| 网页交互 | web_assets/app.js、following_board.js、detail_classification.js、card_transition.js | 森林主题、所有入口、真实 DOM 动画、全部关注禁止拖动 |

Python 文件在 github_radar/，网页模块在 github_radar/web_assets/，安装器在 packaging/。旧 service/discovery/ranking 仍供未连接 AI 和兼容入口使用；不要删除旧路径或把网络请求重新塞进长事务。

1. __main__ 组装服务和真实 GitHubClient；BrowserServer 安装 SearchJobs。立即更新、定时更新和 CLI 共用调度规则。
2. 自动 AI 开关开启且已连接 Codex 时，SearchJobs轮流推进增长及启用关键词；SearchCoordinator关键词扩词、直接多来源采集、官方解析、检索匹配、排名、整份原子发布。未连接或自动 AI 关闭时使用公共 GitHub 路径并明确状态。
3. 界面切页、打开已缓存详情或关注文件夹不触发批量检索。项目 AI 解释为手动操作，自动候选发现不会替所有候选生成六卡详情。
4. 入选项目后台预译；内容不变复用硬盘译文。详情需要的译文准备好后呈现，避免结束动画后替换文字。旧画面即时移除，新真实 DOM 约 350ms 渐显。
5. 软件升级与“立即更新”数据刷新分开。匿名启动检测、六小时节流；发现匹配的新安装包和哈希后才提供软件更新入口。

## 3. 两个榜单与本机任务规则

1. `SearchCoordinator(legacy_review=False)` 经 `search_local.local_steps` 运行：关键词扩词缓存、公开来源分页收集、稳定身份更新、官方统计、匹配、纯数字排名。`legacy_review=True` 只供旧协议隔离回归；手动六卡解释仍独立。
2. 增长榜使用原完整 UTC 日和周边界校验，日增／总 Star／ID 排序及回归／新发现角色保持。关键词使用原词和最多六扩展；新的名称／GitHub主题查询命中保存在 `search_matches`，同字段短语与明确网站分类作为当前相关性依据；简介及README偶然提及不满足默认严格规则，元数据内容指纹不包含 Star；无可靠检索依据为 uncertain，不显示 AI 已审核。保留原历史排重、最低 Star、归档和关键词顺序。
3. `search_candidates` 按身份分页，模型／阈值改变导入同关键词旧库，先用窗口函数选每 ID 最新观察再分页。`discovery_catalog` 完整分页，不能仅导入五百条；404 是当日不可推荐状态，下一日重查，历史不删除。缺失或旧名称变成其他 ID 时按原 ID 恢复。
4. `search_star_days` 的 UTC 日、`utc-sunday-v2`规则和 ID 构成缓存键；有效零计数可用，六小时过期或时间倒退不复用。数值缓存仅保留三十天，候选和推荐历史不因此清空。旧元数据可用 GraphQL 每批二十读取，总 Star 不能冒充日增。
5. `SearchJobs.refresh` 让所有新协调器 `defer_publish=True`；准备完成后按增长→关键词顺序重排占位，`SearchStore.publish_all` 在一个 BEGIN IMMEDIATE 中验证全局／模块租约、所有启用关键词、模型、日期、扩词和快照。任一失败全部回滚；持久进度 ready 改 paused，失败不显示 ok。较旧共享元数据不覆盖较新快照和仓库字段。
6. 同轮 REST 公开请求共享磁盘缓存，跨轮日增单独缓存。标准库 HTTPS 连接池最多100个、同源跳转、完整读完才复用；有代理配置使用现有 urllib 回退。GraphQL 和 REST 额度独立，401 恢复保留 POST 正文，凭证不写入缓存。网络与 AI 不在数据库事务内执行。
7. 关键词扩词最多两个调用并行，缓存有效时不再调用；默认路径不调用 search_repositories，增长榜无 AI，继续检索只推进直接来源；没有自动审核数上限，所有符合来源条件的候选进入程序排名。计时在实际 AI worker 内测量，取消／超时／生成器关闭取消未结束调用，不能只丢弃 future。
8. `SearchJobs.prepare(cancel_event)` 仅本机醒着空闲时准备公开事实，无 AI 调用和榜单发表；持久候选／分区／官方日增可被前台复用。BrowserServer 十分钟内最多启动一轮、每轮保护十分钟。先注册固定取消事件再暴露线程；前台刷新、手动 AI、README 读取取消并等待准备退出，不并发修改共享 client.budget。睡眠不唤醒；到期检查仍遵守每天三次／间隔一小时。
9. 阶段进度最多每秒持久更新（阶段／限制／AI 起止立即保存），避免逐候选 SQLite 写入。整次墙钟从调用入口计到保存，包含 readiness、模型选择、AI及失败；模块阶段计时排除轮转挂起。并行 AI 累计时长可能超过墙钟，不能把阶段之和写成用户等待时间。
10. `search_incomplete_date/owner` 在有效 refresh 租约内结束；仅完整事务成功才清理。日增缺边界、必需官方来源不完整、AI失败均保留整份旧结果和原因；额度到线按下方新契约发布明确范围，不将未核算当零、不仅查第一页、不以部分发布或后台补齐冒充达标。之前20分钟目标仅在隔离候选规模验证，未证明正式用户完整范围达标；当前前台已取消整轮时间截断，冷启动／长期睡眠／限流等实际耗时必须记录。
11. 软件升级与数据更新分开，原数据保留、自动安装包失败清理、十秒可关闭通知、主题／图标、拖动、归类、类型首行、离线翻译均保持。

## 4. API 对接契约

API 仅限本次回环实例。GET 需要 X-Radar-Token；POST 同时验证 Origin。普通 JSON 最大 8192 字节、翻译正文最大 524288 字节。200 是处理/读取成功，202 是受理，400 参数错误，403 会话错误，404 不存在，409 冲突，503 依赖不可用。不要把 202 当完成，实际字段以 browser_server.py 的 payload 为准。

| 方法/路径 | 请求与结果 | 状态/副作用 |
|---|---|---|
| GET /api/health | status、instance_id、pid | 实例存活识别 |
| GET /api/issue | 保存日期、推荐、任务和检索进度 | 只读，静态头部不要因轮询重播 |
| POST /api/refresh | 空对象 | 后台数据更新；复用活动任务，手动不依赖计划时间 |
| GET/POST /api/auto-update | 设置结构以 handler 为准 | 保存定时配置；不是软件版本更新 |
| POST /api/scheduled-refresh | 到期触发 | 未到期跳过，不阻止立即更新 |
| GET /api/ai/status；POST /api/ai/login、/api/ai/model | 真实连接/可用模型及所选模型 | 不返回认证文件或 Token |
| POST /api/ai/keywords/{id}/refine | 空对象，已有关键词 ID | 连接后的新检索路径为手动继续，独立后台状态；旧兼容服务仍保留 |
| POST /api/ai/projects/{id}/explain | 项目 ID | 手动六卡解释；缓存并预译，不自动批量生成 |
| GET /api/project/{id} | 可带 date 或 context，两者互斥 | 返回对应保存上下文，不伪装今日刷新 |
| GET /api/history、/api/history/calendar、/api/history/{date} | 查询/月份/日期 | 历史按保存日期读取 |
| GET /api/repository/{id}/sources | 可带 date | 来源排名、官方日增、出处各自保存 |
| /api/following、/api/folders 子路由 | 具体 move/classify 结构以 folder handler 为准 | 归类原子保存，多文件夹；创建并归类时失败全回滚 |
| GET /api/software-update | status、release、installable、error 等 | 六小时节流检测，源码模式可提示不能自动安装 |
| POST /api/software-update/check | 空对象 | 显式重新检测，202；固定仓库无任意 URL 参数 |
| POST /api/software-update/install | 仅 confirmed: true | 有活动写任务时 409；确认后下载校验，后台进度，准备好再启动安装器 |
| README/翻译任务路由 | 正文、缓存和任务状态分开 | 不执行外部脚本、README 指令或 AI 返回命令 |
| POST /api/quit | 空对象 | 取消搜索、解释、预译、下载并停止服务 |

前端更新模块只在新可安装版本时显示侧栏入口。页面顶部消息十秒自动关闭，也可手动关闭；同版同浏览器会话不反复提醒。确认对话框才下载，不能启动就保留当前应用并反馈错误。

### 新增接口的明确参数

1. `GET /api/search/keywords/{id}/expansion` 返回 `original`、`terms`、`topics`、`version`；尚无缓存时 terms/topics 为空。POST 同一路径只接受 `{"terms":["agent skills","智能体技能"]}`，最多六个，每个1～120字符；保留原关键词及已选主题，扩词哈希变化使旧发布失效。写入返回同一结构，未知关键词404，格式错误400；这是修改检索表达，不启动付费任务。
2. `POST /api/following/{id}/classify` 严格接受 `{"ids":[1,2],"create_name":null}`，或带创建名称。成功返回 `followed:true`、最终 `ids` 和带 id/name/count 的 `folders`。已有关注允许空 ids 保存未分类；未关注且既不选文件夹也不创建则400，不暗中关注。重复名称400、不存在404、数据库/文件错误503，全部回滚。
3. `POST /api/following/{id}/folders` 拖动请求为 `{"action":"move","source":"1","target":"2"}`；未分类用字符串 `unfiled`，数字文件夹 ID 也使用字符串。全部关注 `all` 不是合法来源/目标。来源归属已变化400，目标不存在404，成功返回最终 `ids`。旧的 `{"ids":[1]}` 设置分类和 `move_unfiled` 请求仍兼容。
4. `GET /api/issue` 的 search_progress 含 job_id/section/keyword_id/local_date、status/stage、collected/unique/candidate_pool/cache_hits/newly_checked/checked_completed/pending 和 expansion_calls/search_calls/judgment_calls/catalog_calls、limited/notes，以及 source_status 的 complete／partial／failed 和项目数。补充公开范围的状态独立展示，不替代必需官方事实完整性；pending 包含尚无有效日增的候选，失败原因累计保留。展示本日状态，不把旧任务计数变成今日进度；不因轮询重复生成卡片动画。
5. `GET /api/search/settings` 返回 `{"enabled":true}`，缺省开启。POST 同路径严格接受布尔 `enabled`，短事务保存；关闭成功后取消当前 AI 检索。GET `/api/search/status` 返回本日 `jobs` 数组；POST `/api/search/cancel` 仅接受空对象，202 表示正在停止，不等于任务已退出。未知搜索路径404。自动关闭时数据刷新使用公共路径；显式手动继续仍可使用，模块之间停止返回部分完成及未执行说明。设置读取/保存以页面代次隔离，迟到 GET 不覆盖刚保存的额度选择。
6. 安装下载确认只接受 `{"confirmed":true}`；服务端没有任意 installer URL、安装目录或外部命令参数。software_update 状态 idle/checking/available/up_to_date/error/downloading/ready/cancelling/cancelled/installing，release 包含 tag/url/notes/installer_url/sha256/size/prerelease；installable 标明是否冻结安装版，downloaded/total 用于进度。

## 5. 存储、失效和并发

1. RadarStore 管理 radar.db。repositories 是最新元数据，star_snapshots 是带真实 observed_at 的日期快照，recommendations 是当天入选上下文。displayed_repositories 保留实际展示历史；删除/替换当天卡片不擦掉已经展示的历史。
2. SearchScope 包含模块/关键词、本地日、统计日、原词、门槛、模型、规则和扩词哈希。query_expansions、search_candidates、search_readmes、search_judgments、search_runs、search_cursors、search_http_cache 管理扩词、观察、README、判断、租约/预算和来源断点。
3. 语义缓存按模型、规则、关键词配置、README 内容指纹失效，排除 Star 和日期；证据缓存还包含官方统计日及出处。未知或旧 schema 的类型不自动花额度重解读。AI 类型使用 schema v3，含双语首行定性、证据和不确定状态。
4. run lease 核对 owner/generation/expiry；同一天调整模型不会洗掉已花预算。发布事务重新验证日期、统计日、关键词启用/配置、扩词和当前模型，旧任务不得覆盖新设置或跨日结果。
5. 网络和 AI 在数据库事务之外；frontier 分页读完关闭游标才处理。数据库写入短事务，更新租约有界；不要用延长锁时间代替并发设计。取消是状态，不是成功的空榜。
6. 全部关注是汇总不可拖动；未分类和自建文件夹允许拖动。拖到目标只移除源文件夹，不删除其他分类；拖到未分类清空成员关系但保留关注。所有详情入口的归类保存自动关注，取消不写入。

## 6. 安装升级与数据恢复

1. 安装身份仍为 GitHubRadar，应用内版本来源是固定 StarTrail GitHub Releases。稳定渠道排除预览；检查 release、安装包名、SHA256SUMS、大小、下载域名和版本，绝不采信 AI 给出的安装地址。
2. 下载流式写入专用缓存，校验完成才可用；启动前重新验证大小和 SHA。仅冻结安装版、自己的原安装目录和 UserData 标记允许自动安装；源码模式手动下载。
3. 应用正常退出写任务后，安装器取得维护锁、停止旧进程/计划任务，再复制 UserData 到 Backups/pre-update-*。跳过嵌套 Backups 与软件下载缓存，拒绝重解析目录，完整成功才写 BACKUP_COMPLETE.txt。失败停止覆盖。
4. 安装器只更新程序文件，保留关键词、历史、关注、文件夹、设置及认证状态。快捷方式显式使用 startrail.ico。新装/直接下载重装和应用内更新共用同一安装器规则。
5. 恢复前关闭软件和计划任务，先另存当前 UserData；只使用带完成标记的备份恢复，不能在运行时替换数据库。认证加密受本机 Windows 用户约束，跨电脑不保证凭证可迁移。
6. 不移动真实已安装目录来整理文件；卸载入口与路径绑定。旧验收程序只记录给用户卸载，临时报告和构建产物放集中可删除目录，不进入 Git。

## 7. 发布前清单与接续约束

1. 核对 Git 差异、所有新增文件、文档相对链接和图片，不含真实数据库、Token、认证日志、电脑绝对路径、原始聊天与私人验收附件。
2. 跑完整 Python 回归、Node 交互、公开文件检查、diff --check；付费 AI 验证单列次数及失败，不能用模拟事件冒充真实联网验证。
3. 独立代码复核、冻结程序、隔离中文路径安装、覆盖升级、备份完整性、快捷方式图标及安装包 SHA 都需真实证据。干净第二台 Windows 未验证就明确保留限制。
4. 源码版本、安装器版本、RELEASE_TAG 与发布标签对应，先本地保存，再推送并发布匹配安装包。README 只描述当前产品；每次提交的变化写提交说明和统一日志。
5. 下次开发从 CHANGELOG 的最新实际证据开始；不要重复请求已获授权的实现，也不要把未发布草案当交付成功。

设置手动检查入口 settings-software-check 复用 POST /api/software-update/check 空对象，force=True 绕过自动六小时节流；与正在进行的检查/下载复用任务。界面防重复、晚到GET以代次隔离，不启动数据更新、AI或下载。软件版本/结果显示在设置，侧栏仅有匹配新安装包时显示；提醒10000ms自动关闭并可提前关闭。

## 整组分析与取消的具体对接

1. `SearchProvider.filter_catalog/filter_batch/assess_growth_batch` 为旧协议兼容，默认 `search_local` 不调用；不要让界面把 `search_matches` 误标成这类审核。
2. `GET /api/issue.refresh_timing` 为最近整次尝试的 elapsed_seconds、ai_seconds、stage_seconds、status、attempted_at，包含失败。search_progress 新增 search_mode、matched_count、started_at、elapsed_seconds、ai_seconds、ai_started_at、stage_seconds、official_checked；默认值可读取旧 JSON。
3. `SearchJobs` 暂存 prepared publication，完成全部后一次事务保存，模块之间释放的占位由最终 rerank 重新检查。未结束 generator 必须 close，future／provider 必须停止。
4. `SearchRequestCache` 仅同轮 run_id 命中公开 REST 元数据，保留至多最近两轮记录而不跨轮读取；跨轮日增依据由独立日期缓存处理，不把未知旧信息改成今天保存。
5. `POST /api/software-update/cancel`仅空对象、会话/Origin验证；返回202表示已请求。`cancellable`由状态提供：downloading先设取消事件，退出写文件后删除目标；ready/error可安全清理，仅当前自动包和.part，拒绝符号链接或路径逃逸，失败说明原因。checking/installing拒绝，原数据库和备份不动。
6. 下载重试归零；真正启动安装器后设置installing，网页轮询接收后停止旧服务交互。100%加Failed to fetch只说明连接中断，不作为安装器已启动证明；保留后续查询和失败处理。没有增加强制安装或绕过Windows安全提示。

旧协议兼容测试的整组分析计数说明（默认产品路径不执行）：newly_checked 与 checked_completed 是新增候选的提交/成功判断累计数，不是Token量。若出现新增或语义失效资料，统一分析输入仍包含当时全部资格候选（也包含有缓存的条目）；cache_hits表示准备阶段命中，并不保证这些条目在整组调用中不重复占输入。没有待新增判断时直接复用，不调用模型。

## 单轮额度范围发布契约

1. 最新维护者授权覆盖旧「额度耗尽必须等待完整核算」规则：本轮用实际可用配额，到安全预留线结束网络工作，读完同统计日有效缓存并发布已验证范围；没有固定5000候选限制。真实失败仍阻止整份原子提交。
2. SearchProgress新增向后兼容默认字段quota_limited、failed、metadata_pending（尚未验证的来源元数据）。limited为覆盖提示，quota_limited为配额截止，failed独立记录真正故障；二者可同时为真，此时不能发布。local_steps开始重置本轮计数与标志。SearchSources._quota_stop独立于limited失败setter；明确GitHubRateLimitError及实际预算截止可归类配额，网络和格式错误不可按配额吞掉。
3. _frontier不等待下一小时，剩余行仅读取有效磁盘证据，不发无额度请求；已知零日增算完成，未知不算零。未恢复transient错误单独记录，配额不足不能跳过后误报成功。没有有效事实时保留旧榜，不能用空结果覆盖。
4. 两榜defer publication仍status=ready；publish_all一次事务成功后，额度截止模块partial，完整模块done。整轮status=ok但message明确额度范围，search_incomplete=False表示此次范围已提交，不代表全站穷尽。取消、跨日、配置变化、租约、磁盘错误保持旧榜。
5. 原词与有限扩词按页轮转，高Star分区在前。growth_frontier仅冻结身份，按100行读取payload；当前趋势依据source_evidence真实observed_at，不用tracked刷新过的目录last_discovered_at。之后是前期增长榜和长期未核算工作，最后仍按官方日增、总Star、ID排序。关键词严格名称/主题/明确分类匹配后总Star排序，历史和跨模块优先级不变。
6. GraphQL HTTP200只有明确RATE_LIMITED错误可当额度；其他不完整data继续失败。动态产品状态在Python tr和网页localizeServerText两端翻译，日期和数量保留，未知作者文本不翻译。不要用离线机器翻译掩盖缺失产品状态映射。
7. 候选、身份、来源、断点、扩词与推荐历史持久保存；日增含零保留30天、同日6小时复用。正式UserData不得用作写入测试；不要执行安装器。当前代码验收与安装交付状态以CHANGELOG为准，不能复用preview.7的147秒宣称新逻辑实测速度。

8. 来源批次在任何yield和后续topic请求前逐批入库；Trending读取成功后创建SourceEvidence记录来源位置，不伪造日增；持久化sqlite错误不得被辅助来源网络catch吞掉。GraphQL明确纯RATE_LIMITED才额度，混合错误保持失败；统一renew含graphql恢复。分页产出指标保存在search_cursors的github-yield命名空间，不影响匹配口径。
9. 最终本地822项133.241秒回归、Node两项、公开扫描和冻结直接运行验收通过；网络试验241.409秒为有效缓存完整完成，两榜done、10项原子提交，不代表首次无缓存，也不是实网额度耗尽证明。当前安装包尚待GitHub发布，状态以CHANGELOG为准。
