# Shadow 本地素材库

用户只要求“给原始视频按内容分类编号，放到指定文件夹”时，先用 [分类整理](organize.md)。它会真实抽帧供 Codex 查看，再复制成分类目录并输出原名对照；本页的数据库归档用于后续检索和剪辑衔接。

每个视频项目持有独立 SQLite 库与清楚的目录。整理应在正式剪辑前进行：把素材来源、技术信息、观看后的内容、标签、候选状态与选镜用途记录下来，后续按现成记录检索并组时间线，避免反复从文件名猜画面。

## 项目与状态

`assets.py init PROJECT` 创建 `.shadow-project.json`、`library.sqlite3` 和标准目录：`staging/downloads`、`staging/extracted`、`media/video`、`media/audio`、`media/image`、`rights`、`previews/contact-sheets`、`manifests`、`notes`、`projects`、`exports`。原素材默认复制，`--mode reference` 可按引用导入；两种方式都不移动或覆盖用户源文件。复制过程校验 SHA256，相同内容只存一个主素材记录，同时保留每次来源与搜索记录。引用文件改动/移走后必须重新检查。

| 字段 / 状态 | 实际含义 |
|---|---|
| candidate | 浏览器找到页面，未必已经下载；没有可用媒体文件 |
| downloaded | 已得到完成的媒体文件并导入；技术探测和观看状态独立记录 |
| verified | 文件存在，有明确观看、试听或用户核验依据；不能用页面标题代替 |
| selected | 已作选镜决定；可同时仍未观看，必须看 `review_evidence` 而不只看状态 |
| media_role=source | 原素材；先核对内容，再按需选用 |
| media_role=preview | 预览、样机或水印示例；不默认替代原片进入成片 |
| media_role=reference | 用作参考的素材，需要明确用途 |
| technical_metadata | ffprobe 得到时长、分辨率、编码、帧率与音轨信息；不描述画面内容 |
| semantic_summary / review_evidence | 记录“看到了什么”与依据，区分文件元数据、用户描述、实际观看与实际试听 |

下载一个 stock pack 可能同时有原片、预览片、封面、样机和授权附件。文件名含“预览 / 样机 / preview / watermark”会暂标 preview；这是文件标签判断，需要观看核对，不是画面识别。可用 `import --role source|preview|reference` 明确设置。特别检查“预览视频.mp4”和真实原片的差别。

## 常用命令

以下 `PROJECT` 是本次项目的绝对路径，`SCRIPT` 为此技能的 `scripts/assets.py`。Windows/PowerShell 中给中文或带空格路径加引号。命令输出 JSON；文件、探测或校验错误返回非零，不把错误包装成成功。Python 使用 UTF-8：

```powershell
python -X utf8 SCRIPT init PROJECT
python -X utf8 SCRIPT candidate PROJECT --origin baotu --page-url "https://ibaotu.com/sucai/ITEM.html" --item-id "ITEM" --title "页面标题" --query "搜索关键词" --entitlement-note "页面可见的会员与下载资格"
python -X utf8 SCRIPT import PROJECT "D:\素材\人物走进画面.mp4" --tag 人物 --tag 进入 --origin user
python -X utf8 SCRIPT import PROJECT "D:\大素材\原片.mp4" --mode reference --tag 航拍
python -X utf8 SCRIPT search PROJECT --query 日出 --category video --status downloaded
python -X utf8 SCRIPT annotate PROJECT ASSET_ID --summary "航拍山脊，太阳从远处地平线升起，镜头缓慢前移" --evidence viewed --note "已看实际原片 0–11 秒" --tag 建立镜头 --tag 日出 --verify
python -X utf8 SCRIPT use PROJECT ASSET_ID --in 1.2 --out 6.2 --order 1 --track V1 --purpose "前奏建立环境"
python -X utf8 SCRIPT check PROJECT --rehash
python -X utf8 SCRIPT export PROJECT
python -X utf8 SCRIPT summary PROJECT
python -X utf8 SCRIPT shotlist PROJECT
```

`candidate` 返回页面记录 ID；实际下载后，按准确的完成文件路径 `import ... --candidate-id ID` 关联。若文件与已有素材内容重复，候选来源/标签会合并到主素材，返回新的主 ID；后续命令使用返回的 `asset_id`。页面候选不能直接选入时间线。

`search` 查询文件名、语义摘要、标签、页面标题、查询词和 item ID；关键词是字面匹配，不是向量搜索或自动语义分析。多个 `--tag` 要同时满足。可筛选 category（video/audio/image/other）和状态。实际观看后应写明主体、动作、地点/环境、景别、镜头运动、情绪/用途以及合适片段时间；不知道的字段不编造。

`use` 记录源入出点、轨道、排序与用途，可用 `--timeline-in` 指定秒数；没有指定时生成 shotlist 按同轨排序顺排。检查源选段不超过已探测时长。图像的入出点表示使用时长，后续时间线工具仍须明确支持图片。它没有执行转场、变速、字幕或混音。当前没有“删除选镜”命令；需要重排可输出 shotlist 后在剪辑计划里修订。

## 包图网会员下载记录

通过用户已经登录的浏览器进行实际搜索与下载，使用用户既有会员权益。记录页面 URL、item ID、标题、query、页面可见授权说明、实际下载时间与账户资格的文字说明；不保存密码、cookie、token 或完整浏览器会话。`--downloaded-at` 由实际下载事件提供；未观察到时保留未知，`recorded_at` 单独表示入库时间，不冒充下载完成时间。若仅能观察下载文件的mtime，可明确标注该依据，不能写成浏览器事件。下载资格与商业用途范围不是同一个字段，分别按实际页面信息记录。无需为已授权的下载额外重复提出版权确认。

浏览器下载完成事件返回的路径优先用于 `import` / `extract-zip` / `extract-rar`，不要按最近修改时间盲猜。若浏览器把文件放在用户 Downloads，先在该完成路径上导入；不改用户浏览器全局下载设置。下载未完成、额度/权限限制、登录过期、unexpected purchase 或访问屏障由浏览器流程处理；文件工具不会绕过这些限制。

项目 `staging/downloads` 仅供已知属于本项目的下载临时存放。`import-staging` 只读该目录第一层的支持媒体后缀，忽略 `.crdownload/.part/.partial/.tmp/.download` 和 archive。关联一个候选时目录必须恰好只有一个完成媒体文件，否则报错让调用者选择确切文件；批量 import-staging 的同一来源字段只有在这些文件确属一个素材包时才可使用，不能把多个不同页面来源混在一起。

## ZIP 与 RAR 素材包

```powershell
python -X utf8 SCRIPT extract-zip PROJECT "C:\Downloads\素材包.zip" --origin baotu --page-url "https://ibaotu.com/sucai/ITEM.html" --item-id "ITEM" --title "实际页面标题" --query "实际查询" --license-note "实际页面授权说明" --entitlement-note "实际会员资格"
python -X utf8 SCRIPT extract-rar PROJECT "C:\Downloads\素材包.rar" --origin baotu --page-url "https://ibaotu.com/sucai/ITEM.html" --item-id "ITEM" --unrar "C:\Program Files\WinRAR\UnRAR.exe"
```

ZIP 使用 Python 标准库；RAR 需要现有可信 UnRAR。RAR 先列出全部成员与解压大小，当前可解析 Windows UnRAR 的中文/英文技术列表；其它无法解析的工具/语言报错，不能盲目解压。随后通过 `p` 输出逐个成员字节，由脚本写入已检查的项目目录，防止 archive 自定写入路径。RAR 超时或 CRC 失败会报错。不会运行素材包中的任何文件。

两者先验证全体成员路径、成员总数、压缩比与展开体积，拒绝 `..`、绝对路径、盘符、Windows 特殊路径、链接/路径碰撞。默认保护值为至多 500 成员、总展开 2048 MiB、单文件 1024 MiB、压缩比 200；大型正常 stock pack 可以按实际用途显式提高参数，但不能取消路径校验。只有支持的视频、音频、图像会进入媒体库；名字明确为授权/版权/license/copyright/rights 的 `.txt/.pdf/.doc/.docx` 附件留存到 `rights`，不打开、不执行、不据此自动宣称授权已验证。其 SHA256、原 archive 和成员路径保存在独立附件表。其它成员跳过并报告。

## 看图与摘要

```powershell
python -X utf8 SCRIPT contact-sheet PROJECT ASSET_ID --count 8 --columns 4
```

使用实际 FFmpeg 提取不同源时间点，生成帧 JPEG、拼接 contact-sheet.jpg 和 timestamps.json。每个样本记录所请求的源秒数及图片路径；解码的最近可用帧可能略有不同，不称为逐帧精确定位。可以用 `--start/--end` 限定范围，未知时长必须给 `--end`。图像素材输出一张图，音频素材需要试听而非 contact sheet。片段取样不能代替看完整动作、音画同步或全部对白。

通过环境变量 `SHADOW_FFMPEG/SHADOW_FFPROBE`，或命令 `--ffmpeg/--ffprobe` 指定工具。PATH 和已存在 Topaz Video AI 工具目录为发现候选；不会安装或下载 FFmpeg。找不到 ffprobe 时仍可导入文件，但技术信息标 unknown。找不到 FFmpeg 时预览命令明确失败。生成预览不会自动把素材变为 viewed；AI 实际打开样本图/观看视频或用户明确核验后再 annotate。

`export` 产生 `manifests/assets.json`、UTF-8 CSV 与 `rights-attachments.json`；JSON 保留全部来源。`summary` 产生 `notes/asset-summary.md`，分别写文件可用性、技术探测、观看依据和未知项。`shotlist` 产生 JSON/CSV，字段包含 asset_id/media_path/in/out/timeline_in/track/sort_order/purpose/review_evidence，可交给主剪辑计划。摘要中未核验授权字段仅为记录缺口，不自动触发额外版权确认。

每次正式剪辑前 `check --rehash`，并查看所选素材的 role 与 review_evidence。文件存在/哈希正确并不能证明内容已观看、下载对应正确项目、授权解释正确或 Premiere 已导入成功。
