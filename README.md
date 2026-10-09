# shadow

面向 **Codex + Premiere Pro** 的影视剪辑插件。将常用方法做成可复用的领域流程、技巧库和本地工具：从素材整理、包图网补素材、叙事选片到时间线、字幕、标题与交付验证。

**当前版本：2.1.0。** 20 个细分领域、12 套基础配方、56 张技巧卡。领域规则按需要加载，减少每次从头规划；具体镜头、节奏和情绪仍结合实际素材判断。

[下载最新安装包](https://github.com/Sh4d0W0728/shadow/releases/latest/download/shadow.zip) · [版本与更新记录](https://github.com/Sh4d0W0728/shadow/releases) · [问题反馈](https://github.com/Sh4d0W0728/shadow/issues)

## 安装与更新

需要支持 `codex plugin` 的 Codex，以及 Git。Windows 下载上面的 ZIP，完整解压后双击 **安装shadow.cmd**。以后双击 **更新shadow.cmd** 即可拉取仓库 main 分支的版本。脚本只在运行时检查更新，不常驻、不后台轮询。

也可以在终端执行：

```powershell
codex plugin marketplace add https://github.com/Sh4d0W0728/shadow.git --ref main
codex plugin add shadow@shadow
```

以后更新：

```powershell
codex plugin marketplace upgrade shadow
codex plugin add shadow@shadow
```

安装后新开一个 Codex 对话，用 `$shadow` 调用。Codex CLI 的插件安装方式见 [OpenAI 官方文档](https://developers.openai.com/plugins/build/plugins)。这是可以从 GitHub 安装的自定义插件市场；安装包不含 Codex 本体。

Windows 可先检查环境：`powershell -NoProfile -ExecutionPolicy Bypass -File .\install-shadow.ps1 -CheckOnly`（仅用于本次进程，不改变系统策略）。找不到 CLI 时用 `-CodexPath "完整的codex.exe路径"`。遇到来源冲突，先在 Codex 中核对已有的 `shadow` 市场，不要覆盖一个来源不明的同名插件。旧版 `shadow@shadow-local` 与本版可以共存；确认新版本可用后，在插件页面停用旧版，避免两个同名 skill 同时生效。

离线机器可运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\install-shadow.ps1 -Local`，使用解压目录安装。该模式需要保留目录，不提供 GitHub 更新；`更新shadow.cmd` 会明确拒绝替换同名的本地来源。选择联网安装时需要先通过 Codex 移除该本地市场配置，再运行默认安装脚本。素材和项目应一直放在插件目录之外。

## 按成片目的选流程

| 领域 | 细分工作流 | 重点区别 |
| --- | --- | --- |
| 宣传片 | 品牌形象、企业介绍、产品演示、文旅目的地、服务与机构 | 品牌情绪、事实证据、功能因果、目的地体验各有组织方法 |
| 活动与会议 | 当天快剪、活动回顾、完整会议演讲 | 当晚交付优先关键瞬间；回顾补足过程；完整演讲保留语意与结构 |
| 网络教学 | 系统课程、微课、屏幕操作教程 | 章节递进、单一知识点、可复现操作分别设计 |
| 人物与纪实 | 人物采访、观察式纪录 | 采访以观点组织，观察式以行动和现场关系组织 |
| 音乐与混剪 | 歌词叙事 MV、节奏动作混剪、人物影视混剪 | 歌词情绪、音乐节拍、人物关系分别驱动镜头选择 |
| 生活与社交 | 婚礼故事、知识资讯、销售转化、个人旅行 | 人物关系、信息准确、利益点与行动、旅途体验分别处理 |

每个领域都包含必要输入、素材分类、结构起点、节奏与转场动机、音画/字幕/标题策略、交付物和质量关卡。用户给定的时长、画幅、帧率、参考片和文稿优先于模板。课程招生广告按宣传目的处理，不会仅因“课程”一词套用教学流程。

## 直接这样调用

```text
$shadow 用这些素材剪一条企业宣传片，面向客户，90秒，16:9，25fps。
先整理真实业务镜头，再列包图网需要补的氛围素材。交付PR工程、成片和SRT。
```

```text
$shadow 今天活动结束后要发一条45秒竖屏快剪。
保留开场、核心发言一句、观众反应和合影；先给出缺素材清单，再开始剪。
```

```text
$shadow 把这套录屏和讲稿整理成网络课程。
按知识点分章，保留操作因果，结合音频识别和讲稿校正文稿，输出分课视频、SRT和PR交接文件。
```

## 实际能力与运行条件

- **素材管理：** 本地 SQLite 清单、SHA-256 去重、来源记录、媒体信息、接触表和候选入出点。内容总结需要实际看画面、听音频。
- **包图网：** 通过 Codex 当前可用的浏览器能力操作会员页面。登录时由用户完成验证；正常下载后归档原文件、页面来源与授权附件。没有私有 API 接口，也不保存密码、Cookie 或令牌。会员素材不随插件分发。
- **时间线与成片：** Python + FFmpeg 创建、校验和渲染时间线，支持基础转场、音轨、字幕与标题合成，并导出 Premiere 可导入 XML。
- **字幕：** 本地 faster-whisper 识别序列音频，结合用户文稿校对，输出 UTF-8 SRT 和校正报告。模型首次需下载；也可使用本地模型目录。长片速度取决于硬件和模型。
- **标题：** 可生成片头、章节字和人物条。脚本交接的标题是可重新生成的透明图层；SRT 独立交付。在 PR 内制作原生可编辑文字需实际编辑器操作。
- **原生 PR：** `.prproj` 需要在 Premiere 中导入、保存和重开。能否自动操作取决于当前 Codex 的桌面控制能力与本机 Premiere；仅生成 XML 时会明确标注交付类型。复杂变速、稳定、精细调色与高级特效需要 PR 内处理。

核心脚本需要 **Python 3.10+、FFmpeg/ffprobe**。ASR 依赖按需安装，中文标题需要合适字体；Windows 是主要验证平台。先由 skill 执行 doctor，再依照 [运行环境说明](shadow/skills/shadow/references/setup.md) 配置缺项。浏览器和桌面控制由 Codex 环境提供，插件本身不会开启权限或安装剪辑软件。

## 开发和持续维护

领域库入口：[domains.md](shadow/skills/shadow/references/domains.md)。完整 skill：[SKILL.md](shadow/skills/shadow/SKILL.md)。基础配方与技巧：[工作流](shadow/skills/shadow/references/workflows.md)、[技巧卡](shadow/skills/shadow/references/techniques.md)。

```powershell
python -X utf8 scripts/validate_plugin.py
python -X utf8 -m unittest discover -s tests -v
python -X utf8 scripts/build_release.py --tag v2.1.0
```

以上开发命令在克隆的源码仓库内执行；安装 ZIP 仅包含使用所需文件。提交/PR 会运行校验；发布 GitHub Release 后，工作流会验证版本、生成版本 ZIP、固定名 `shadow.zip` 和 SHA-256 校验文件，并附到该 Release。后续新增领域只需扩充领域文档和索引，并通过校验。完整步骤见 [维护指南](docs/maintaining.md)。

仓库只收录插件代码与文档，不包含会员素材、账号会话、个人工程、ASR 模型或缓存。代码采用 [MIT License](LICENSE)；外部素材的使用范围以取得素材时的授权为准。
