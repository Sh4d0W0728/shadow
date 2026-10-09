# 维护与发布 shadow

仓库中的 `shadow/` 是可安装插件；用户的媒体、素材库、授权附件、模型、缓存、字幕和 Premiere 工程属于用户项目，不应放进插件源码。维护脚本只使用 Python 标准库，运行源码验证和发布测试无需安装 FFmpeg、faster-whisper 或 Premiere。

## 本地验证

在仓库根目录运行 Python 3.10 或更新版本：

```console
python -X utf8 scripts/validate_plugin.py
python -X utf8 -m unittest discover -s tests -v
python -X utf8 scripts/build_release.py
```

构建结果在 `dist/`：`shadow-v<version>.zip`、固定下载名 `shadow.zip` 和 `SHA256SUMS`。两个 ZIP 完全同字节。固定下载名供 GitHub 的 `/releases/latest/download/shadow.zip` 链接使用；带版本的文件方便归档和指定版本安装。校验文件包含两个 ZIP 的 SHA-256。Windows 可用 `Get-FileHash -Algorithm SHA256` 核对，Linux 可用 `sha256sum -c SHA256SUMS`。在相同源码与 Python/zlib 环境中重复构建，文件排序、ZIP 日期和权限均固定；构建器不承诺不同 zlib 版本的压缩字节相同。

默认拒绝覆盖已有构建结果。确认重建同一版本时用 `python -X utf8 scripts/build_release.py --force`。`--force` 只替换生成的 ZIP 和校验文件；不会改动源码。可用 `--output-dir` 指定仓库外目录；仓库内统一使用 `dist/`，避免把生成物混进插件。`--root` 可验证或构建另一个完整源码目录。

验证器检查两个 manifest 与 `shadow.py VERSION` 的版本一致、marketplace 的 `./shadow` 引用、图标和技能入口、技能元数据、JSON 重复键/无效数字、Python 语法、Markdown 本地文件链接、技巧与配方关系，以及领域目录的 schema、必需字符串和列表、数值优先级、ID、基础配方和参考文档。外部网址只检查格式并列出域名，CI 不联网确认网页仍然可访问。不同领域共享关键词是正常的混合意图，不作为重复错误。

检查通过说明源码和包结构通过验证。视频编码、声音、识别准确性、中文文字、原生转场和 Premiere 导入需要另外用实际媒体验收；不能把发布 CI 的通过当成这些能力已经在所有系统验证。标题图层目前依赖 Windows 字体绘制，媒体工具与 ASR 后端仍为运行时依赖。

## 增加或修改内容

发布器使用明确的文件白名单，不会递归打包任意项目文件。插件源码、根目录安装/更新脚本、README、CHANGELOG、LICENSE、marketplace 与本维护文档进入 ZIP；仓库维护脚本、测试和 Actions 工作流保留在 GitHub 源码中。`.git/`、已有 `dist/` 和明确的 Python 缓存不入包，并在构建报告中列出。

新增脚本、参考文档或资源时，同步更新 `scripts/validate_plugin.py` 中的 `PLUGIN_FILES` 或相关白名单，再增加测试。未知文件或目录会让构建失败并指出具体路径，不能通过把它放进 `__pycache__` 隐藏：缓存目录只允许 `.pyc`。媒体文件、私人账户绝对路径、凭据形态的文本和二进制内容均会在发布前被拒绝。不要把会员下载样片或个人工程提交进仓库。

新增领域时，在 `shadow/skills/shadow/assets/domain-profiles.json` 登记唯一 ID、已有的 `base_recipe` 和 `references/domains/<id>.md`。登记的领域文档形成动态明确白名单；未登记的额外领域文件会失败。新增技巧时同步更新 `recipes.json` 的 `technique_index`、实现级别和 `references/techniques.md` 的技巧卡；新增配方时同步更新 `recipes.json` 与 `references/workflows.md` 的对应小节。参数应描述适用条件及实际起点，不能把建议写成必然效果。

修改用户可见 CLI 或时间线格式时，同时修改帮助、参考文档和可复现样例。兼容旧时间线，或提供明确迁移说明。涉及媒体的修改使用用户许可的素材或合成素材完成运行测试，把本机临时证据保留在工作目录；源码仓库只提交不含私人路径和会员素材的可复用测试。

## 发布一个版本

1. 同步修改 `shadow/plugin.json` 与 `shadow/.codex-plugin/plugin.json` 的 `version`、`shadow/skills/shadow/scripts/shadow.py` 和 `subtitles.py` 的 `VERSION`，更新 `CHANGELOG.md` 与 README 当前版本。版本格式为 `major.minor.patch`，发布标签必须为对应的 `v<version>`。
2. 运行上述验证、测试和构建，再检查包内文件及校验值。也可用 `python -X utf8 scripts/validate_plugin.py --tag v2.1.0` 在本地检查拟用标签。
3. 提交源码并创建指向该提交的 Git 标签。在 GitHub 创建并发布该标签对应的 Release，按真实改动填写发布说明。
4. `release.yml` 接收已有 Release 的 `published` 事件，检出该标签，重新匹配两个 manifest、运行测试和构建，然后将两个 ZIP 与 `SHA256SUMS` 上传到这次 Release。
5. 确认 Release 中三个附件可下载，校验哈希，并以独立安装目录检查离线安装及联网更新路径。

验证工作流在 push 和 pull request 上运行，覆盖 Ubuntu/Windows 与 Python 3.10/3.12。发布工作流仅给附件上传任务 `contents: write`；token 通过 `GH_TOKEN` 环境变量供 GitHub CLI 使用，不写进源码或包。发布步骤使用 `gh release upload`，没有 `gh release create`，不会从 push 自动创建版本。GitHub 对 `published` 的触发规则见 [官方事件文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release)，权限设置见 [GITHUB_TOKEN 官方说明](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)，已有 Release 的附件上传参数见 [GitHub CLI 官方说明](https://cli.github.com/manual/gh_release_upload)。

Actions 使用 [actions/checkout v4](https://github.com/actions/checkout/tree/v4) 与 [actions/setup-python v5](https://github.com/actions/setup-python/tree/v5) 的官方主线版本。后续升级 action 时重新核对 runner 与 Node 兼容要求；需要更严格供应链固定时可将经过核对的版本改为完整提交 SHA。

发布器不带 `--clobber`。如果同名附件已存在，上传会失败而保留旧资产。需要重跑时先核对现有附件与本地生成物是否相同；如必须替换，由维护者明确删除相应附件后重跑该工作流。不要自动覆盖已经分发给用户的版本；内容有变化时应发布新版本。
