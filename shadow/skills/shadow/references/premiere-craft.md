# Premiere 实操：从选片到交付前检查

按当前任务查一张卡，无需通读。PR01–PR06 是现有技巧的操作补充，不增加技巧库计数。阅读日期：2026-10-09；证据为 Adobe 官方公开网页正文，共 **12 页、6 个主题**，全部实读，未将搜索索引或视频标题计入。未播放教学音频，未在用户的 Premiere 中执行或验证这些操作。

| 当前问题 | 使用卡片 | 对应既有技巧 |
| --- | --- | --- |
| 从长素材挑有效片段、精修切点 | PR01 | S04、C02、C06、D01 |
| 同一事件有多个机位和独立录音 | PR02 | C02、C04、A01 |
| 混合 Log/普通素材、镜头切换跳色 | PR03 | V01、V02 |
| 对白有噪声、嗡声、齿音或混响 | PR04 | A01、A09 |
| 对白被音乐掩盖 | PR05 | A05、A08 |
| 语音生成字幕、校稿、统一文字层级 | PR06 | S04、T01、T02、T03 |

**执行边界。** 以下面板操作属于 Premiere 原生能力，shadow 当前脚本未自动实现多机位同步、色彩管理/匹配、对白修复、动态 Ducking、Premiere 转录及原生文字样式。现有 [timeline-v2](timeline-v2.md) 可保存已选镜头的入出点、硬切、有限转场、静态音量、PNG 标题和定时字幕，交接 XML/SRT；PNG 不能当作原生可改字标题。脚本生成成功仍保持 `premiere_import_verified: false`。只有实际检查工程后才可更改验收结论；静音工作时将试听项留为待验收。

下列“正文依据”是来源事实；“shadow 应用”是面向任务的迁移建议。界面名称按实读文档记录，运行时核对用户安装版本、语言和快捷键映射。

## PR01 — 三点选片与精剪

**触发与输入：** 已有故事骨架，需从长素材选段或填补已知时长；提供目标序列、目标轨道和可用源素材余量。

**来源 1｜主页面：** Adobe [Three-point edits](https://helpx.adobe.com/lt/premiere-pro/how-to/three-point-edits.html)，讲解者 Maxim Jago；页面未显示发布日期。实读正文范围：源/序列入出点、Insert/Overwrite、反向三点、Source Patching。

- **正文依据 → 操作：** ① Source Monitor 设 In/Out；② Timeline 定序列 In，或用播放头作落点；③ 核对 Source Patching 的音/视频和目标轨；④ 用 Insert 或 Overwrite。也可先定序列两端，再定源的一端以填满时段。
- **shadow 应用 → 检查/避用：** 覆盖采访的补充画面可只接视频，保留主录音。检查首尾动作、目标轨、是否误覆盖后续内容。已定四点却时长冲突时先重选范围，不擅自变速；脚本只承接选好的入出点。

**来源 2｜精剪补充：** Adobe [Perform rolling edit](https://helpx.adobe.com/premiere/desktop/edit-projects/trim-clips/perform-rolling-edits.html)，更新 2025-08-22。实读正文范围：滚动编辑工具、相邻剪点、链接音视频修饰键。

- **正文依据 → 操作：** ① 选 Rolling Edit Tool；② 拖动相邻两镜头边界，同时改前镜出点与后镜入点，维持两者总长。
- **shadow 应用 → 检查/避用：** 用于总时长已定的动作接剪；逐帧比较动作相位并核对口型。两侧必须有足够余量；需改变后续整体位置时另选编辑方式。默认 N 键须核对个人映射。

## PR02 — 多机位同步与切换

**触发与输入：** 同一事件的多个机位；需共同时间码、可对应的同期声或共同标记，并明确采用哪一路主录音。

**来源 3｜主页面：** Adobe [Create a multi-camera source sequence](https://helpx.adobe.com/au/premiere/desktop/edit-projects/set-up-multi-camera-sequences-for-editing/create-a-multi-camera-source-sequence.html)，更新 2026-01-21。实读正文范围：创建、同步选项、序列预设、音频设置及 Processed Clips。

- **正文依据 → 操作：** ① Project 选素材；② Clip > Create Multi-Camera Source Sequence；③ 按证据选 Timecode、Audio、In/Out 或 Clip Marker；④ 选择音频模式。Camera 1 可固定主音频；Switch Audio 才随角度切音频。
- **shadow 应用 → 检查/避用：** 先确定主录音，复核开始、中间、末尾的口型/拍板及未进入 Processed Clips 的素材。无共同同步依据先人工定位；创建成功不代表全片同步。脚本尚未实现此步骤。

**来源 4｜切换补充：** Adobe [Create and edit a multi-camera target sequence](https://helpx.adobe.com/in/premiere/desktop/edit-projects/set-up-multi-camera-sequences-for-editing/create-and-edit-a-multi-camera-target-sequence.html)，更新 2025-10-19。实读正文范围：目标序列、Multi-Camera View、角度切换与切点微调。

- **正文依据 → 操作：** ① 从多机位源执行 New Sequence from Clip；② Program Monitor 按钮编辑器加入并启用 Multi-Camera View；③ 播放时用角度数字键切换；④ 回到时间线换角度或滚动修剪。
- **shadow 应用 → 检查/避用：** 依说话者、动作和反应切换，检查轴线与连续主声轨；无新信息不频繁换机位。静音条件只检查画面与同步标记，声音连续性待试听。

## PR03 — 先管理色彩，再匹配镜头

**触发与输入：** 混合 Log/RAW/普通素材或曝光色温跳变；先收集机型、拍摄色彩空间、既有 LUT/调色及 SDR/HDR 交付目标。

**来源 5｜主页面：** Adobe [Configure sequence Color Management](https://helpx.adobe.com/premiere/desktop/correct-color/set-up-color-management/configuring-sequence-color-management.html)，更新 2026-01-07。实读正文范围：序列 Color 设置、Color Setup、输出空间及禁用色彩管理。

- **正文依据 → 操作：** ① 新建序列的 Color，或 Sequence > Sequence Settings 的 Color；② 按素材和输出目标选择 Color Setup；③ 核对工作与输出空间及色调映射。各序列可独立配置。
- **shadow 应用 → 检查/避用：** 先检查输入识别和转换，再调风格，避免重复套转换 LUT；检查高光、肤色及 SDR/HDR 输出。本文对默认项分别写 Broadcast 709 与 Direct 709，故不硬编码默认名称；旧工程先复制序列验证。脚本未实现。

**来源 6｜匹配补充：** Adobe [Match color between shots](https://helpx.adobe.com/sg/premiere/desktop/correct-color/add-color-effects/match-color-between-shots.html)，更新 2026-04-22。实读正文范围：Comparison View、参考帧、Face Detection、Apply Match 与覆盖提示。

- **正文依据 → 操作：** ① 开 Comparison View；② 选代表性的参考帧与目标镜头；③ 按主体决定 Face Detection；④ Apply Match 调整色轮及饱和度。
- **shadow 应用 → 检查/避用：** 先保存已有调色，再匹配正常肤色/中性物，检查曝光、白平衡和切换一致性。重新匹配可能覆盖已有 Lumetri 参数；不同时间地点的合理光线差异不应抹平。自动结果仍须逐镜修正。

## PR04 — 对白修复

**触发与输入：** 对白噪声影响理解；需要原始对白轨、问题时间段和同场干净片段作比较。

**来源 7｜主页面：** Adobe [Repair dialogue](https://helpx.adobe.com/in/premiere/desktop/add-audio-effects/adjust-volume-and-levels/repair-dialogue.html)，更新 2026-01-21。实读正文范围：Essential Sound > Repair 的降噪、低频隆隆声、电流声、齿音和混响工具。

- **正文依据 → 操作：** ① 在 Essential Sound 的 Repair 勾选所需处理；② 对应噪声选 Reduce Noise/Rumble、DeHum、DeEss 或 Reduce Reverb；③ 小量调整强度后复查。DeHum 区分 50/60 Hz，降噪有音质取舍。
- **shadow 应用 → 检查/避用：** 先诊断再处理，保留原轨，对比开关前后，检查金属感、吞字和尾音。不要全项拉满或把严重削波当成可恢复原声；静音时只准备参数，听感未验证。脚本未实现。

## PR05 — 对白优先的音乐 Ducking

**触发与输入：** 对白与音乐同时存在且互相掩盖；先稳定对白剪辑，明确 Dialogue/Music 轨和需要保留的音乐落点。

**来源 8｜主页面：** Adobe [Automatically duck audio](https://helpx.adobe.com/uk/premiere/desktop/add-audio-effects/adjust-volume-and-levels/automatically-duck-audio.html)，更新 2026-01-21。实读正文范围：音频分类、Ducking 控件、Generate Keyframes 及覆盖提示。

- **正文依据 → 操作：** ① Essential Sound 分类 Dialogue/Music；② 音乐启用 Ducking，选 Duck Against；③ 调 Sensitivity、Duck Amount、Fade Duration/Position；④ Generate Keyframes，再局部修关键帧。
- **shadow 应用 → 检查/避用：** 检查句首前是否及时降低、句尾是否被抬升掩盖、句间是否忽高忽低；试听待实际允许时完成。重新生成会覆盖手调关键帧，先保存版本。纯音乐 MV 不套对白闪避；脚本只有静态 gain，未实现包络。

## PR06 — 转录、字幕校稿与文字层级

**触发与输入：** 需要可读且同步的字幕；准备最终对白音轨、语言、用户确认文稿/专名表、成片尺寸及字体。

**来源 9｜识别补充：** Adobe [Auto transcribe video using Speech to Text](https://helpx.adobe.com/premiere/desktop/add-text-images/insert-captions/auto-transcribe-video-using-speech-to-text.html)，更新 2026-06-02。实读正文范围：Text > Transcript 的静态转录、语言、说话者、音轨与范围设置。

- **正文依据 → 操作：** ① Window > Text，在 Transcript 菜单 Generate Static Transcript；② 设语言、说话者和要分析的对白/音轨；③ 确认全片或 In/Out 范围后 Transcribe。
- **shadow 应用 → 检查/避用：** 指定实际对白，避免把音乐轨当输入；核对语言包及识别范围。静态转录后若再次剪片，重建或重新校准时间；不能把获得文字当成听校完成。此转录由 Premiere 原生执行。

**来源 10｜校稿补充：** Adobe [Find and replace text in transcript](https://helpx.adobe.com/premiere/desktop/add-text-images/insert-captions/find-and-replace-text-in-transcription.html)，更新 2025-08-22。实读正文范围：查找、逐项定位、Replace 与 Replace all。

- **正文依据 → 操作：** ① 搜索错词；② 逐项定位上下文；③ Replace，确认全部同义误识别才 Replace all。
- **shadow 应用 → 检查/避用：** 按确认文稿校正人名、数字和否定词；文稿与实说不同时标注差异，不能伪装成逐字一致。歧义同音词不批量盲替换；文字修订不等于修改实际录音。

**来源 11｜主页面：** Adobe [Create captions](https://helpx.adobe.com/premiere/desktop/add-text-images/insert-captions/create-captions.html)，更新 2026-01-07。实读正文范围：Captions and Graphics 工作区、转录生成字幕、格式、样式、长度/时长/间隔及行数。

- **正文依据 → 操作：** ① 校完转录后进入 Captions and Graphics；② Captions > Create captions from transcript；③ 设格式、样式、长度、时长、间隔和一/两行；④ 创建字幕。
- **shadow 应用 → 检查/避用：** 按语义断句，逐条检查起止与阅读时间；先验人名数字和跨剪点字幕。导入 shadow 的 SRT 并采用原生字幕时隐藏重复 PNG 字幕层。字幕生成不证明全片同步，仍需验收。

**来源 12｜样式补充：** Adobe [Create styles for captions](https://helpx.adobe.com/premiere/desktop/add-text-images/insert-captions/create-styles-for-captions.html)，更新 2026-01-07。实读正文范围：Properties 的文字/外观/对齐、标题 Linked Style、字幕 Track Style 与保存。

- **正文依据 → 操作：** ① 选时间线文字；② Properties 调字体、填充/描边和位置；③ 标题用 Linked Style、字幕用 Track Style，保存并应用统一样式。
- **shadow 应用 → 检查/避用：** 一屏一个主标题，字幕与辅助说明分层级；在目标尺寸检查可读性、安全区和主体遮挡。不同角色文字不盲套同样式；本文按当前 Properties 界面，旧版面板需核对。PNG 改字仍须重生。
