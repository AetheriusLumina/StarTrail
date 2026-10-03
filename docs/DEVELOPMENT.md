# 开发与维护

先阅读根 README。保留 Python 后端、原生网页、SQLite 和默认浏览器，不要求迁移框架。版本 0.4.0 的运行功能沿用原 0.3.4；新增品牌、独立环境、公开文档与安全检查。

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
| 候选来源、GitHub 请求 | `discovery.py`、`github_client.py`、`trending.py`、`trendshift.py` |
| 榜单与更新规则 | `ranking.py`、`service.py`、`daily_update.py` |
| 数据、历史、关注分类 | `storage.py`、`history_search.py`、`follow_folders.py` |
| README 和翻译 | `readme_service.py`、`project_translation.py`、`translation_service.py`、`translation_worker.py` |
| 登录与 AI | `github_account.py`、`codex_connection.py`、`ai_service.py` |
| 本机 API 与启动 | `browser_server.py`、`browser_launcher.py`、`__main__.py` |
| 页面与动效 | `web_assets/`；`app.js` 入口，其他交互按职责分模块 |
| Windows 任务、安装与卸载 | `windows_scheduler.py`、`uninstall.py`、`packaging/` |

## 运行边界与数据流

应用以 `__main__.py` 启动，`browser_launcher.py` 打开默认浏览器，`browser_server.py` 在回环地址提供页面和 API。静态界面用原生 HTML/CSS/JavaScript；个人状态进入 `storage.py` 管理的 SQLite 与配置。服务层协调业务，来源层处理 GitHub/公开页面请求，展示层组合可阅读的数据。没有公网后台或项目自建账号服务。

一次更新的主要路径：`daily_update.py` / `service.py` → `discovery.py` 获取候选 → `github_client.py` 获取公开仓库及 Star 历史 → `ranking.py` 筛选与排重 → `storage.py` 保存当天快照 → `project_translation.py` 排队预译入选项目。来源失败与预算停止需要保存可恢复状态，不能把未核实数字当成有效增长。手动与定时更新共享锁；并发点击复用活动任务。

阅读已保存的分类、日期或关注项目不等于再次更新。README 由 `readme_service.py` 获取并缓存；翻译由 `translation_service.py` 管理缓存与单任务进程，`translation_worker.py` 负责 CPU 推理。详情准备需要的译文后再展示，避免结束动效后替换文字。AI 由 `ai_service.py` / `codex_connection.py` 处理，必须用户触发；生成的结构化结果保存后再进行本地翻译。

前端入口是 `web_assets/app.js`；历史日历、关注文件夹、翻译和真实 DOM 动效分模块。调整布局时复用主题变量，避免给单页重复硬编码尺寸。标题与排名的艺术字体不扩展到所有正文；固定列数与字号放大依赖滚动，不通过加宽侧栏解决。

新维护者可以先选择一个已有模块和对应测试阅读，不需要先重写整个应用。修改排序要一起读 `ranking.py` 与更新协调；修改缓存要检查内容指纹、失败重试和并发边界；修改外部资料渲染要检查文本转义及链接安全。

## 不能改错的规则

- 增长取最近已结束的完整 **UTC** 统计日；北京时间当天 08:00 才结束昨天 UTC 日。手动更新优先，自动任务关闭时仍可用；并发点击复用任务。
- 历史重复前五项目紧凑显示，不占新增五个名额；不足五个时如实显示，不假造项目。关键词根据当前真实总 Star 排序、历史排重，不机械展示固定下一段名次。
- 本地预译只处理已入选项目，单任务、分批、硬盘缓存；内容不变不重译，不自动生成 AI，不翻译全部候选。代码、链接及仓库标识保持原样。
- 详情六卡为三列两行、等大、内部滚动；历史与关注五列紧凑卡。日期进入独立当天页，搜索禁用/淡出日历。文件夹重命名/删除保留关注，可排序与拖入未分类项目。
- 标题和排名共享艺术字体；森林透明主题、固定侧栏、透明菜单和主题滚动条保持。真实 DOM 渐显最多约 350ms，旧页即时消失；静态关注头部不反复重播。
- 服务只监听回环地址，所有可变操作核对会话凭证/来源；README 按不可信文本处理，不执行其脚本或命令。AI 手动调用，普通语言解释术语。

## 自动检查

```powershell
$env:RADAR_TEST_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
.\.venv\Scripts\python -m unittest discover -s tests
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

`check.yml` 在普通提交/PR 执行，只读权限。`build.yml` 只允许 `workflow_dispatch`；其构建任务经维护者授权具有 `contents: write`，没有推送或 PR 发布入口。

发布步骤：

1. 确认版本、隐私检查、许可证、用户手册及变更范围，创建指向待发布源码的标签，并准备对应 Release 草稿。
2. 在 Actions 的 Windows installer 工作流手动输入已有标签；空标签只保留 artifact，不公开发布。
3. 工作流检出指定标签，用 Python 3.13/Node.js 22 创建独立环境，运行公开文件检查和完整回归，再准备模型并打包。
4. 上传安装包、用户手册及 SHA256，匹配服务端安装包摘要后将该标签的 Release 公开为预览版。验证失败时停止，维护者检查日志后处理，不把失败改成成功。
5. 下载公开安装包，再检查 SHA256，进行独立安装环境验收。构建成功不等于真实授权和长期运行全部验收。

草稿信息通过认证后的 Release 列表读取；GitHub 的按标签查询可能对草稿返回 404。流程只更新已存在的标签/Release，不创建或重写标签，不发布未测试的其他分支。需要更改发布权限或自动触发条件时另行讨论。

README 截图只使用隔离演示数据，说明合成数值与人工演示文本。不要把真实账户、授权状态、个人关注列表或本机地址作为公开示例。截图在 `docs/images/`；日常验收日志放仓库外或忽略目录。

## 内存与性能

已有按需加载、CPU int8、一次一个翻译子进程、有限任务队列和译文硬盘缓存。区分安装包大小、Python 后端、翻译子进程和浏览器内存，不用下载大小冒充内存占用。

建议分别记录空闲、更新、预译峰值、重复打开详情、长时间空闲；模型准备/下载不计入运行占用。优先复用缓存和释放临时任务，再考虑改变线程/模型；换语言或框架不能保证降低模型内存。每次优化附同一输入的前后对比和质量回归。

0.4.0 本机短句基线（工作集，MiB）：纯 Python 16.5、加载翻译运行库 30.2、单方向译完 135.2（峰值 137.1），同一进程双方向 227.9（峰值 229.4）。仅测翻译进程，不含后台/浏览器，不能作为整产品内存承诺。实际后台一次一任务，译文持久化后工作子进程退出释放模型；大段 README 应另测峰值。

## 贡献与维护约定

1. 先在 Issue 说明问题或设计；修复附复现方法，功能变化先讨论。
2. 从 main 创建短分支；改动按职责，避免全仓格式化、整体重写及顺手改界面。
3. 清楚命名；注释解释约束、原因和安全边界，不逐句复述代码。异常要保留已有数据，并显示可理解反馈。
4. 给有影响的行为补回归；界面检查键盘、字号、减少动效。测试不要依赖私人凭证、付费 AI 或实时网络成功。
5. PR 写明问题、行为变化、验证和限制。提交前审查所有新增文件和提交邮箱。

阶段结果集中维护本指南与发布说明；临时日志、截图、旧包由维护者保存在仓库外或 `.build`，不不断新增验收报告。后续重点：干净 Windows/不同电脑、有效真实授权跨夜续期、性能基线、多模块渐进拆分；尚未测的事项不要写成已经完成。
