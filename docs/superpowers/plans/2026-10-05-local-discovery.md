# 本机增量检索实施计划

> **For agentic workers:** 使用 superpowers:executing-plans 在当前会话逐项实施，保护已有未提交计时代码，不额外开启实现子代理。

**Goal:** 取消更新中的AI审核，保留多来源发现和原数字排名，持久复用证据并真实测量整份更新。
**Architecture:** Python标准库/原生网页/SQLite保持。SearchCoordinator共享计时和租约，新增本机无审核路径；SearchStore保存查询证据/日增并提供整份事务发布。官方连接有界复用；SearchJobs集中排重、提交、计时。
**Tech Stack:** Windows x64、Python 3.13、SQLite、原生JavaScript、现有Codex CLI。
**Spec:** ../specs/2026-10-04-keyword-discovery-design.md

## Global Constraints
完全本机；取消榜单AI审核；实际时间包含AI；不减少候选假装提速；不写正式数据库或运行安装器；保留其他功能与数据兼容；公开文件无凭证/私有路径；GitHub发布须在验证后。

## Review Focus
1. 日增为零、周边界异常：零有效，异常不能当零。
2. 改名、旧配置/跨日：稳定身份及事务验证，不用旧任务覆盖。
3. AI失败/引用另一个仓库：不伪造检索成功或数字。
4. 多榜占位与取消/磁盘失败：同一事务回滚全部结果。
5. 冷启动、睡眠、额度/代理：保留断点，如实记录实际时间与覆盖。

### Task 1: 无审核检索路径
Files: github_radar/search_local.py、search_coordinator.py、search_sources.py、search_storage.py、tests/test_search_local.py。
Interfaces: run_steps(scope, defer_publish=False, ...)、save_match(scope,repo,term,kind,observed_at)、matching_verdict(scope,observation,terms)。
- [x] 测试调用任何审核即失败；25/250候选仍按总Star排序；AI时间包含关键词扩词，README不批量读取。
- [x] 观察RED，实现源查询依据/本地明确匹配及无审核路径，观察GREEN。
- [x] 保留旧审核协议兼容单测；产品默认不进入旧审核路径。

### Task 2: 持久证据与来源回扫
Files: search_storage.py、search_sources.py、tests/test_search_local.py。
Interfaces: load_star_day(repo_id,stat_date,now)、save_star_day(repo_id,day,now)、import_candidates(scope)、catalog分页。
- [x] 测试跨刷新复用日增（含零）、6小时过期、跨统计日不复用、老仓库不只限30天。
- [x] RED→GREEN；候选分页导入不截断500；元数据仍保留真实观测时间。

### Task 3: 两榜完整原子提交及整次时间
Files: search_jobs.py、search_storage.py、service.py、tests/test_search_local.py。
Interfaces: publish_all(values,leases,now,cancel_event,refresh_lease)、deferred publication/rerank、RefreshResult timing。
- [x] 先失败回归：第二模块失败/改配置/磁盘触发器时第一模块和历史都不改变。
- [x] 实现排重顺序及同一事务；失败显示旧完整结果；记录一条整次墙钟时间。
- [x] 兼容手动单组继续及已有调度回归。

### Task 4: 有界连接复用
Files: github_radar/github_transport.py、github_client.py、tests/test_github_transport.py。
Interfaces: PooledHTTPSOpener(request,timeout)、close()；代理配置使用现有urllib回退。
- [x] RED→GREEN：连接复用、未读完关闭、错误/重定向同源、超时与最多四连接。
- [x] 保持401恢复、预算及注入opener接口。

### Task 5: 用户进度与文档
Files: web_assets/app.js、i18n.js、browser_server.py、README.md、README.en.md、docs/{USER_GUIDE,DEVELOPMENT,AI_HANDOFF,CHANGELOG}.md。
- [x] 删除自动审核宣传，匹配不标AI审核；进度展示实际总/AI/最慢阶段及零审核计数。
- [x] 保留手动项目解释；源码行为、双语说明和统一日志一致。

### Task 6: 验收和发布
- [x] 相关测试、完整unittest、Node交互、公开文件扫描、diff检查。
- [ ] 隔离实际外网/AI计时和内存，记录范围/失败，不夸称两分钟。
- [ ] 自查/独立复核，先本地提交再GitHub；构建新安装包，下载公开资产验证SHA；不运行安装器。
- [ ] 统一交付入口与旧包归档，由用户自行安装/清理。

## 执行记录
Ruling: 维护者明确要求实施刚才方案、取消AI审核并授权调整其余逻辑；沿用当前会话原地实施，不重复要求选择技术执行方式。原有未提交计时改动纳入本次验证。当前main为维护者指定本地公开副本，已有未提交工作不能被新worktree遗漏。
状态：Task1–5实现及隔离回归已保存；最新要求改为固定来源直接读、增长零AI、关键词仅扩词、官方最多4连接。完整669项回归96.497秒通过（1项环境跳过）；Node交互通过，公开扫描208文件0标记，diff --check通过。固定公共页面实际读取8.632秒、零AI；Trendshift主题只读到首批26项目，后续覆盖尚未核实，不能据此宣称整次完成。整次有效登录计时／真实两分钟目标、安装包和GitHub未完成。

## 2026-10-07：来源状态与日常发现修正（待发布）

1. 增长榜前台沿用最近 30 天的新仓库发现窗口，同时完整刷新已有候选与趋势来源；旧仓库不会因创建时间早而从已知候选库删除。全部年代目录回扫使用独立断点，在电脑清醒且空闲时进行，不与每日前台搜索共用游标。
2. Trendshift 主题页只解析主题数据，排除侧栏无关项目。公开首批内容与后续未覆盖内容分开记录；来源不可用与有效但有限的公开范围分别显示。补充来源的缺口不会被当作官方数字核算失败；官方搜索或已知候选所需事实不完整时，整轮仍保留旧榜单。
3. 同一轮各榜共享公开页面结果，下一轮重新读取；官方连接上限为 4，关键词扩词最多 2 个并行。增长榜不调用 AI，关键词只做扩词，不逐仓库审核。
4. 软件退出或启动安装程序前停止并等待空闲资料准备，避免安装备份与后台数据库写入重叠。
5. 有效 GitHub 登录的隔离样本：四连接读取 20 个官方日增证据耗时 2.397 秒；增长搜索结果总数 441,723，但该次只返回 100 条。样本总用时 10.934 秒，不代表完整目录或整轮两分钟验收通过。正式数据未修改，安装器未运行。

尚待完整外网整轮计时、独立复核、新安装包与 GitHub 发布。不能将有限补充来源的成功读取说成全站完整覆盖。

### 2026-10-07 实测与复核 checkpoint

1. 稳定身份恢复涵盖名称404、名称被新仓库占用、当日同名缓存；逐个缺失的 GraphQL NOT_FOUND alias 显式 null 交给稳定ID恢复，其余批量权限／不完整错误仍阻止整份发布。
2. 官方元数据每批20个，最多四批并行；只在主线程写库。同轮公开页面共享，下一轮重新请求。跨日只前置尚未覆盖的新日期区间，不把2007开始的全部范围反复插到未完成断点之前。
3. 官方核算待处理数包括没有有效日增证据的候选，不再在失败时显示零。已核算仅计有效日期证据；失败原因累计保留，不被后一个请求覆盖。
4. 独立复核发现的稳定身份问题已观察失败回归后修复；游标持久推进问题也以新增日期区间修复。完整723项回归103.881秒通过（1项环境跳过），最后游标修订后的111项相关回归通过，失败计数修订后的78项相关回归通过；Node交互通过。新安装包验收尚未开始。
5. 有效登录、公开元数据隔离副本的整轮实测：第一轮241.753秒保护取消；四批优化后240.126秒仍未完成；较长续做599.824秒仍失败，阶段收集115.227秒、日增核算465.628秒、排序0.017秒、保存准备0.009秒，AI0秒（复用已有扩词）。没有保存新榜单；没有运行安装器或写正式数据。测得该次隔离Python进程峰值51.88MiB。真实两分钟完整更新未通过。
6. 隔离候选增长库4555个，已缓存3549个日增证据；排除已确认不可用的仓库后仍有812个没有证据。源站错误、官方配额与网络等待不得当作完整覆盖；不能只拿首页或把昨日未验证数据改为今天来交付。
7. 本地源码与交接记录已保存；GitHub仍为preview.5。新安装包、GitHub更新、完整实测成功及两分钟目标待完成，不把上述测试通过等同于实网验收通过。

最终本地源码回归：725项／105.372秒通过，环境跳过1项；Node交互、公开208文件0标记、diff检查通过。实际整轮更新与两分钟目标仍失败，未发布新安装包。
