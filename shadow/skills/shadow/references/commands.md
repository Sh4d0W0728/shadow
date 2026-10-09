# 本地命令

所有路径应替换为当前技能/任务的真实绝对路径。使用 Python 3.10+，核心时间线脚本只需要标准库；语音识别另需 faster-whisper；媒体操作调用已有 FFmpeg/ffprobe。`shadow.py COMMAND --help` 给出完整参数。

## 配方候选

```powershell
python -X utf8 "<技能目录>/scripts/recipes.py" list
python -X utf8 "<技能目录>/scripts/recipes.py" select "口播，保留自然停顿，配相关的补充画面"
python -X utf8 "<技能目录>/scripts/recipes.py" show talking-head
```

`select` 输出关键词候选和匹配依据；没有匹配或并列时标为 `ambiguous`。根据用户目的、素材和对应工作流决定，不能把分数解释成内容理解置信度。

## 素材检查

需要把原片按实际画面分类、编号并复制到指定目录时，用 `organize.py` 的 `prepare`、`review-template`、`plan`、`apply`、`verify`；完整命令及实际查看画面的方法见 [分类整理](organize.md)。下面的 `inspect` 只检查技术信息，不完成语义分类或物理归档。

```powershell
python -X utf8 "<技能目录>/scripts/shadow.py" doctor
python -X utf8 "<技能目录>/scripts/shadow.py" inspect "<素材目录>" --recursive --sha256 --output "<任务目录>/media.json"
```

`inspect` 支持多个文件/目录，目录递归需要显式 `--recursive`；默认最多 1000 个文件。SHA-256 会完整读文件，大素材或只需元数据时省略 `--sha256`。

可使用 `--ffprobe "<ffprobe.exe>"`，涉及 FFmpeg 的命令另可使用 `--ffmpeg "<ffmpeg.exe>"`。环境变量 `SHADOW_FFMPEG` / `SHADOW_FFPROBE` 可以提供本次进程的配置；也会搜索 PATH 和常见本地 Windows 路径。不要仅因缺少依赖就修改系统环境或另装软件。

## 场景和静音候选

研究参考成片时，可一次准备全片接触表、候选前后帧和音轨信号报告：

```powershell
python -X utf8 "<技能目录>/scripts/analyze_reference.py" "<参考片.mp4>" --out "<任务目录>/参考分析新目录" --interval 3 --pairs 24
```

该工具使用 Python 标准库、FFmpeg/ffprobe，全程不播放。输出目录必须不存在；不提供覆盖选项。报错输出诊断并返回非零退出码。报告仅检查首个视频/音频流；必须实际看图才能形成内容结论。详情见 [参考片研究](reference-study.md)。

```powershell
python -X utf8 "<技能目录>/scripts/shadow.py" scenes "<素材.mp4>" --threshold 0.30 --min-gap 1.0 --output "<任务目录>/scene-candidates.json"
python -X utf8 "<技能目录>/scripts/shadow.py" silence "<素材.mp4>" --noise-db -35 --min-duration 0.4 --output "<任务目录>/silence-candidates.json"
```

场景阈值的范围是 `(0,1]`，最短间隔非负；静音阈值为 `[-120,0] dB`，持续时间需大于零。起点要按素材调整；闪光、运动、音乐和环境底均可能影响检测。检测不会自动删去任何片段。

长操作可用 `--timeout` 限制每次媒体操作秒数，默认 3600；这是执行超时，不表示预计需要一小时。分段分析更方便缓存和汇报。

## 时间线和 XML

```powershell
python -X utf8 "<技能目录>/scripts/shadow.py" validate "<任务目录>/timeline.json" --report "<任务目录>/timeline-check.json"
python -X utf8 "<技能目录>/scripts/shadow.py" xml "<任务目录>/timeline.json" --output "<任务目录>/剪辑.xml"
```

输入见 [时间线格式 v2](timeline-v2.md)。XML 默认附带 `剪辑.validation.json`，其中明确 `premiere_import_verified: false`；报告的结构通过不能用于宣称 PR 已导入。素材帧率不能准确表示为 FCP7 的整数或 NTSC 分数时，需要先转为合适的中间素材，或遵照警告在 PR 中核对。

XML 报告里的 `xml_clip_timing` 列出量化后的源帧数和秒数，可对照混合帧率的实际入出点。当前 XML 导出仅可靠表达方形像素源；非方形像素源需要先制作明确标注的方形像素中间素材。粗剪渲染可以按源显示纵横比处理这类素材。

源存在多音频流或多视频流等不能可靠交换的情况，XML 会拒绝而不是猜测正确轨道。剪切仍可由执行者在 PR 里完成，或先将明确选定的流处理成新素材。

源音频按通道拆分并保留链接；立体声配对和输出信息已写入。Premiere 的实际导入声像/多声道混音仍需试听，不能仅靠 XML 的通道数量推定正确。

## 粗剪预览

```powershell
python -X utf8 "<技能目录>/scripts/shadow.py" render "<任务目录>/timeline.json" --output "<任务目录>/粗剪.mp4" --dry-run
python -X utf8 "<技能目录>/scripts/shadow.py" render "<任务目录>/timeline.json" --output "<任务目录>/粗剪.mp4"
```

`--dry-run` 验证输入并显示参数数组，不编码、不创建输出。正式渲染先在临时目录完成逐段处理、合并和背景音乐混合，检查尺寸、帧率、音频、时长和全片解码，再提交输出。默认附带 `粗剪.render-report.json`。

尺寸转换保持显示纵横比并补边，不做主体追踪或创意裁切。音频按序列采样率和立体声预览处理；音乐按请求增益相加，没有自动 Ducking、限幅或响度目标。混音仍需试听。

`--video-codec auto` 优先 `libx264`；不可用时对目标尺寸/FPS执行一帧 NVIDIA NVENC 编码探针，成功才选择 `h264_nvenc` 输出 H.264；GPU/驱动或目标格式不支持时退到软件 `mpeg4`（MPEG-4 Part 2）并明确警告。可显式指定 `libx264`、`h264_nvenc` 或 `mpeg4`；明确指定的编码器不可用时会报错，不自动安装依赖。输出报告记录实际编码器和实际媒体格式。不要将本地粗剪的编码设置当成用户最终发布规格。

## 文件与错误

命令成功输出 JSON，失败输出 `{ok:false,error:...}` 并返回非零退出码。现有输出需要显式 `--force` 才能替换，原素材和输入时间线即使加 `--force` 也不会被覆盖。

每个错误保留源路径和具体阶段，按错误修正后重试。不要把检测失败转换成“没有素材”“没有静音”或“已经导入成功”。
