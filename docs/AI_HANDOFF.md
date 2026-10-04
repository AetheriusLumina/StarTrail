# 产品对接与 AI 开发接续

此文档描述当前源码，帮助新的开发者或 AI 在不依赖旧聊天记录的情况下继续工作。设计与实现必须分开；代码发生变化时同步更新。版本变化集中在 [开发日志](CHANGELOG.md)，环境和打包命令集中在 [开发指南](DEVELOPMENT.md)。

## 1. 当前实际状态

1. 产品为星迹 · StarTrail，运行版本 `0.4.0`，Windows x64 优先。公开安装包为 `v0.4.0-preview.1`；仓库 README 的更新不意味着安装包内容变化。
2. Python 标准库后端、原生网页、SQLite、默认浏览器。没有公网后端、自建账号系统或要求迁移到大型前端框架。
3. 首页包含增长榜和关键词分组，另有历史日历/搜索、关注与文件夹、设置、README 阅读、本地翻译、GitHub 连接及 Codex 分析。
4. **当前 AI 仍是手动触发**。增长与关键词的AI检索规划、关键词自动扩词、自动增量核实、跨日候选判断缓存和最多200个候选的新流程均未实现。不要仅凭需求已经确认，就对用户宣称功能已完成。
5. 开发文档本次更新不修改数据库、排名、界面和安装包。产品改造完成后再更新本节状态。

## 2. 从哪里开始修改

| 职责 | 主要入口 | 相关测试 |
|---|---|---|
| 启动、浏览器、会话 | `github_radar/__main__.py`、`browser_launcher.py`、`browser_server.py` | `tests/test_browser_server.py`；以 tests 目录实际文件为准 |
| 更新与调度 | `daily_update.py`、`service.py`、`update_lock.py`、`windows_scheduler.py` | `tests/test_service.py`、`tests/test_wider_refresh.py`、调度与锁测试 |
| 候选来源与网络 | `discovery.py`、`github_client.py`、`trending.py`、`trendshift.py`、`public_http.py` | `tests/test_discovery.py`、`test_github_client.py`、`test_trendshift.py` |
| 筛选、历史排重 | `ranking.py`、`presentation.py` | `tests/test_ranking.py` |
| 持久化与迁移 | `storage.py`、`models.py`、各类 types 模块 | `tests/test_storage.py`、`test_discovery_storage.py` |
| AI 连接、判断、详情 | `codex_connection.py`、`ai_provider.py`、`ai_service.py`、`ai_types.py` | `tests/test_ai_service.py`、AI provider/connection 测试 |
| README、预译与缓存 | `readme_service.py`、`readme_content.py`、`project_translation.py`、`translation_service.py`、`translation_worker.py` | README、project translation、translation 测试 |
| 页面与交互 | `web_assets/app.js`、`app.css`、`history_calendar.js`、`following_board.js`、`card_transition.js` | 前端 Node 检查与 HTTP 回归 |
| 安装、授权与发布 | `github_account.py`、`install_paths.py`、`uninstall.py`、`packaging/`、`.github/workflows/` | 账号、安装、卸载和冻结程序检查 |

表中无包路径的 Python 文件均位于 `github_radar/`。新增职责宜拆成小模块，不对整个大文件做无关重构。

## 3. 运行链路与任务边界

1. `__main__.py` 组装 store、网络 client、业务 service 和 BrowserServer；服务监听 `127.0.0.1` 动态端口，启动默认浏览器。
2. 更新进入 `daily_update.py` / `service.py`，发现候选、读取真实仓库和 Star 数据、筛选排重、保存当天快照。成功后 `ProjectPretranslator` 排队准备入选项目译文。
3. 前端通过本机 API 读取已保存数据，切换分类/历史/关注不自动重新爬取或调用 AI。
4. 当前 `BrowserServer._run_ai_job` 在后台线程中调用 `AIService.refine_keyword` 或 `explain_project`；关键词一次只核实最多20个。当前网络/AI阶段持有跨进程更新锁，扩大批次前必须处理此边界。
5. 已有 `_lock` 为进程内状态锁；`update_lock(data_dir)` 为跨进程写任务锁；SQLite 使用事务。三者职责不同，不能为长耗时任务简单延长 HTTP 请求或无限持锁。
6. 本地翻译单任务处理并缓存到硬盘；项目内容指纹未变化不重译。详情需要的译文准备好后再呈现，避免结束动画后替换文字。
7. 退出停止后台任务及进程；不能把取消后的结果继续写入当天快照。

## 4. 本机 API 对接规则

`browser_server.py` 的 `_handle_get`、`_handle_post` 和各 payload 方法为接口实际依据。下面列出主要路径，不虚构尚未实现的深度检索端点。

1. `/api/` 请求需要本次实例的 `X-Radar-Token`；POST 还验证 `Origin` 与本机来源。凭证由启动会话生成，不能固定写进源码、文档或测试样本。
2. POST 使用 JSON 对象，普通正文最大8192字节，翻译正文最大524288字节；参数还需各业务校验，不能只依赖 JSON 解析。
3. `200` 为成功读取/已处理，`202` 表示后台任务已受理或同一任务已运行，`400` 参数无效，`403` 会话/来源失败，`404` 不存在，`409` 状态冲突，`503` 依赖不可用。具体路由有各自分支，调用方不能把受理等同于完成。
4. 任务通过状态接口继续读取；前端需要超时、错误、取消/退出和实例变化处理，不能无限轮询或频繁重新渲染静态头部。

| 操作 | 当前接口 | 对接要点 |
|---|---|---|
| 存活检查 | `GET /api/health` | 返回 status、instance_id、pid；用于实例识别，不公开到外网 |
| 首页数据/状态 | `GET /api/issue` | 由 `_issue_payload` 组合保存日期、项目与任务状态，不直接改库 |
| 立即更新 | `POST /api/refresh` | 后台更新，同一活动任务复用；普通页面切换不调用此接口 |
| 定时更新 | `GET/POST /api/auto-update`、`POST /api/scheduled-refresh` | 保存时间设置与到期判断，不把手动更新绑到计划时间 |
| GitHub 账号 | `GET /api/github/status`、`POST /api/github/...` | 设备授权/状态；不能返回 Token 到界面日志 |
| Codex 状态/模型 | `GET /api/ai/status`、`POST /api/ai/login`、`POST /api/ai/model` | 就绪状态与可用模型以真实连接为准 |
| 关键词 AI | `POST /api/ai/keywords/{id}/refine` | 当前为手动单批核实；新自动流程尚未替换 |
| 项目 AI 详情 | `POST /api/ai/projects/{id}/explain` | 当前手动生成、缓存并预译；自动关键词核实不等于批量生成详情 |
| 历史 | `GET /api/history`、`GET /api/history/calendar?month=YYYY-MM`、`GET /api/history/{date}` | 查询由 history_search 校验，日历与列表按保存日期读取 |
| 项目 | `GET /api/project/{id}` | 可带 date 或 context，不能同时使用；只读取对应保存上下文 |
| 来源证据 | `GET /api/repository/{id}/sources?date=YYYY-MM-DD` | 分开来源排名、增长排名与统计日期，不混成一个榜单 |
| 关注和文件夹 | `/api/following`、`/api/folders` 及其子路径 | 具体读写/排序/归类参数见 folder handler，重命名不丢关注 |
| README/翻译 | `/api/readme/{id}`、`/api/translation` 及任务子路径 | 缓存、状态和正文分开，不执行 README 脚本或指令 |
| 退出 | `POST /api/quit` | 按退出流程停止服务；默认浏览器标签能否关闭受浏览器规则约束 |

## 5. 数据与缓存边界

1. `RadarStore` 管理 data_dir 内的 `radar.db`，连接与事务集中在 `storage.py`；不要在网页处理器随意写 SQLite。
2. `repositories` 保存真实仓库元数据；`recommendations` 保存某日入选项目和来源/排名上下文。仓库最新 Star 与历史入选日期不是同一个概念。
3. `keywords` 保存词、最低 Star 与启用/删除状态；历史、关注、文件夹和用户偏好属于持久化用户状态，改造检索不能清空这些状态。
4. `ai_verdicts` / `ai_keyword_progress` 当前按日期、关键词和模型记录判断/游标；尚不支持新设计中的跨日内容指纹复用。迁移需要新增结构与回归，不能直接假设已有缓存可跨日使用。
5. `ai_removed_recommendations` 记录已展示后被 AI 排除的项目；`seen_repo_ids` 当前包含 recommendations 与此表。采集过或仅核实过的候选不应自动成为已展示项目。
6. `readme_cache` 保存正文、来源、ETag、内容哈希与截断标识；`translation_cache` 保存译文。缓存键、提取版本、模型与判断规则变化需有明确失效条件。
7. discovery cursor、候选和 source evidence 记录发现进度与来源。来源失败、时间预算到达或分页未覆盖，不能写成全量完成。
8. 升级迁移保留旧数据，迁移设计要说明旧记录如何转入、无指纹记录是否需要补核，以及取消/异常后事务如何回滚。

## 6. 关键产品约束

1. 日增 Star 取最近已结束的完整 UTC 日，保存日期使用本机日期。累计 Star、活动热度、网站上榜和官方日增分别保存，不互相冒充。
2. 历史排重与当天其他分组占位仍有效；本组已有低 Star 不应永久占位阻止更高 Star 的合格候选。此选择缺陷目前待修复。
3. 已复现当天AI新增推荐未同步当天Star快照，详情会取较早的snapshot导致旧日期/旧Star；这是待修复缺陷。需原子保存、按当天/历史上下文读取，并独立验证跨日排重。
4. 当前关键词 AI “完成”指本批次/已有五张已核实卡片，不代表全站搜索完成；新设计要准确区分采集数、去重数、已核实数和剩余数。
5. 浏览器仍使用默认浏览器，保留森林透明主题、固定侧栏、主题滚动条和350ms以内真实 DOM 渐显。只动画变化内容，静态头部不反复重播。
6. GitHub 数据来自真实接口；Trendshift/Trending 是发现与上榜证据，不是官方相关性认证。未上榜项目不能因此被排除。
7. README 和 AI 返回均不可信，必须验证结构、转义显示、限制长度；不能执行网页/README命令，不能让 AI 返回值控制任意 URL、SQL、文件或安装步骤。
8. 默认离线翻译不消耗 Codex 额度；自动关键词 AI 与手动详情 AI 的触发和额度需要明确区分。自动 AI 功能当前仍未交付。


## 7. 当前待办与接续顺序

优先工作见 [增长与关键词检索设计草案](superpowers/specs/2026-10-04-keyword-discovery-design.md)：AI提前规划/扩词、多来源收集、自动增量核实、跨日缓存、重新排名、进度与断点、短时写锁。增长按官方完整UTC日增排序，关键词按累计Star排序；二者不能互换，也不能使用AI生成的数字。

1. 先确认设计和有限覆盖的表达，不许承诺“GitHub全站零遗漏”。每天扫描计划覆盖范围，新候选/内容变化按优先级核实，不设置任意20个日常截止；单次新增核实最多200个。
2. 根据设计补实施步骤和失败回归，按职责逐项实现。首先验证更高 Star 相关候选能替换低 Star 旧卡，随后验证扩词、合并和缓存。
3. 覆盖候选充足/不足、来源失败、超时/限流、重复更新、跨日内容变化、取消/退出、更新与 AI 并发、模型变化、关键词停用及迁移恢复。
4. 完成后跑完整回归、公开文件扫描、Git差异、隔离界面检查；实际调用付费 AI 的次数和范围单独说明，不能为了测试无控制消耗额度。
5. 同步 CHANGELOG、此文档、开发指南、用户手册及双语 README；核对待办与实际源码一致，再提交/推送。
6. 本次完整交付需要构建新安装包并同步手册；安装包若尚未重新构建，明确旧包仍是旧功能。发布需实际新标签、对应源码和哈希校验。
7. 关键算法/约束附原因注释；无用包、临时产物和旧验收资料整理到既有清理入口，由用户删除/卸载。保留正式数据和活动源码，不能移动已安装目录而破坏卸载。

## 8. 下一位 AI 的工作核对表

1. 阅读四个入口文档，核对分支/状态/源码并找对应测试。
2. 区分已实现、设计待实现、已知缺陷与未实测事项，先复现后修复。
3. 保持现有行为与用户数据，给影响行为的变化补测试。
4. 检查长任务锁、取消状态、缓存失效和权限/文本边界。
5. 新接口写明方法、路径、请求/响应、错误码、幂等、持久化和前端调用位置；更改旧接口一起更新调用方与回归。
6. 用实际执行证据更新统一日志，不引用未经核对的旧“通过”结论。
7. 提交前检查文档链接、公共隐私和暂存区；只公开源码与安全文档，不带私人原始材料。
