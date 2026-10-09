# Shadow 时间线 v2

`shadow.py` 同时支持 v1 与 v2。v1 保留连续硬切、源音频开关、独立音乐和报告元数据；新增能力必须使用 `schema_version: 2`。输入为 UTF-8 JSON，所有相对路径按 JSON 所在目录解析。时间单位是秒，FPS 必须是字符串整数或精确分数；例如 `"25"`、`"24000/1001"`、`"30000/1001"`，不要把 `29.97` 当作精确帧率。

```json
{
  "schema_version": 2,
  "project": {
    "name": "Shadow 旅行短片",
    "width": 1920,
    "height": 1080,
    "fps": "25",
    "sample_rate": 48000
  },
  "identity": {"title": "本次指定作品", "music_title": "本次指定歌曲"},
  "clips": [
    {"source": "素材/镜头一.mp4", "in": 0, "out": 4, "audio": true, "gain_db": -3},
    {
      "source": "素材/镜头二.mp4", "in": 1, "out": 5, "audio": true,
      "transition_in": {"type": "dissolve", "duration": 0.4, "audio": "crossfade"}
    },
    {
      "kind": "image", "source": "素材/照片.jpg", "in": 0, "out": 3,
      "transition_in": {"type": "fadeblack", "duration": 0.6, "audio": "crossfade"}
    }
  ],
  "music": {"source": "素材/音乐.wav", "in": 0, "gain_db": -18},
  "titles": [
    {
      "text": "一次旅行\n一段记忆", "start": 0.5, "end": 2.5,
      "font_face": "Microsoft YaHei", "font_size": 72,
      "color": "#FFFFFF", "position": "center", "bold": true,
      "fade_in": 0.2, "fade_out": 0.2
    }
  ],
  "subtitles": {
    "source": "字幕/校对字幕.srt", "font_face": "Microsoft YaHei",
    "font_size": 48, "color": "#FFFFFF", "position": "bottom"
  }
}
```

这个例子的最终长度是 `4 + 4 + 3 - 0.4 - 0.6 = 10` 秒，25 fps 下为 250 帧。字幕和标题使用这 10 秒最终序列的时间。若原始字幕对应未经剪辑的素材，必须先按剪后音频重新识别，或显式重排字幕时间。

## 镜头与时间计算

- `project.name` 为非空字符串。宽、高为 16–8192 的偶数；FPS 范围为 1–240。`sample_rate` 可选 44100、48000、96000，默认 48000。
- `clips` 非空，最多 10000 段。视频镜头要求 `0 <= in < out <= 已探测的视频时长`。`label` 可选，默认文件名。`audio` 是布尔值，视频默认为 true；缺少音轨时预览补静音，XML 的原声音轨在该处留空。`gain_db` 范围 -96 至 +12，默认 0。
- `kind` 默认 `video`。静态照片使用 `kind: "image"`，要求 `in: 0`，`out` 表示显示时长，`audio` 默认为 false；显式开启照片音频会拒绝。支持 PNG、JPEG、BMP、TIFF、WebP 的常规静态文件。照片保持显示纵横比并补边，不自动执行主体追踪、创意裁切或 Ken Burns 动画。
- 每段显示时长独立按序列 FPS 四舍五入到整帧，采用半帧向上取整。实际帧数和时长写入报告。所有范围检查仍使用实际源时长，不能通过量化读取越界素材。
- 没有 `transition_in` 就是硬切。脚本不会给每个切点自动加转场。`title` 和 `identity` 沿用 v1，仅供身份核对及报告；实际文字层必须写在 `titles` 中。
- `music` 可选。音乐 `in` 默认 0，`gain_db` 默认 0，增益范围 -96 至 +12。源音频与音乐相加，不自动 ducking、限幅或响度归一。音乐不足时明确警告并补静音，不自动循环。

## 转场

`transition_in` 放在进入的镜头上，类型为 `dissolve` 或 `fadeblack`；`duration` 为秒，`audio` 为 `crossfade` 或 `cut`，默认 `crossfade`。首段禁止转场。转场必须至少一帧，而且短于两段镜头；一段镜头的前后转场合计不能耗尽该段。

转场在两段完整选段之间产生重叠：进入镜头的起点等于上一段终点减去转场帧数，后续镜头相应前移。因此总帧数是镜头帧数之和减去所有转场帧数。标题、字幕、音乐终点及交付目标必须以此重新核对。

`dissolve` 在重叠区逐渐混合两个画面。`fadeblack` 先淡到黑，再从黑淡入，黑色位于转场中点；预览使用明确的对称运算，不依赖 FFmpeg 自带的不对称 fadeblack 预设。`crossfade` 按正弦曲线交叠音频；`cut` 在重叠区中点切换原声。音频静音开关和增益在转场前应用。

自然转场需要有理由。连续动作、采访观点和明确因果通常先试硬切；景别、光线或情绪接近的镜头，可试短叠化；明显的章节、时间或情绪停顿可试过黑。数值服从素材和用户要求，可以从约 0.2–0.5 秒试叠化、约 0.4–0.8 秒试过黑，再看与听，不能以这些起点代替判断。

## 中文标题与字幕

`titles` 是文字事件数组，每个事件要求 `text`、`start`、`end`。时间属于重叠计算后的最终序列。每个事件都必须至少占一帧且处于序列范围。最多 1000 个标题和字幕事件合计。

文字属性有 `font_face`、`font_size`、`color`、`position`、`bold`、`fade_in`、`fade_out`。字体默认 Microsoft YaHei；标题默认约画面高度的 7.5%，字幕默认约 4.5%，最小默认字号 18。明确字号要求 10–512 的整数像素。颜色为 `#RRGGBB`，位置为 `top`、`center`、`bottom`，默认标题居中、字幕居下。标题默认粗体，字幕默认正常字重；淡入淡出默认 0。文本支持换行，自动按安全宽度折行；无法容纳时明确报错，应缩小字号或修改断行。未知字体会拒绝静默替换。

当前中文栅格文字后端为 Windows GDI，使用已安装字体，Python 标准库即可工作，不需要 FFmpeg 的 drawtext/libass，也不下载字体。每个文字资产是带透明度和深色描边的 PNG。资产文件名由文字样式和画面尺寸确定。非 Windows 主机可继续无文字剪辑；这版栅格文字需要 Windows，或预先制作好的图片标题。

`subtitles.source` 接受 UTF-8 SRT 或 ASS。SRT 使用文本和时间；常见 i/b/u/font 标签不用于本版图片样式。ASS 使用 `[Events]` 的 Format/Dialogue 时间与文本，处理 `\N`、`\n`、`\h`，移除内联 override 标签；ASS 的样式、绘图、动画、卡拉 OK 等不会复现，报告会明确提醒。复杂 ASS 应交给适当字幕渲染器或 Premiere 后续制作。两种输入都输出统一的 `captions.srt`，供 Premiere 的原生字幕导入及校稿。

预览应用文字淡入淡出。XML 提供按时间放好的 PNG 图层，当前没有为这些文字图片生成原生 opacity 关键帧；如事件指定淡入淡出，报告会要求在 Premiere 中参照预览补加。PNG 是图片标题，文字可以修改 JSON/SRT 后重生；不能宣称是可直接编辑字词的 Premiere 原生文字对象。

## 预览与 Premiere 交接

```powershell
python -X utf8 "C:\实际技能目录\scripts\shadow.py" validate "C:\任务目录\timeline.json"
python -X utf8 "C:\实际技能目录\scripts\shadow.py" render "C:\任务目录\timeline.json" --output "C:\任务目录\outputs\成片.mp4" --dry-run
python -X utf8 "C:\实际技能目录\scripts\shadow.py" render "C:\任务目录\timeline.json" --output "C:\任务目录\outputs\成片.mp4"
python -X utf8 "C:\实际技能目录\scripts\shadow.py" xml "C:\任务目录\timeline.json" --output "C:\任务目录\outputs\交接.xml"
```

输出默认附带 `.render-report.json` 或 `.validation.json`。文字及局部转场资产存放在输出同名的 `.assets` 目录。若预览和 XML 使用同一文件主名，且已经产生同名资产，再生成时需要 `--force`；它只允许替换生成输出，仍拒绝源文件或源文件硬链接。要在不同目录交接时，需要把 XML、`.assets` 及相关媒体一并保留。

预览先规范每段尺寸、显示比例、FPS、像素格式、声道和采样率，再合成。`--video-codec auto` 优先 libx264；没有时会对目标尺寸/FPS实际探测 NVIDIA NVENC 能否编码一帧，成功后输出 H.264；不支持时退到软件 MPEG-4 Part 2 并明确警告。可指定 `libx264`、`h264_nvenc`、`mpeg4`。列出了 NVENC 不代表 GPU/驱动已经可用，失败不能宣称 H.264 成功。输出必须通过尺寸、FPS、帧数、时长、音频检查及全片解码。剪辑帧网格末尾可补不足一帧的最后画面，确保实际总帧数准确。

XML 的 `--transition-mode` 可选：

| 模式 | 结果 |
| --- | --- |
| `auto`（默认） | 可可靠表示的 dissolve 使用原生 `Cross Dissolve`，匹配声道的原声用 `Cross Fade (+3dB)`；fadeblack 或无法可靠交接的原声音轨组合改用短转场段烘焙。 |
| `native` | 只接受有明确映射的原生叠化；无法准确交接时拒绝，要求使用 auto/bake。 |
| `bake` | 把所有转场各自渲染为短视频/音频段，原始镜头的非重叠主体仍独立引用原素材。 |

自动烘焙会保留精确时间线：上一镜头主体、短转场段、下一镜头主体相接。源镜头主体仍可编辑，局部转场的内部效果是烘焙结果；不能把它说成可调参数的原生转场。音乐保持独立音轨，文字保持上层图片图层，避免把所有内容压成一整段不可拆的电影。烘焙模式需要 FFmpeg，可显式 `--ffmpeg`，或配置 `SHADOW_FFMPEG`；ffprobe 同理使用 `SHADOW_FFPROBE`。

XML 保留独立源 FPS／序列 FPS，源音频按声道拆轨并链接；报告中的 `xml_clip_timing` 列出实际源帧入出点。FCP7 用整数 timebase 加 NTSC 标记表达精确帧率，不可表示的源 FPS 会提醒核对或先转换。XML 目前要求方形像素源，拒绝有歧义的多视频流、多音频流选择。预览可以规范非方形像素，也会对交错源发出需选择去隔行方法的提示。

PNG 字幕图层和独立 SRT 是两种交接材料。若在 Premiere 中导入原生 SRT 字幕后继续使用它，应隐藏或删除对应 PNG 字幕层，避免重复显示；标题层可保留。当前脚本不自动猜测怎样替代用户的原生字幕设计。

生成成功不等于 Premiere 已验收。脚本始终标注 `premiere_import_verified: false`；实际 PR 导入、媒体链接、叠化效果、左右声道、单声道居中、增益、图片透明度、字幕、保存 `.prproj` 和最终播放应由执行工作流核对。原生 XML 转场写法参考 [Apple FCP7 编码说明](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/FinalCutPro_XML/Basics/Basics.html)，转场和音频翻译仍须参照 [Adobe XML 交接说明](https://helpx.adobe.com/premiere/desktop/render-and-export/export-files/export-a-project-as-a-final-cut-pro-xml-file.html) 及实际软件验证。
