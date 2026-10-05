# 产品对接与 AI 开发接续

此文档以当前源码为准；工程变化集中在 [开发日志](CHANGELOG.md)，环境和打包见 [开发指南](DEVELOPMENT.md)，用户操作见 [用户手册](USER_GUIDE.md)。接续时先读根目录 AGENTS.md，再核对 Git 状态和实际代码。

## 1. 当前状态与交付门槛

1. Python标准库后端、原生网页、SQLite、默认浏览器，Windows x64优先；旧包名、EXE、AppId和数据标识保持兼容。
2. 当前代码正在准备 v0.5.0-preview.5；已发布资产仍为preview.4，不能把本地改动当作公开交付。合并检索、整组判断、低并发共享缓存、失败清理和网页交接已实现；最终验证与发布证据见开发日志。
3. 正式安装及UserData未用于测试。本轮零真实AI调用，不运行安装器；隔离网络/AI模拟与直接冻结运行单独记录，不能当作真实大候选模型验收。
4. 验收范围、源码标签、实际云包SHA、构建与检查结果统一写开发日志；第二台干净Windows、真实睡眠跨夜和大候选真实AI仍未验证。

### 更新恢复、状态与通知契约

1. SearchSources 读取 RadarStore.recent_growth_repositories(local_date, limit=200)，不得使用不存在的 latest_growth_repositories。SearchCoordinator 在创建 RequestBudget 前调用真实 GitHubClient.renew_expired_quotas；只能重新探测已过服务器重置时刻的额度。
2. settings.search_incomplete_date 和 search_incomplete_owner 保存整次更新状态及 JSON [job_id, generation, owner]。mark_search_incomplete 在 BEGIN IMMEDIATE 中验证活动租约和现有所有权；整次成功须在释放 refresh 租约前结束标记。任何单模块 daily_runs 记录都不能替代整体完成。
3. auto_update_due 的 incomplete 参数只绕过“当天已有结果/曾成功”门禁，仍守住三个尝试与一小时间隔；begin_auto_attempt 在同一事务核对未完成标记。暂停/取消模块导致整体 partial，不伪报 ok；正常预算覆盖有限但已发布模块仍维持 partial 模块语义。
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
2. 自动 AI 开关开启且已连接 Codex 时，SearchJobs轮流推进增长及启用关键词；SearchCoordinator扩词、主动搜索、多来源采集、官方解析、整组相关判断、排名、原子发布。未连接或自动 AI 关闭时使用公共 GitHub 路径并明确状态。
3. 界面切页、打开已缓存详情或关注文件夹不触发批量检索。项目 AI 解释为手动操作，自动候选核实不会替所有候选生成六卡详情。
4. 入选项目后台预译；内容不变复用硬盘译文。详情需要的译文准备好后呈现，避免结束动画后替换文字。旧画面即时移除，新真实 DOM 约 350ms 渐显。
5. 软件升级与“立即更新”数据刷新分开。匿名启动检测、六小时节流；发现匹配的新安装包和哈希后才提供软件更新入口。

## 3. 两个榜单与任务规则

1. 多来源先收集：GitHub 分页及分区搜索、Trending、Trendshift、AI 主动联网搜索与持久候选库合并，按官方仓库 ID 去重，保留来源。先复用官方搜索页已有元数据，再解析其他来源的缺失身份；不能承诺全站穷尽。
2. 增长榜不让 AI 逐仓库判断。官方 Star 历史经过原有周边界、完整 UTC 日校验，只使用目标日有效正日增；按日增、总 Star、ID 排序。历史前五回归和五个新发现名额保持。
3. 关键词保留原词及最多六个相关扩展。候选合并完成后，将符合归档/Star/历史资格的整组资料一次交给 AI 判断相关、不相关、不确定，再由程序按官方累计 Star 排名。当天其他榜的占位留到前序榜发表后检查，不能提前丢失旧榜释放的候选。
4. 整组输入最多10000个真实ID、1MiB，摘要按候选数压缩；每轮最多准备200份 README，其余仍携带名称、简介、主题、语言和官方数字，资料不足可判不确定。200不再是增长或关键词逐个AI判断上限。输入超限、模型上下文不足、遗漏/重复/虚构返回ID均保留旧结果并说明原因，不偷偷删候选或用模型数字。
5. 默认每关键词每天最多一次整组分析。有效语义缓存复用；扩词、主动搜索另计调用。反复更新不会洗掉已预留调用，失败不自动重试；手动继续可明确重新分析整组，并继续准备资料。不能承诺一次调用固定Token或必然更省额度。
6. 各模块按来源分页、最多两个官方GET、资料准备阶段轮流推进，AI仍一次一个调用；发表顺序增长→关键词原顺序保持。共享同轮公开元数据/日增磁盘缓存，下一次刷新失效；数据库短事务、原子配额、401同身份只恢复一次，取消后恢复共享客户端预算并释放租约。
7. 最新时间要求是程序自身处理60秒内，排除外部网络与AI等待。整组AI最多600秒，扩词/搜索最多120秒，模块整体保护1800秒；不能把受理/提交/超时当完成。4000候选合成本机处理5.869秒、峰值工作集43.8MiB；这不是整应用或真实AI性能数据。
8. 来源读取、持久去重库、AI提交、AI完成、缓存、待处理分别显示。单仓库404跳过并说明，不将整个来源误判失效；网络/认证/限流/预算各有原因。部分模块保存不代表整次成功，失败十秒可关闭提示和持久原因保留。原有睡眠补检查、每日三次自动尝试/一小时间隔不变。
9. 软件升级失败可取消并删除仅本次自动下载文件；不删除手动安装包、其他版本缓存、UserData或备份。checking/installing禁止取消，下载中先停止再清理；启动安装器后状态installing短暂保留供网页接收。100%加Failed to fetch不等于已启动安装器。主题、图标、拖动、归类、六卡手动类型解释和离线翻译保持。

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
4. `GET /api/issue` 的 search_progress 含 job_id/section/keyword_id/local_date、status/stage、collected/unique/candidate_pool/cache_hits/newly_checked/checked_completed/pending 和 expansion_calls/search_calls/judgment_calls/catalog_calls、limited/notes。展示本日状态，不把旧任务计数变成今日进度；不因轮询重复生成卡片动画。
5. `GET /api/search/settings` 返回 `{"enabled":true}`，缺省开启。POST 同路径严格接受布尔 `enabled`，短事务保存；关闭成功后取消当前 AI 检索。GET `/api/search/status` 返回本日 `jobs` 数组；POST `/api/search/cancel` 仅接受空对象，202 表示正在停止，不等于任务已退出。未知搜索路径404。自动关闭时数据刷新使用公共路径；显式手动继续仍可使用，模块之间停止返回部分完成及未执行说明。设置读取/保存以页面代次隔离，迟到 GET 不覆盖刚保存的额度选择。
6. 安装下载确认只接受 `{"confirmed":true}`；服务端没有任意 installer URL、安装目录或外部命令参数。software_update 状态 idle/checking/available/up_to_date/error/downloading/ready/cancelling/installing，release 包含 tag/url/notes/installer_url/sha256/size/prerelease；installable 标明是否冻结安装版，downloaded/total 用于进度。

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

1. `SearchProvider.filter_catalog(inputs, keyword, terms, model_id, timeout=600)`：真实ID去重、列格式压缩、临时JSON资料最多1MiB；名称/简介/主题/语言/Star/有限README全组供应。`CodexRunner.run(..., data_file=...)`读取该资料进入只读stdin，禁用本地shell/unified_exec/multi_agent；不是开放模型读取个人文件。
2. 返回严格的`relevant_ids/irrelevant_ids/uncertain_ids`三个整数数组，必须互斥、无重复、无额外ID且覆盖全组；模型只判断语义，不计算Star排名。全部校验后同一事务持久化。失败预留的catalog_calls仍消耗一次自动预算；界面说明手动继续会重新分析并额外消耗额度。
3. `SearchCoordinator.run_steps`在分页/两请求读取/每20份准备资料之后yield，`SearchJobs.refresh`轮转；yield前官方线程全部结束，客户端预算/时钟在resume和close两条路径恢复。`can_publish`等待前序模块结束，发表前按最新占位重排，事务再验证代次/日期/配置，保留原优先级。
4. `SearchRequestCache`仅共享本次刷新公开元数据和Star历史，下一次清除；官方搜索页可直接补充已解析的元数据。SQLite存储而非所有候选常驻字典，网络最多两请求，AI调用不并行。HTTP404单仓库不代表全部来源失效。
5. `POST /api/software-update/cancel`仅空对象、会话/Origin验证；返回202表示已请求。`cancellable`由状态提供：downloading先设取消事件，退出写文件后删除目标；ready/error可安全清理，仅当前自动包和.part，拒绝符号链接或路径逃逸，失败说明原因。checking/installing拒绝，原数据库和备份不动。
6. 下载重试归零；真正启动安装器后设置installing，网页轮询接收后停止旧服务交互。100%加Failed to fetch只说明连接中断，不作为安装器已启动证明；保留后续查询和失败处理。没有增加强制安装或绕过Windows安全提示。
