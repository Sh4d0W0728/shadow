# Premiere 交付与精修

## 身份与工程

项目名称、作品/歌曲/说话人、音频与歌词、素材范围、时间线、字幕和成片保持一致。不把以前项目的音乐、比例、角色或工程当新任务的默认。

`shadow.py xml` 输出 FCP7 XML（xmeml v4），是 Premiere 可导入的交换文件。用实际 PR 导入并另存原生 `.prproj`；扩展名改名、只解析 XML 或发现 PR 安装路径均不能证明原生工程完成。

## v2 自动交接

- 连续剪辑、源媒体引用、入出点、序列尺寸/帧率、支持的音轨与音乐增益。
- `dissolve` 可输出原生 Cross Dissolve；需要可靠保留预览外观时选 `--transition-mode bake`，局部转场烘焙，镜头主体仍引用原素材。
- `--transition-mode auto` 对无法可靠原生交换的转场生成局部媒体。`fadeblack` 不冒充已支持的原生 PR 效果。烘焙文件跟随 XML 的 `.assets` 文件夹一起保存。
- 标题与字幕生成透明 PNG 和按帧放置的上层视频轨道，文字源保留在 `text-events.json`/时间线 JSON/SRT。PNG 的文字需修改源再生成，不是 PR 原生可编辑文字。
- 独立 SRT 可以导入 PR 为真正的字幕轨道，再改文稿、时间和样式。若启用 SRT 字幕轨，关闭 XML 自带字幕 PNG 轨，避免显示两套。

完整能力以 [timeline v2](timeline-v2.md) 和命令报告为准。没有实际实现的复杂效果继续由 PR 完成并验证。

## 实际 PR 验证

1. 在新工程或用户原工程副本中导入 XML；核对文件路径，不让自动搜索误链接到同名预览或旧版本素材。
2. 打开序列，查看总时长、画幅/帧率、视频轨/音轨、转场位置、标题与字幕图层。
3. 拖动播放头检查首、中、尾及转场中间；播放转场和语音片段，检查重影、黑帧、爆音、声音相位与同步。
4. 需要原生字幕时导入 SRT 并放到序列开头，检查第一句、剪口后的句子和末句。SRT 没有字体和位置样式。
5. 保存新 `.prproj` 后关闭并重开核对媒体。输出原生工程要连同全部依赖/图层/烘焙文件交付，不能只给一个依赖临时目录的工程文件。

支持格式见 [Adobe 字幕导入](https://helpx.adobe.com/premiere/desktop/add-text-images/insert-captions/import-caption-file-from-third-party-service.html) 与 [Adobe 字幕格式](https://helpx.adobe.com/ca/premiere/desktop/add-text-images/insert-captions/supported-file-formats-for-captions.html)。XML 交换可能改变部分效果，参见 [Adobe XML 限制](https://helpx.adobe.com/premiere/desktop/render-and-export/export-files/export-a-project-as-a-final-cut-pro-xml-file.html)。

## 原生精修范围

J/L-cut、多轨混音、自动 ducking、降噪、精细调色、遮罩、分屏、速度渐变、稳定和追踪需使用实际可用的 PR 功能，并逐段观看/试听。技巧库提供选用条件与检查点，不把技巧条目等同于自动效果代码。图层的淡入淡出若报告 XML 未迁移，须在 PR 中补回。

报告分别写：脚本结构验证、实际渲染/解码、视觉与声音抽查、PR 导入/保存/重开。未执行的检查如实保持待验证。
