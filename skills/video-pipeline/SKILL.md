---
name: video-pipeline
description: 把剧本/分镜和素材组装成剪映草稿：生成分镜表 JSON、用 build_draft.py 建草稿、doctor 体检、交付验收。当用户说"把这一集做成剪映草稿""按剧本把素材排好""生成草稿"时使用。
---

# 剧本 → 剪映草稿流水线

和 `analyze-video` 是配套技能：先用它给素材打标，再用本技能选片排片。

## 依赖路径

```powershell
$py   = "__KIT_ROOT__\.venv\Scripts\python.exe"
$tool = "__KIT_ROOT__\tools\build_draft.py"
```

草稿目录：`%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`

> **剪映不在默认位置时**：安装器会把探测到的实际路径写进 `__KIT_ROOT__\jianying-paths.json`
> （`JIANYING_INSTALL_ROOT` / `JY_INSTALL_DIR` / `JIANYING_DRAFT_ROOT`）。
> `build_draft.py` 和 `subtitle_tools.py` 会自动读取它；如果你直接调 `jianying_tool.cli`，
> 先按这个文件里的值设置同名环境变量，否则工具会去找默认路径。

## 流程

1. **读剧本**，把镜头拆成一串可拍的单元（一镜一条）。
2. **对照素材索引**（没有就先跑 `analyze-video`），把素材映射到镜头，顺序不对要指出来。
3. **读真实时长**：用 `av` 取每个文件的**视频流时长**。
4. **写分镜表 JSON**，放到 `__KIT_ROOT__\shotlists\<集号>.json`。
5. **建草稿**：
   ```powershell
   & $py $tool --shotlist <分镜表>
   # 草稿已存在（比如先让剪映建了空草稿）时：
   & $py $tool --shotlist <分镜表> --existing
   ```
6. **体检**：`& $py -m jianying_tool.cli doctor "<草稿目录>"`，必须 `"ok": true`。
7. **交付**：让用户在剪映里打开验收（顺序、黑帧、音画同步）。

## 分镜表 JSON

```json
{
  "draft_name": "我的短剧 第1集",
  "canvas": { "width": 1280, "height": 720, "ratio": "16:9", "fps": 24 },
  "video_track": "main_video",
  "shots": [
    { "file": "E:\\素材\\01.mp4", "duration": 12.041667, "target_start": 0.0 },
    { "file": "E:\\素材\\02.mp4", "duration": 6.041667,  "target_start": 12.041667 }
  ],
  "subtitles": { "srt": "ep01.srt", "offset": 0 },
  "bgm": { "file": "bgm.m4a", "volume": 0.35, "fade_in": 1, "fade_out": 2 }
}
```

镜头字段：`source_start`（从第几秒起取）、`duration`（取多长）、`target_start`（放到时间线第几秒）、`speed`（变速）。
`bgm.source_duration` 不写时自动裁到与画面等长。

## 已踩过的坑（必须遵守）

- **时长必须 ≤ 视频流时长**。容器常报得比视频流长（如 289 帧 @24fps：视频流 12.041667s，容器 12.10s），
  按容器值写会报 `截取的素材时间范围超出素材时长`，草稿建到一半失败。
- **同名草稿不能重复创建**：`build_draft.py` 会拒绝，改用别的名字或 `--existing`。
- **草稿索引 `root_meta_info.json` 由剪映独占**，脚本不写它。剪映运行时新建的草稿文件夹会被自动登记；
  剪映关闭时创建的，需要用户在剪映里刷一次草稿首页，或先在剪映建一个空草稿再用 `--existing` 填充。
- **写入前确认剪映没打开目标草稿**，否则剪映内存里的版本会覆盖脚本的修改。
- 卡片时长靠脚本的 `sync_card_meta` 同步；用户打开一次草稿后剪映也会自己刷新。

## 字幕与配音

- 字幕：剪映里点一次「识别字幕」后，用 `subtitle_tools.py dump/apply` 批量校正（保留样式与时间轴）。
- 配音：`jianying_tool.cli voiceover --draft <草稿> --script <台词文件> --subtitles`（edge-tts，需联网）。
- 逐段微调：`jianying_tool.cli` 的 `edit-segment / split-segment / transition / keyframe / text-style` 等命令。
