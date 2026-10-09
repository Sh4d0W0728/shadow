# 把本地流程串起来

`pipeline.py` 在已经选好镜头并写出 timeline.json 后运行。它不会凭文件名编造镜头内容，不代替包图网登录或素材选择。

```powershell
python -X utf8 "<skill>/scripts/pipeline.py" "<项目>/timeline.json" --output-dir "<项目>/exports/v01" --transcribe --asr-python "<项目>/.shadow-venv/Scripts/python.exe" --model small --language zh --manuscript "<项目>/文稿.txt"
```

阶段顺序：验证时间线 → 渲染无文字粗剪 → 从成片序列抽取 16 kHz 单声道识别音频 → 真实 ASR 与文稿校核 → 校验 SRT → 合成标题字幕与最终视频 → Premiere XML 和文字图层交接。这样字幕基于剪辑后的时间，不会因剪掉停顿或增加转场而漂移。

已有正确时间轴的 SRT 可用 `--srt "文件.srt"`，无需安装 ASR。无字幕任务省略两项。若 timeline 自带 subtitles，则不能同时指定新字幕来源，避免意外覆盖。

`--output-dir` 必须是不存在的新目录，重剪用 v02/v03；不会覆盖早期成果。每一步输出 log，`pipeline-report.json` 记录命令、阶段、文件和失败原因。失败后读取日志，修复对应问题，使用新版本目录重试；可以单独调用各模块继续，无需重新下载素材。

默认不执行 PR GUI；`premiere_gui_verified` 保持 false。完成合成后仍应抽查画面与听取声音，按需要在 PR 中导入 XML 和独立 SRT、保存原生工程。文字 PNG 图层只是视觉交接，独立 SRT 用于 PR 原生字幕编辑，避免同时启用两套字幕造成重影。

ASR 依赖和模型留在项目工具目录。`--model-dir` 指定已经下载的 CTranslate2 模型，采用本地加载；不指定时 faster-whisper 首次按需下载指定模型。实际识别在本地，文稿和音频不通过本脚本上传。
