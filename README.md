# 星迹 · StarTrail

发现值得关注的开源项目。Windows 本地应用，使用默认浏览器展示，个人数据保存在本机。

StarTrail is a Windows-first, local reader for public GitHub projects. It combines verified daily Star growth, keyword discovery, a history calendar, folders, and offline Chinese/English translation. This is an independent project, not an official GitHub product.

## 功能

- 增长榜使用最近已结束的完整 UTC 统计日；重复历史项目紧凑展示，并继续寻找未展示的新项目。
- 关键词项目按当前总 Star 等真实条件排序，历史排重；不会按天机械轮换名次。
- 历史日历、组合搜索、关注与文件夹；项目详情提供六项关键信息。
- 选中项目在后台本地预译，译文缓存到硬盘；内容没变就不重译。
- AI 分析是可选、手动的，需要另装并登录 Codex，会使用各自账号额度。基础浏览不需要 AI。

## 开发启动（Windows x64）

需要 Python **3.13 x64**、Node.js **22 或更高版本**和 Git。当前验证范围是 Windows 10/11 x64；其他系统尚未支持。

```powershell
git clone https://github.com/AetheriusLumina/StarTrail.git
cd StarTrail
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements/constraints.txt -e ".[translation]"
.\.venv\Scripts\python scripts/prepare_models.py
.\.venv\Scripts\python -m github_radar
```

首次模型下载可能较慢。之后翻译本身离线运行，不发送文本到云端、不使用 AI token。源码和模型来源、安装使用说明及开发/打包命令见 [开发指南](docs/DEVELOPMENT.md) 与 [用户手册](docs/USER_GUIDE.md)。

```powershell
$env:RADAR_TEST_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
.\.venv\Scripts\python -m unittest discover -s tests
```

测试使用合成数据和本机临时目录，无需个人凭证，也不会付费调用 AI。Node.js 在 PATH 中，用于运行交互测试。

## 文件夹

| 位置 | 用途 |
|---|---|
| `github_radar/` | 后端与浏览器资源；旧包名保留以兼容原版本 |
| `tests/` | 自动回归与合成样本，不含以前的个人验收记录 |
| `scripts/` | 模型准备及维护检查 |
| `packaging/` | Windows 打包、安装器和模型来源清单 |
| `docs/` | 用户和开发者说明 |
| `.github/` | Windows 自动检查与发布构建 |

本地环境 `.venv`、模型 `.tools`、构建 `.build`、个人数据 `UserData` 都忽略，不提交。不要上传凭证、日志、数据库或个人机器配置。

## 参与开发

通过 Issues 讨论问题，Fork 后提交 Pull Request；小范围修改、说明行为和验证结果。详见[贡献与维护约定](docs/DEVELOPMENT.md#贡献与维护约定)。漏洞请使用私密报告，不公开贴 Token 或个人日志，见 [安全说明](.github/SECURITY.md)。

项目代码采用 [MIT](LICENSE)；字体、模型及背景资源各有单独授权，见 [第三方与资源说明](docs/THIRD_PARTY_NOTICES.md)。安装版无需 Python；安装包通过 Releases 发布，不放进源码 Git 历史。
