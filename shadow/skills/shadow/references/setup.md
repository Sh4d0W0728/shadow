# 运行环境与首次准备

核心命令需要 Python 3.10+；媒体探测、缩略图、剪辑与导出需要 FFmpeg/ffprobe。包图网搜索下载需要当前 Codex 会话具备浏览器控制能力；原生 PR 验收需要已安装 Premiere 与可用的桌面控制。

```powershell
python -X utf8 "<skill>/scripts/shadow.py" doctor
```

FFmpeg 不在 PATH 时可以给 `--ffmpeg` / `--ffprobe` 绝对路径，或只为当前进程设置 `SHADOW_FFMPEG` / `SHADOW_FFPROBE`。工具不修改系统环境。Windows 中文标题使用本机 GDI 与已安装字体；跨平台标题渲染需另外提供可用字体后端，按 doctor 与实际错误处理。核心脚本不自动安装其他软件。

## 可选本地语音识别

只在需要从音频识别字幕时准备，已有SRT无需安装。首次下载依赖和模型需要联网；识别与文稿处理在本机完成。

```powershell
python -X utf8 "<skill>/scripts/setup_asr.py" --directory "<工具目录>"
```

准备工具创建该目录的 `.venv` 和 models，安装 faster-whisper 1.2.1，下载并校验官方固定版本 tiny 模型，然后验证实际加载。用 `--check-only` 复核现有环境。网络环境确实需要时，可显式加 `--direct --curl-fallback`；这仅控制子进程网络环境，保留TLS校验，不修改系统代理。失败诊断留在工具目录，不报告为已准备。

后续用 `.venv/Scripts/python.exe` 调用 `subtitles.py`，指定实际模型目录。tiny 便于快速试通，复杂对白和中文生产任务可使用本地 small 或更适合的模型，再按校核报告复核。模型大小与识别准确率不能用单条演示作保证。

## 可选 RAR 支持

包图网实际素材可能是 RAR。`assets.py extract-rar` 使用已经安装的 UnRAR，先核验成员清单，再按安全路径逐个写出。找不到时给 `--unrar` 绝对路径，或由用户通过官方工具手动解压到staging再导入。ZIP安全解包使用Python标准库。

## 本机验证条件

2026-10-09 开发测试环境：Windows、Python 3.10.1、Topaz随附FFmpeg 7.1、Premiere Pro 2023、Microsoft YaHei、已安装UnRAR。渲染会实测编码器：libx264优先，其次可用的NVENC，再退到mpeg4并报告。插件包不携带Adobe/FFmpeg/会员视频/大型ASR模型，也不要求OpenAI API Key。
