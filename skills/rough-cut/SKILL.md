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

## 流程（六步）

1. **清点**：`& $py <root>\tools\roughcut.py scan --source <素材目录>`，读产物 `scan.json`（时长以视频流为准）。
2. **命名推断**：读文件名列表，推断「镜头 ↔ take ↔ 分段」的组织关系，写 `grouping.json`（见下方字段速查）。每个推断写 evidence；判不准的进 `unresolved`，绝不硬猜。
3. **看片建卡**：`& $py <root>\tools\roughcut.py frames --grouping <out>\grouping.json`，生成同镜 take 并排的联系表；用 `view_image` 逐张看，对照剧本/分镜记录：头尾稳定性、中段崩坏、人物/服装一致性、动作完成度、情绪匹配。
4. **逐镜决策**：按规则集为每个 shot 定首选 / 备选 / 取用区间 / 理由 / 置信度，写 `roughcut.json`；`& $py <root>\tools\roughcut.py report --plan <out>\roughcut.json` 生成人读 `roughcut.md`。
5. **校验**：`& $py <root>\tools\roughcut.py validate --grouping <...> --plan <...>`，必须 0 error 才能继续。
6. **交付**：把待确认清单给用户过目；`& $py <root>\tools\roughcut.py build --plan <out>\roughcut.json [--name <草稿名>]` 建草稿；让用户在剪映验收：镜序、取用区间、无黑帧、叠化正确。

## 硬规则

- 不许无 evidence 硬猜；不确定进 `unresolved` / `review`。
- 不许跳过 validate；校验有 error 时不许 build。
- 源素材只读：不修改、不删除、不重命名。
- 改草稿前确认剪映已退出目标草稿（否则剪映会用内存版本覆盖）。
- 改过 plan 后重建要换新草稿名，不覆盖已交付的草稿。
- 判断结论必须落盘：每个镜头的 `reason` / `confidence`、每个落选 take 的备注。

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
  "unresolved": [{ "file": "1.mp4", "reason": "纯序号无法判断镜号" }],
  "excluded": []
}
```

`roughcut.json`（edit plan）：每镜必填 `shot` / `segments` / `reason` / `confidence` / `alternates` / `transition_out` / `review`；`segments` 字段是 `file` / `source_start` / `duration`；`confidence < 0.6` 必须写 `review`；顶层 `canvas` 必填，可选 `subtitles` / `bgm` / `voiceover` 透传块与 `source_audio`（`keep` / `mute`）。

## 失败时

- validate 报 coverage 类错误：说明分组表漏了素材或重复覆盖，先修 `grouping.json` 再重跑。
- validate 报「超出视频流时长」：改小 `duration` 或把 `source_start` 前移（容器时长不可信）。
- build 报草稿已存在：换 `--name`，或确认后加 `--existing`。
