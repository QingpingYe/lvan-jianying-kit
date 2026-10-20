---
name: analyze-video
description: 对本地视频素材批量抽帧、生成联系表并逐条打标，产出素材索引（时长/分辨率/画面内容/建议用途），供选片和剪辑使用。当用户说"分析这些素材""给素材打标""看看视频里都有什么""选片"时使用。
---

# 素材分析（视频打标）

把一堆视频变成一份可检索的素材索引。**必须真看画面**，不许凭文件名猜内容。

## 依赖路径

```powershell
$py = "__KIT_ROOT__\.venv\Scripts\python.exe"
# ffmpeg 路径随版本变化，用下面这行现取，不要写死
& $py -c "import imageio_ffmpeg,sys; sys.stdout.write(imageio_ffmpeg.get_ffmpeg_exe())"
```

环境里还有 `av`（读元数据）、`Pillow`（拼图）、`faster-whisper`（转写，需另下模型）。

## 流程

1. **清点**：列出目标目录下所有视频文件，记录名字与大小。
2. **元数据**：用 `av` 读每个文件的**视频流时长**（`stream.duration * time_base`）、分辨率、帧率、有无音轨。
   注意：容器的 `format.duration` 常比视频流多 1 帧，剪辑时以视频流为准。
3. **抽帧**：每个视频在 10% / 50% / 90% 处各抽 1 帧，缩放到 480×270。
4. **联系表**：用 Pillow 拼成 4 列网格，每格左上角写文件名，输出 `contact-N.jpg`。一批不超过 12 个视频。
5. **看片**：用 `view_image` 逐张看联系表，为每个文件写：画面内容、景别、人物/动作、可用性。
6. **产出**：写 `assets-index.json` 与 `assets-index.md`。

## 输出格式

```json
{
  "root": "E:\\素材\\第1集",
  "generated": "2026-10-12",
  "clips": [
    {
      "file": "01.mp4",
      "path": "E:\\素材\\第1集\\01.mp4",
      "duration": 12.041667,
      "width": 1280, "height": 720, "fps": 24.0,
      "has_audio": true,
      "summary": "黄昏海面，渔船剪影，前景一只大红灯笼",
      "shot_size": "全景",
      "characters": [],
      "usable": true,
      "notes": "适合做开场空镜"
    }
  ]
}
```

## 注意

- 抽帧失败（损坏 / 无视频流）单独标 `"usable": false` 并写明原因，不要跳过。
- 素材文件名保持原样，不要批量重命名。
- 若要按台词选片，先做索引再转写（`jianying_tool.cli transcribe`），两件事分开做。
