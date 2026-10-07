# 开发与维护

先阅读根 README。保留 Python 后端、原生网页、SQLite 和默认浏览器，不要求迁移框架。当前检索、缓存、任务与升级接口见产品对接文档；发布状态以开发日志和实际 Release 为准。

## 文档入口与每次更新记录

1. [开发日志](CHANGELOG.md)：连续记录每次更新的问题、行为、模块、核验、限制和版本状态。
2. [产品对接与 AI 接续](AI_HANDOFF.md)：当前实际状态、模块、API、数据/缓存、并发边界、待办与接续步骤。
3. [仓库 AI 工作规则](../AGENTS.md)：要求每次交付同步维护日志和接续文档，不用旧聊天记录代替工程文档。
4. 本指南集中维护环境、源码运行、打包与贡献方法；用户操作集中在用户手册。私人原始验收附件留在仓库外，公共文档仅记录脱敏工程结论。

## 开发环境

Windows 10/11 x64、Python 3.13 x64、Node.js 22+。Python 虚拟环境和模型均在仓库内的忽略目录。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements/constraints.txt -e ".[translation,build]"
.\.venv\Scripts\python scripts/prepare_models.py
.\.venv\Scripts\python -m github_radar --data-dir .build/DevelopmentData
```

模型版本/哈希在 `packaging/translation-manifest.json`。下载校验失败应核对来源和版本，不跳过校验。断网开发可以复用已下载的两个 `.argosmodel` 压缩包：

```powershell
.\.venv\Scripts\python scripts/prepare_models.py --archive-dir .tools/downloads --offline
```

自选已准备的模型目录通过 `STARTRAIL_MODEL_DIR` 指定；它包含 `en-zh/`、`zh-en/` 和 `manifest.json`。冻结安装版使用内部模型，忽略开发环境覆盖。GitHub 登录可选，公开 Client ID 不是密钥；Fork 发布者可以用 `GITHUB_RADAR_OAUTH_CLIENT_ID` 配置自己的设备流程应用。绝不能分发 Client Secret 或个人 Token。

## 修改位置

| 工作 | 主要模块 |
|---|---|
| 候选来源、GitHub 请求 | `search_sources.py`、`github_client.py`、`trending.py`、`trendshift.py`；`discovery.py` 为兼容路径 |
| 榜单与更新规则 | `search_jobs.py`、`search_local.py`、`search_coordinator.py`、`search_ranking.py`；`service.py` / `ranking.py` 为兼容路径 |
| 数据、历史、关注分类 | `storage.py`、`history_search.py`、`follow_folders.py` |
| README 和翻译 | `readme_service.py`、`project_translation.py`、`translation_service.py`、`translation_worker.py` |
| 登录与 AI | `github_account.py`、`codex_connection.py`、`ai_service.py` |
| 本机 API 与启动 | `browser_server.py`、`browser_launcher.py`、`__main__.py` |
| 页面与动效 | `web_assets/`；`app.js` 入口，其他交互按职责分模块 |
| Windows 任务、安装与卸载 | `windows_scheduler.py`、`uninstall.py`、`packaging/` |

## 运行边界与数据流

应用以 `__main__.py` 启动，`browser_launcher.py` 打开默认浏览器，`browser_server.py` 在回环地址提供页面和 API。静态界面用原生 HTML/CSS/JavaScript；个人状态进入 `storage.py` 管理的 SQLite 与配置。服务层协调业务，来源层处理 GitHub/公开页面请求，展示层组合可阅读的数据。没有公网后台或项目自建账号服务。

当前自动AI发现连接路径：`search_jobs.py` → `search_coordinator.py` / `search_local.py` → `search_sources.py` 多来源发现与 `github_client.py` 官方事实 → `search_ranking.py` 排序 → `storage.py` 两榜原子保存 → `project_translation.py` 入选项目预译。未连接或关闭自动AI的兼容路径为 `daily_update.py` / `service.py` → `discovery.py` → `ranking.py`。来源失败与预算停止需要保存可恢复状态，不能把未核实数字当成有效增长。手动与定时更新共享锁；并发点击复用活动任务。

阅读已保存的分类、日期或关注项目不等于再次更新。README 由 `readme_service.py` 获取并缓存；翻译由 `translation_service.py` 管理缓存与单任务进程，`translation_worker.py` 负责 CPU 推理。详情准备需要的译文后再展示，避免结束动效后替换文字。已连接 Codex 的数据更新通过 `search_jobs.py` / `search_coordinator.py` 关键词扩词、直接公开检索和依据匹配；项目六卡解释仍由用户手动触发。结构化结果保存后再进行本地翻译。

前端入口是 `web_assets/app.js`；历史日历、关注文件夹、翻译和真实 DOM 动效分模块。调整布局时复用主题变量，避免给单页重复硬编码尺寸。标题与排名的艺术字体不扩展到所有正文；固定列数与字号放大依赖滚动，不通过加宽侧栏解决。

初次参与开发时，可以先选择一个已有模块和对应测试阅读，不需要先重写整个应用。修改排序要一起读 `ranking.py` 与更新协调；修改缓存要检查内容指纹、失败重试和并发边界；修改外部资料渲染要检查文本转义及链接安全。

## 不能改错的规则

- 增长取最近已结束的完整 **UTC** 统计日；北京时间当天 08:00 才结束昨天 UTC 日。手动更新优先，自动任务关闭时仍可用；并发点击复用任务。
- 历史重复前五项目紧凑显示，不占新增五个名额；不足五个时如实显示，不假造项目。关键词根据当前真实总 Star 排序、历史排重，不机械展示固定下一段名次。
- 本地预译只处理已入选项目，单任务、分批、硬盘缓存；内容不变不重译，不自动生成 AI，不翻译全部候选。代码、链接及仓库标识保持原样。
- 详情六卡为三列两行、等大、内部滚动；历史与关注五列紧凑卡。日期进入独立当天页，搜索禁用/淡出日历。文件夹重命名/删除保留关注；未分类和自建文件夹可拖动，全部关注禁止；详情多分类/创建并归类原子保存。
- 标题和排名共享艺术字体；森林透明主题、固定侧栏、透明菜单和主题滚动条保持。真实 DOM 渐显最多约 350ms，旧页即时消失；静态关注头部不反复重播。
- 服务只监听回环地址，所有可变操作核对会话凭证/来源；README 按不可信文本处理，不执行其脚本或命令。自动检索与手动解释的额度分别说明，普通语言解释术语，用途首行高亮项目类型。

## 自动检查

```powershell
$env:RADAR_TEST_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
.\.venv\Scripts\python -m unittest discover -s tests
node tests/software_update_ui.cjs
.\.venv\Scripts\python scripts/check_public.py
.\.venv\Scripts\python scripts/check_public.py --tracked
git diff --check
```

`check_public.py` 检查强制加入 Git 的数据/日志、常见凭证模式、固定电脑地址和个人邮箱，但不能识别所有泄露；提交前仍逐项审查 `git diff --cached`。不要把真实 HTTP/AI/用户数据作为公开测试样本。

## Windows 安装包

安装 Inno Setup 6（官方来源），然后：

```powershell
.\.venv\Scripts\python -m pip install -c requirements/constraints.txt -e ".[translation,build]"
.\.venv\Scripts\python scripts/prepare_models.py
pwsh -File packaging/build.ps1
```

编译器不在常见安装位置时，用 `-IsccPath` 指定实际 `ISCC.exe`。`-SkipInstaller` 只生成独立运行目录。结果在 `.build/StarTrail_Setup.exe`；临时编译和验收也在 `.build`，不要提交。

为兼容旧用户，内部 Python 包名、`GitHubRadar.exe`、安装 AppId、默认数据位置和 Windows 任务标识继续使用旧名；显示名与安装包名为 StarTrail。不要仅为改名修改这些身份；否则可能重复安装或丢失旧数据入口。

许可证和第三方说明随冻结程序进入 `AppFiles/licenses/`。发布前在独立测试目录验收启动、离线翻译、更新、升级保留数据、卸载保留/删除隔离。CI 打包是可复现验证，不代替第二台电脑/干净 Windows 安装测试。首次公开版为未签名预览，不能声称已获得代码签名或 SmartScreen 信任。

## 手动云端构建与发布

`check.yml` 在普通提交/PR 执行，只读权限。`build.yml` 只允许 `workflow_dispatch`；构建任务具有 `contents: write`，没有推送或 PR 发布入口。

发布步骤：

1. 确认版本、隐私检查、许可证、用户手册及变更范围，创建指向待发布源码的标签，并准备对应 Release 草稿。
2. 在 Actions 的 Windows installer 工作流手动输入已有标签；空标签只保留 artifact，不公开发布。
3. 工作流检出指定标签，用 Python 3.13/Node.js 22 创建独立环境，运行公开文件检查和完整回归，再准备模型并打包。
4. 上传安装包、用户手册及 SHA256，匹配服务端安装包摘要后将该标签的 Release 公开为预览版。验证失败时停止，检查日志后处理，不把失败改成成功。
5. 下载公开安装包，再检查 SHA256，进行独立安装环境验收。构建成功不等于真实授权和长期运行全部验收。

草稿信息通过认证后的 Release 列表读取；GitHub 的按标签查询可能对草稿返回 404。流程只更新已存在的标签/Release，不创建或重写标签，不发布未测试的其他分支。需要更改发布权限或自动触发条件时另行讨论。

README 截图集中在 `docs/images/`，展示实际使用时保存的项目与关注分类。首图已更新当前名称和图标，其他三张保留原图，数据以截图时的保存结果为准。新增截图优先使用隔离环境。发布前逐张检查凭证、登录信息、本机地址和其他未获授权的个人内容；不要未经允许复制真实数据库或个人验收附件。日常验收日志放仓库外或忽略目录。

## 内存与性能

已有按需加载、CPU int8、一次一个翻译子进程、有限任务队列和译文硬盘缓存。区分安装包大小、Python 后端、翻译子进程和浏览器内存，不用下载大小冒充内存占用。

建议分别记录空闲、更新、预译峰值、重复打开详情、长时间空闲；模型准备/下载不计入运行占用。优先复用缓存和释放临时任务，再考虑改变线程/模型；换语言或框架不能保证降低模型内存。每次优化附同一输入的前后对比和质量回归。

0.4.0 本机短句基线（工作集，MiB）：纯 Python 16.5、加载翻译运行库 30.2、单方向译完 135.2（峰值 137.1），同一进程双方向 227.9（峰值 229.4）。仅测翻译进程，不含后台/浏览器，不能作为整产品内存承诺。实际后台一次一任务，译文持久化后工作子进程退出释放模型；大段 README 应另测峰值。

0.5.0 有界测量：同一进程以每页100条存取1000个合成候选，工作集30.36→31.83 MiB；新冻结程序短句中/英两个独立翻译进程峰值139.59/139.96 MiB，完成后均退出。仅测该合成输入与短句，不含浏览器、整轮网络/AI或长 README，不能当整产品上限，也不是与旧版同输入的性能提升证明。

## 贡献与维护约定

1. 先在 Issue 说明问题或设计；修复附复现方法，功能变化先讨论。
2. 从 main 创建短分支；改动按职责，避免全仓格式化、整体重写及顺手改界面。
3. 清楚命名；注释解释约束、原因和安全边界，不逐句复述代码。异常要保留已有数据，并显示可理解反馈。
4. 给有影响的行为补回归；界面检查键盘、字号、减少动效。测试不要依赖私人凭证、付费 AI 或实时网络成功。
5. PR 写明问题、行为变化、验证和限制。提交前审查所有新增文件和提交邮箱。

阶段结果集中维护本指南与发布说明；临时日志、截图和旧包保存在仓库外或 `.build`，避免不断新增验收报告。后续重点：干净 Windows/不同电脑、有效真实授权跨夜续期、性能基线、多模块渐进拆分；尚未测的事项不要写成已经完成。

## 新检索和升级维护入口

1. SearchJobs 共享立即更新、定时和 CLI 路径；SearchCoordinator 管理关键词扩词、直接搜索、官方解析、匹配和发布。SearchStore 保留租约、每日预算、断点和跨日语义缓存。修改时先读 AI_HANDOFF 的契约及对应 tests/test_search_*。
2. 内容指纹不包含累计 Star；增长证据包含统计日。预算在调用前预留，异常不洗掉额度。仅实际展示进入历史排重；发布必须重新验证日期、代次及当前配置，不能让旧任务覆盖新模型/关键词。
3. 全量自动回归不用真实账号和网络。真实 AI 验收需要单列调用范围及限额，禁用自动大批量任务；CLI 的真实 web_search 事件才是联网证据，输出提到“搜索”不算。
4. software_update.py 只读取固定仓库 Releases；UI 为十秒消息及确认下载。Inno 同时处理手动安装和应用内升级，维护锁后完整复制数据，备份失败停止覆盖。保持内部身份，更新源码版本/安装器版本/RELEASE_TAG 必须一起核对。
5. 中文图片在 docs/images/，英文图片在 docs/images/en/。README 描述当前功能，提交记录和 CHANGELOG 记录变化；不要在正文重复逐版更新日志。
6. 发布门槛包括新冻结目录、中文带空格路径、覆盖升级、数据库和备份一致、快捷方式图标、安装包校验及实际运行。仅编译安装器脚本不能冒充升级验收；第二台干净 Windows 没测就保留限制。

## 本机直接检索与有界并发

1. search_local.py 是默认无审核路径：增长榜不调用AI；关键词只用AI扩词，再由 SearchSources 直接读 GitHub分页／分区搜索、Trending、Trendshift公开页面和本机候选库。旧 AI搜索／审核协议仅供兼容测试，不删除已有数据或手动六卡解释。
2. 原词和扩展词的检索依据保存在 search_matches，内容指纹不包含Star；无可信匹配为 uncertain。关键词按官方总Star排序，增长按验证过的完整UTC日新增、总Star、稳定ID排序。保持原阈值、归档、历史排重、榜单优先级和回归／新发现展示。
3. 官方元数据每批最多二十，正式启动使用最多100个共享HTTPS连接，遇到限流减半降低后续日增请求并发，关键词扩词最多两个调用并行；服务器配额仍控制准入，不能仅增加线程绕过限流。页面、数据库按批读取，进度最多每秒保存一次。
4. 同轮REST请求共享磁盘缓存；统计日／规则／ID对应日增含零缓存六小时，旧元数据按稳定ID刷新，404当日跳过次日可重查。候选分页不能截断五百或只留第一页。
5. SearchJobs轮流推进所有榜单，全部就绪后增长→关键词统一排重，以一个短事务保存。任何必需来源、官方统计、配置／租约或磁盘失败均保留整份旧榜及原因，不把部分或后台补齐当成功。
6. 醒着空闲时 prepare只采集公开事实，不用AI、不写推荐历史；前台更新／详情读取优先，取消并等待其安全退出。睡眠恢复到期检查仍遵守自动任务间隔和尝试次数。
7. SearchJobs记录整次墙钟，包含网络、AI、轮转等待、保存及失败。累计AI调用时间可能因并行大于墙钟，两者分别显示。前台默认budget_seconds=None，不设整轮分钟数上限；后台prepare仍显式传有限预算。真实耗时包含AI、网络与服务器配额等待；必须给出真实规模、认证、成功状态、内存与阶段耗时，不再把小候选测试当正式用户规模达标。
8. 真实测试正式数据库只读，隔离数据验收，不运行安装器。覆盖升级、冻结程序、公开安装包哈希、另一台干净Windows和真实睡眠分别说明实际覆盖。测试中凭证仅驻内存，不把账号“connected”当作token仍有效的证明。
9. 软件升级、失败下载清理、森林主题和图标、拖动、归类、六卡类型首行、离线翻译保持。对应回归见 test_search_local.py、test_github_transport.py、test_search_timing.py；接口细节见 AI_HANDOFF.md。


来源覆盖状态：公开补充页记录 complete／partial／failed 与本次项目数。partial 表示页面有效但未覆盖后续目录，failed 表示该来源暂不可用；两者会在界面显示。它们不替代官方事实；默认路径可按已核实额度范围发表，未知候选不参与并保留断点。前台增长的新仓库发现沿用 30 天窗口，旧候选仍更新和排名；全年代目录准备使用独立断点。同轮共享公开页面，下一轮重新请求。


## 配额边界、兼容等待与本机留存

1. 新来源候选和旧元数据均使用GraphQL二十仓库批量读取，最多四个批请求并行；稳定ID核对、来源证据与当日观测仍保存。没有有效授权时保留兼容REST路径，不能冒称批量读取成功。
2. 日增波次必须全部收取并保存结果，之后才能yield或等待配额；跨模块共享客户端不能带着未收取future切换预算。限流行保留到retry_rows，不用“已失效”跳过403/429。
3. 默认无审核路径到配额预留线停止本轮请求，不等待下一小时；继续读同日有效日增缓存，按已核实范围准备发表。`_wait_core_steps`仅旧审核兼容路径保留：先等retry_not_before再读/rate_limit，取消有效。网络失败独立记录，不能被quota_limited掩盖。
4. SearchJobs全局refresh与所有活动／已准备模块租约一起续期；continue_keyword也有心跳。取消、配置或日期变化的旧任务不得写新榜单。单次网络超时保留，取消整轮分钟截断不等于请求永久阻塞。
5. 候选、来源、查询断点、关键词扩词、当日资格与已发布历史保留在SQLite；日增数含零保存最近30天，同一日有效期六小时。未完成证据可复用，新统计日仍需取得新官方事实，累计Star差值不能代替新增。

## 单轮范围回归

配额截止、网络与配额混合、有效零日增、剩余缓存、GraphQL混合错误与恢复、来源取消留存、查询轮转均使用隔离确定性测试。产品动态英文状态同时运行 `python -m unittest tests.test_status_i18n` 和 `node tests/status_i18n.cjs`；浏览器交互仍运行 `node tests/browser_interaction.cjs`。单轮成功只代表已验证额度范围，不以旧版本缓存测试代替新版本首次实测。


## 本地文件整理与交付

1. Git跟踪目录是唯一活动源码；`.build/`是可重建构建和隔离验收，`.venv/`和`.tools/`是开发环境与离线模型。清理产物前确认没有对应冻结进程在运行，并保留需要的原始验收证据和公开安装包校验记录。
2. 安装目录、UserData与备份不作为开发缓存清理。注册安装不得通过搬文件夹卸载；使用对应卸载器，缺失时记录并由用户决定处理。
3. 本机清理清单、历史私有源码、认证资料、数据库和原始日志不提交Git；只同步当前源码、授权图片及公共用户／开发／交接文档。
4. 文档修订不改变现有发布标签或安装包。发行附件手册是发布时快照，仓库用户手册是当前勘误版本；程序代码未改时无需伪造新版包。
