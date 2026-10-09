# 真实音频识别、文稿辅助字幕与可编辑交接

用实际讲话决定字幕的内容和时间，用用户提供的文稿辅助核对标点、专名和差异。不能仅按文稿字符数平均分配时间，然后把结果称为音频识别。

## 先确定字幕使用哪条音频

对白剪辑已经完成时，识别最终粗剪或与最终序列同步的对白音轨，SRT 的时间即为序列时间。背景音乐较响时，优先使用同一时间线的干净对白轨。若识别的是未剪源文件，切镜、删句和变速之后必须重新映射时间；`shift` 只能处理统一偏移，不能替代多段剪辑或变速映射。

原声听不清、歌曲演唱或多人重叠时识别质量可能下降。歌词字幕核对真实歌名、完整音频、歌词版本和演唱时间。文稿与音频不同，以实际录音为锚并报告差异；不悄悄补上未说的话。

## 本地识别

入口脚本的 SRT、ASS、文稿校对与时间操作使用 Python 标准库。真正的 ASR 使用可选本地后端 `faster-whisper`，按其 [官方文档](https://github.com/SYSTRAN/faster-whisper) 调用 CPU/int8 和词级时间戳。第一次需准备依赖和模型；识别期间音频在本机解码和推理，模型下载会访问模型托管站点。

先检查本机已有环境和模型。缺依赖时优先用自带的显式本地准备命令：

```powershell
python -X utf8 "<技能目录>/scripts/setup_asr.py" --directory "<任务目录>/asr-tools" --curl-fallback
python -X utf8 "<技能目录>/scripts/setup_asr.py" --directory "<任务目录>/asr-tools" --check-only
& "<任务目录>/asr-tools/.venv/Scripts/python.exe" -X utf8 "<技能目录>/scripts/subtitles.py" transcribe "<最终粗剪或对白.wav>" --output "<任务目录>/字幕.srt" --manuscript "<任务目录>/文稿.txt" --language zh --model-dir "<任务目录>/asr-tools/models/tiny" --local-files-only
```

`setup_asr.py` 只在明确指定的工具目录创建虚拟环境并安装 `faster-whisper==1.2.1`，从官方 `Systran/faster-whisper-tiny` 仓库的固定版本 `d90ca5fe260221311c53c58e660288d3deb8d356` 下载约 75.5 MB 权重及附属文件。按已验证的官方 Git/LFS 哈希检查四个文件，并实际加载本地 CPU/int8 模型；输出诊断和 `setup-report.json`。不修改全局 Python、系统环境或关闭 TLS；模型已有且通过校验时复用。`--curl-fallback` 只在普通 HTTPS 传输失败时改用保持 TLS 校验的官方 URL/curl。代理配置阻碍连接时可加 `--direct`，只对本命令的网络与子进程忽略代理变量。`--check-only` 不创建或下载文件，也不进行识别。

若已有自己的受控环境，也可手动准备；示例中的路径需替换为真实任务目录：

```powershell
python -m venv "<任务目录>/.venv-asr"
& "<任务目录>/.venv-asr/Scripts/python.exe" -m pip install faster-whisper
& "<任务目录>/.venv-asr/Scripts/python.exe" -X utf8 "<技能目录>/scripts/subtitles.py" transcribe "<最终粗剪或对白.wav>" --output "<任务目录>/字幕.srt" --manuscript "<任务目录>/文稿.txt" --language zh --model tiny --download-root "<任务目录>/asr-models"
```

默认 `tiny` 是小型多语模型和 CPU/int8 起点，适合验证通路，不能据此保证专业字幕准确率。可选择 `base`、`small`，或通过 `--model-dir` 使用已有的 CTranslate2 Whisper 模型目录。模型目录需含 `model.bin`；本脚本不会自动下载超大模型。依赖或下载失败时报告故障阶段和具体错误；没有真正运行识别就不能标为“ASR 已验证”。GPU 是可选项，先确认实际 CUDA/CTranslate2 兼容性，不为一次字幕任务修改系统驱动。

```powershell
& "<任务目录>/.venv-asr/Scripts/python.exe" -X utf8 "<技能目录>/scripts/subtitles.py" transcribe "<对白.wav>" --model-dir "<本地模型目录>" --local-files-only --language zh --output "<字幕.srt>" --manuscript "<文稿.txt>"
```

默认同时生成 `字幕.segments.json`（原始识别文字、词/段时间、词概率和后端信息）及 `字幕.subtitle-report.json`（时间依据、文稿对齐、校正状态、差异与复核数量）。`--segments`、`--report` 可以指定新输出路径；源文件不会被覆盖。

## 文稿怎样参与

- 文稿为 UTF-8 纯文本，不需要伪造时间码。按识别字幕顺序与文稿做单调文本对齐，保留真实词/段时间。
- 默认仅在标准化文字一致时修正文稿中的标点和大小写。识别语言为 `zh` 时，Windows 通过 [微软 LCMapStringEx 字形映射](https://learn.microsoft.com/en-us/windows/win32/api/winnls/nf-winnls-lcmapstringex) 把简繁字形视为等价用于对齐，再输出用户文稿的字形；已有 JSON 可显式加 `--chinese-script-equivalence`。此项保留原始识别结果，不进行同音词替换。其他系统当前不自动映射简繁。词语不一致则保留识别内容，在报告里给候选文稿、相似度、跳过的文稿和 `review_required` 状态，交由听校。
- 确有校对需求时可加 `--allow-lexical-corrections`：只处理相似度不低于 0.94 的文字，并阻止改变数字或否定词的自动修正。所有词语修改仍标为需要人工复核。此规则是保守文本规则，不能证明候选内容被真实说出。
- `mean_word_probability` 是识别后端词概率的均值；`alignment_similarity` 是文本相似度。两者不同，也不是校准后的正确率或“已经听校”证明。
- 有词时间时，按实际词边界、停顿与句号拆分字幕。`--max-chars` 默认 42 个显示单位（中日韩宽字符按 2 算），`--max-duration` 默认 6 秒。只有段时间时保留整段，不按字符比例捏造细分时间，超长段落会提示复核。

## 已有识别数据与 SRT 操作

已有 Whisper 格式的 `segments` JSON 可以使用，但报告会明确本次没有运行识别。接受数组，或含 `segments` 的对象；每段有 `start`、`end`、`text`，可选 `words`，每词有 `start`、`end`、`word` 和可选 `probability`。

```powershell
python -X utf8 "<技能目录>/scripts/subtitles.py" from-segments "<识别结果.json>" --output "<字幕.srt>" --manuscript "<文稿.txt>"
python -X utf8 "<技能目录>/scripts/subtitles.py" validate "<字幕.srt>"
python -X utf8 "<技能目录>/scripts/subtitles.py" shift "<字幕.srt>" --seconds 1.25 --output "<偏移字幕.srt>"
python -X utf8 "<技能目录>/scripts/subtitles.py" ass "<字幕.srt>" --output "<字幕.ass>" --font "Microsoft YaHei" --font-size 48 --width 1920 --height 1080 --position bottom --color "#FFFFFF"
```

SRT 校验 UTF-8、时间格式、正时长、顺序和重叠；默认拒绝重叠，确实需要时显式 `--allow-overlap`。负偏移若使任何字幕早于零会失败，不偷偷截掉文字。现有生成输出需要 `--force` 才可替换。

ASS 是可选择中文字体的基础样式预览交换，时间量化为最近 10 毫秒；保留原 SRT 方便精确编辑。`--font` 只记录字体名称，需确认目标机器实际安装字体。它不自动创建可编辑的 Premiere 图形标题或 MOGRT。Shadow 预览时间线使用 SRT/ASS 的**最终序列时间**；渲染器当前基础 ASS 支持范围以其实际文档和警告为准。

## Premiere 中保留可编辑文字

导入 SRT，再把字幕放到对应的最终序列字幕轨；在 Text/字幕界面校正文字、时间和样式。该流程由 [Adobe 官方 SRT 导入说明](https://helpx.adobe.com/premiere/desktop/add-text-images/insert-captions/import-caption-file-from-third-party-service.html) 支持。正文字幕通过 SRT 交接，单独的片头/章节/结尾标题通过标题计划和 Shadow 时间线标题字段交接；本地预览里的文字烧录或 PNG 是视觉预览，不等于 Premiere 原生可编辑图形。

字幕导入、实际字体、长句换行、目标设备可读性与音画同步要在实际软件/画面上验证。修改后另存原生 `.prproj`；没有实际操作过 Premiere 时报告“本地识别与字幕结构已验证，Premiere 导入/播放待验证”。
