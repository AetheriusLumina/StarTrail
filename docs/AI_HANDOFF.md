# 产品对接与 AI 开发接续

此文档以当前源码为准；工程变化集中在 [开发日志](CHANGELOG.md)，环境和打包见 [开发指南](DEVELOPMENT.md)，用户操作见 [用户手册](USER_GUIDE.md)。接续时先读根目录 AGENTS.md，再核对 Git 状态和实际代码。

## 1. 当前状态与交付门槛

1. 星迹 · StarTrail 采用 Python 标准库后端、原生网页、SQLite 和默认浏览器，Windows x64 优先。内部包名、EXE、AppId、任务和数据标识继续兼容 GitHub Radar。
2. 自动多来源检索、AI 扩词及主动联网搜索、持久化判断、归类/拖动、项目类型首行高亮、软件更新检测/验证/安装器备份已接入源码。中英文 README 分开，英文功能图使用英文实机图。
3. 运行版本 0.5.0，当前交付标签 v0.5.0-preview.3。同标签源码的本机构建已隔离覆盖升级验收；手动Windows流程测试/构建/校验/发布完成，最终交付采用实际公开下载并验证的云端安装包。云包未安装到正式软件，构建摘要和验收范围分别记录于开发日志。
4. 完整回归在统一日志记录；本次最终回归为 562 项，隔离检查通过，包含 Node 更新交互。独立复核发现的九类边界问题均补回归修复；额外补扩词失败公共路径。真实 AI 三次验收通过，观察到五次实际搜索事件。新冻结资源一致、双向离线翻译、本机 API 和退出通过；隔离中文路径真实安装、覆盖升级、完整备份、数据保留、快捷方式 ICO、备份拒绝停止覆盖通过。
5. 正式用户数据未用于开发测试；真实 AI 验证严格三次，未执行自动二百候选验收。安装验收仅更换测试 AppId/注册键/快捷方式名，程序和备份逻辑未变，避免覆盖真实安装；第二台干净 Windows 和跨夜长期运行仍未验收。

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
2. 自动 AI 开关开启且已连接 Codex 时，SearchJobs 串行处理增长及启用关键词；SearchCoordinator 扩词、主动搜索、多来源采集、官方解析、分批判断、排名、原子发布。未连接或自动 AI 关闭时使用公共 GitHub 路径并明确状态。
3. 界面切页、打开已缓存详情或关注文件夹不触发批量检索。项目 AI 解释为手动操作，自动候选核实不会替所有候选生成六卡详情。
4. 入选项目后台预译；内容不变复用硬盘译文。详情需要的译文准备好后呈现，避免结束动画后替换文字。旧画面即时移除，新真实 DOM 约 350ms 渐显。
5. 软件升级与“立即更新”数据刷新分开。匿名启动检测、六小时节流；发现匹配的新安装包和哈希后才提供软件更新入口。

## 3. 两个榜单的规则

1. 增长使用最近已结束的完整 UTC 日。北京时间 08:00 前，最近完整 UTC 日并不等于本机日历昨天。仅官方可核实的正日增参与增长排名；按日增、累计 Star、仓库 ID 排序。历史回归前五紧凑展示，不占五个新发现名额。
2. 关键词保留用户原词，缓存最多六个相关表达及对应主题；AI 实际联网检索与 GitHub 搜索、Trending、Trendshift、跟踪候选合并。Trendshift 是发现来源，未上榜不是排除条件。
3. 身份、归档状态和累计 Star 以 GitHub 解析结果为准。关键词必须相关且达到门槛，再按官方累计 Star 排序；历史已展示和当天其他模块占位排除。只搜索/核实过而没展示的项目不算历史展示。
4. 每个模块、每日本地日期最多新增核实 200 个，20 个一批，调用前预留预算，失败或重复点击不重置。有效缓存不消耗新核实数；手动继续明确增加最多 200 个。自动主动搜索最多两次，第二次只为明确缺口；扩词和主动搜索与判断的调用次数分别记录。
5. 每天重新采集当前计划覆盖范围，旧前沿必须刷新官方观察才可作为当天结果。语义内容未变可复用判断；增长证据按统计日更新。有限 API、网站和时间预算无法保证全站零遗漏。
6. 限流、超时、取消、未解析候选和断点显示为部分完成或暂停，不写成全量完成。无法确认新结果时保留上次已保存结果及其真实日期。

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
4. `GET /api/issue` 的 search_progress 含 job_id/section/keyword_id/local_date、status/stage、collected/unique/cache_hits/newly_checked/pending 和 expansion_calls/search_calls/judgment_calls、limited/notes。展示本日状态，不把旧任务计数变成今日进度；不因轮询重复生成卡片动画。
5. `GET /api/search/settings` 返回 `{"enabled":true}`，缺省开启。POST 同路径严格接受布尔 `enabled`，短事务保存；关闭成功后取消当前 AI 检索。GET `/api/search/status` 返回本日 `jobs` 数组；POST `/api/search/cancel` 仅接受空对象，202 表示正在停止，不等于任务已退出。未知搜索路径404。自动关闭时数据刷新使用公共路径；显式手动继续仍可使用，模块之间停止返回部分完成及未执行说明。设置读取/保存以页面代次隔离，迟到 GET 不覆盖刚保存的额度选择。
6. 安装下载确认只接受 `{"confirmed":true}`；服务端没有任意 installer URL、安装目录或外部命令参数。software_update 状态 idle/checking/available/up_to_date/error/downloading/ready，release 包含 tag/url/notes/installer_url/sha256/size/prerelease；installable 标明是否冻结安装版，downloaded/total 用于进度。

## 5. 存储、失效和并发

1. RadarStore 管理 radar.db。repositories 是最新元数据，star_snapshots 是带真实 observed_at 的日期快照，recommendations 是当天入选上下文。displayed_repositories 保留实际展示历史；删除/替换当天卡片不擦掉已经展示的历史。
2. SearchScope 包含模块/关键词、本地日、统计日、原词、门槛、模型、规则和扩词哈希。query_expansions、search_candidates、search_readmes、search_judgments、search_runs、search_cursors 管理扩词、观察、README、判断、租约/预算和来源断点。
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
