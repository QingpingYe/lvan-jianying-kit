---
name: rough-cut
description: AI 短剧智能粗剪：清点一集素材、推断命名分组、看片挑 take、产出剪辑决策表（roughcut.json），校验通过后建剪映草稿。当用户说「智能粗剪」「AI 短剧粗剪」「挑 take」「把这一集素材粗剪成草稿」时使用。
---

# AI 短剧智能粗剪

把一集 AI 生成的短剧素材变成可直接在剪映精修的草稿。判断（命名推断、看片、take 择优、节奏）由你完成；清点、校验、转换、执行由 `tools/roughcut.py` 完成。动手前先读 `references/decision-rules-v0.1.md`（评分、裁切、节奏、转场、自检清单都在那里）。

## 路径解析

- 运行时信息在 `%USERPROFILE%\.jianying-kit\runtime.json`（不存在时先跑 `setup` 技能）。
- 本 Skill 所在目录向上两层即插件根 `<root>`；CLI 是 `<root>\tools\roughcut.py`。
- 素材目录由用户给出（一集一个文件夹）；产物默认写 `<素材目录>\roughcut\`。

## 流程（八步）

1. **清点**：`& $py <root>\tools\roughcut.py scan --source <素材目录>`，读产物 `scan.json`（时长以视频流为准）。
2. **命名推断**：读文件名列表，推断「镜头 ↔ take ↔ 分段」的组织关系，写 `grouping.json`（见下方字段速查）。每个推断写 evidence；判不准的进 `unresolved`，绝不硬猜。
3. **看片建卡**：`& $py <root>\tools\roughcut.py frames --grouping <out>\grouping.json`，生成同镜 take 并排的联系表；用 `view_image` 逐张看，对照剧本/分镜记录：头尾稳定性、中段崩坏、人物/服装一致性、动作完成度、情绪匹配。每条素材必查内部切点数量：默认 5 帧会漏掉文件内多个叙事区间；疑似含多段时用 `--count 12` 重抽再判断。
4. **音频核验（有对白/旁白必做）**：逐条跑 `& $py <root>\tools\roughcut.py transcribe --media <文件> --out <out>\transcripts\<名>.json --language zh --initial-prompt "<该镜剧本台词，空格分隔>"`（可选 `--srt`）。提示词直接给剧本原文，能把「膝下敬禀者 / 戚家军 / 治军极严」这类同音字错误压下去；输出用词级时间戳还原逐句区间。读文本对照剧本与该镜画面主体，标记：台词缺失、与画面不符、同一句重复、镜内顺序颠倒、旁白错位。结论写进对应 shot 的 `review`；需要重排声音时写 `audio_tracks`（不要只写 `audio_repair` 备注）。转写走 ffmpeg+numpy，不依赖 jianying-tool 的 PyAV 链路；首次会下载模型，或安装时用 `-PreseedAsrModel tiny` 预置。
5. **逐镜决策**：按规则集为每个 shot 定首选 / 备选 / 取用区间 / 理由 / 置信度，写 `roughcut.json`；`& $py <root>\tools\roughcut.py report --plan <out>\roughcut.json` 生成人读 `roughcut.md`（含备选 take、音频处理、计划警告与待确认清单；无剧本时自动声明「未对剧本验证」）。
6. **校验**：`& $py <root>\tools\roughcut.py validate --grouping <...> --plan <...>`，必须 0 error 才能继续。
7. **交付**：把待确认清单给用户过目；`& $py <root>\tools\roughcut.py build --plan <out>\roughcut.json [--name <草稿名>]` 建草稿。`--existing` 只保证往空草稿追加；剪映开着且目标草稿非空时工具会拒绝写入，必须先退出该草稿或退出剪映（确认风险才加 `--force`）。build 会写 `manifest.json` 并在写后核对段数与时间点（±1 帧），核对失败必须重建。
8. **音画审计**：`& $py <root>\tools\roughcut.py audit --plan <out>\roughcut.json --draft <草稿名> --transcript <out>\transcripts\<名>.json`；产出 `roughcut-audit.json` / `roughcut-audit.md`，逐句给出「台词 → 镜头 → 时间线」，并列出静音区间与分离音轨。audit 通过后再让用户在剪映验收：镜序、取用区间、无黑帧、叠化正确、口型与台词同画。

## 硬规则

- 不许无 evidence 硬猜；不确定进 `unresolved` / `review`。
- 不许跳过 validate；校验有 error 时不许 build。
- 源素材只读：不修改、不删除、不重命名。
- 改草稿前确认剪映已退出目标草稿（否则剪映会用内存版本覆盖）；插件会做写前拦截与写后核对，失败就重建，不用 `--force` 硬闯。
- 改过 plan 后重建要换新草稿名，不覆盖已交付的草稿。
- 判断结论必须落盘：每个镜头的 `reason` / `confidence`、每个落选 take 的备注。
- 同一镜头未入选的 take 必须逐条写进 `alternates` 并写 `note`；`alternates` / `transition_out` / `review` 三个键不许缺。
- 音画修复必须写进可校验字段：逐段用 `segments[].mute` / `segments[].volume`，分离音轨用顶层 `audio_tracks`；不要只写 `audio_repair` 自由文本。
- 同一素材要在两个镜头复用时，必须在 `grouping.json` 和 `roughcut.json` 都写 `media_reuse` 白名单。

## 字段速查

`grouping.json`：

```json
{
  "version": 1, "root": "E:\\素材\\第3集", "scheme": "ep-shot-take",
  "scheme_confidence": "high", "evidence": "文件名 03-05-02 推断为 集-镜-take",
  "shots": [
    { "shot": "05", "takes": [
      { "take": "01", "kind": "single", "files": ["03-05-01.mp4"] },
      { "take": "02", "kind": "segmented", "files": ["03-05-02-1.mp4", "03-05-02-2.mp4"] }
    ] }
  ],
  "media_reuse": ["10.mp4"],
  "unresolved": [{ "file": "1.mp4", "reason": "纯序号无法判断镜号" }],
  "excluded": []
}
```

`grouping.json` 约束：`scheme` / `scheme_confidence` / `evidence` 必填；`shot` 全局唯一、同镜 `take` 唯一；每条视频默认只能出现一次，确需跨镜复用时写进 `media_reuse` 白名单；`unresolved` / `excluded` 每条必须写 `reason`。

`roughcut.json`（edit plan）：每镜必填 `shot` / `segments` / `reason` / `confidence` / `alternates` / `transition_out` / `review`；`segments` 字段是 `file` / `source_start` / `duration`，可选 `mute`（布尔）与 `volume`（≥0）；未入选素材必须逐条进 `alternates` 且写 `note`；入选镜头 `confidence` 不得低于 0.6（低于 0.6 属淘汰档），0.6 至 0.75 必须写 `review`；`dissolve` 的 `duration` 必须是 0.3 至 0.8 秒；顶层 `canvas` 必填，可选 `subtitles` / `bgm` / `voiceover` 透传块、`source_audio`（`keep` / `mute`）、`media_reuse` 与 `audio_tracks`。

`audio_tracks` 每条：`file`（必填）、`duration`（必填，秒）、`track`（默认 `audio`）、`target_start`、`source_start`、`volume`、`fade_in`、`fade_out`、`speed`。同一 `track` 内片段不得重叠；不同轨道允许重叠，用来做 J-cut / 分离音轨对位。

## 失败时

- validate 报 coverage 类错误：说明分组表漏了素材或重复覆盖，先修 `grouping.json` 再重跑。
- validate 报「超出视频流时长」：改小 `duration` 或把 `source_start` 前移（容器时长不可信）。
- build 报草稿已存在：换 `--name`，或确认后加 `--existing`。
- build 报「目标草稿非空」：`--existing` 只支持空草稿；改内容请换新草稿名，不要靠 `--force` 叠加。
- build 报「剪映正在运行」或写后核对失败：先在剪映里关闭/退出该草稿（或完全退出剪映），再用新草稿名重建。
- edit-segment 报 overlaps：通常说明磁盘时间线与剪映内存里的工作副本不一致，不是可以直接忽略的误报；先用 `audit` 对照计划与草稿。
- transcribe 首次运行要下载模型：安装时加 `-PreseedAsrModel tiny`，或保证能访问 HuggingFace 后重跑；模型目录可用 `--model-dir` 指定。
- audit 报某段 source_start / target_start 超差：先确认是否在剪映里手工改过；手工改动要回写进 plan 或新建草稿。
