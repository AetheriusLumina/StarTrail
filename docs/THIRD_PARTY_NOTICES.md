# 第三方与资源说明

MIT 适用于 StarTrail 自有代码和文档。以下资源与运行库保留各自授权，不因本项目采用 MIT 而改变。StarTrail 是独立项目，不使用 GitHub 官方标识作为产品图标，不代表 GitHub 官方产品。

| 资源 | 来源与授权 |
|---|---|
| 原创猫形星光图标 | StarTrail 项目生成并采用，随项目按 MIT 提供；不是 Octocat 官方商标。 |
| 山林背景 `forest-mist.png` | 维护者已确认拥有或取得公开分发授权，允许随本项目分发；不单独声明第三方图像为 MIT。 |
| Cormorant Garamond / Noto Serif SC | Google Fonts，SIL OFL 1.1。字体目录包含原版权、完整 OFL 和来源/哈希清单。 |
| 英中/中英离线模型 1.9 | [Argos 模型源](https://www.argosopentech.com/argospm/index/)，源下载地址与 SHA256 在模型清单；派生自 Jörg Tiedemann、Santhosh Thottingal 的 OPUS-MT 模型，模型 README 标注 CC BY 4.0。安装包保留两个 README 和模型元数据，另附 CC BY 4.0 条款与原作者署名，模型数值未修改。 |
| CTranslate2 4.8.2 | [OpenNMT](https://github.com/OpenNMT/CTranslate2)，MIT；Windows wheel 的 Intel OpenMP/静态计算组件保留上游条款；CPU 安装包排除未使用的 cuDNN loader，不提供 GPU 模式。 |
| SentencePiece 0.2.2 | [Google](https://github.com/google/sentencepiece)，Apache 2.0。 |
| NumPy / PyYAML | BSD / MIT；NumPy 内含的其他组件见其随附完整许可证。 |
| Python | Python Software Foundation 授权，包含标准库的相关第三方声明。 |
| PyInstaller | GPL 2.0 加引导程序分发例外，允许按自有授权分发冻结应用；不将 PyInstaller 本身重新授权为 MIT。 |
| Inno Setup | 官方 Inno Setup License；中文消息来自上游翻译文件，保留来源。 |

运行依赖固定在 `pyproject.toml`、`requirements/constraints.txt`，模型固定在 `packaging/translation-manifest.json`。打包程序收集 Python 包许可证及模型说明到 `AppFiles/licenses/`，具体版本/文件哈希集中在该目录的清单；构建工具的安装位置、用户名与绝对路径不写入公开清单。

仓库读取的 README、描述及公开榜单资料属于各仓库/来源的作者；显示和翻译不转让其版权。测试网页为最小合成夹具，不包含整站抓取的页面或私人数据。

发布者升级依赖或更换模型时应重新核对授权与分发条款，不能只修改版本号。二进制发布还应完成干净 Windows、有效真实授权及代码签名的后续验证；源码开源不等于这些项目已经通过。
