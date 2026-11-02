---
name: template-batch
description: 用一个现成草稿当模板批量出片（剪同款）：先列槽位，再写清单，跑 tools/template_fill.py 生成 N 个草稿并对账。当用户说"按模板批量出片""剪同款""用这个模板出 10 集"时使用。
---

# 模板批量出片（剪同款）

把一集精修好的草稿当模板，批量复制成 N 份并逐份替换素材与文案。模板里的样式、特效、
轨道结构原样保留。批量走脚本；只改一两处的细活直接调 MCP 工具（`duplicate_local_draft`、
`import_local_track`）。

## 路径解析

本 Skill 所在目录向上两层即插件根（记为 `<root>`）。运行时信息在
`%USERPROFILE%\.jianying-kit\runtime.json`：

```powershell
$rt = Get-Content "$env:USERPROFILE\.jianying-kit\runtime.json" -Raw | ConvertFrom-Json
$py = $rt.python
$fill = "<root>\tools\template_fill.py"
```

`runtime.json` 不存在时先跑 `setup` 技能。脚本会自己读 `jianying-paths.json`
（剪映装在非默认位置、草稿目录被改过时必需），不用手设环境变量。

## 流程

1. **确认模板**：跟用户对齐用哪个草稿当模板（`list_local_drafts` 可以列出来）。
2. **列槽位**：调 `list_template_slots`，看清每条轨道、每个片段和现有文案。
   `replaceable: false` 的轨道（特效、滤镜、贴纸）不能换；`ambiguous_name: true`
   说明有同名轨道，清单里引用它会直接报错，必须先让用户在剪映里改名。
3. **写清单**：按用户给的素材与文案填 `rows`，存成 JSON（相对路径按清单所在目录解析）。
4. **先 dry-run**：`& $py $fill --plan <清单> --dry-run`，把报告里的槽位核对给用户看，
   特别是"仍保留模板原样"的那些。
5. **真跑**：去掉 `--dry-run`。跑之前确认剪映没开着模板草稿或同名目标草稿。
6. **交付**：报告里逐行给出草稿名与警告；让用户在剪映草稿首页验收（可能需要刷新列表）。

## 清单字段

| 字段 | 说明 |
| --- | --- |
| `template` | 模板草稿名（草稿目录下的文件夹名），必需 |
| `rows[].name` | 新草稿名；不写时用 `<模板名>-<行号>`（行号从 1 开始） |
| `materials.by_name` | 素材名 → 新文件路径：所有引用该素材的片段一起换（只支持视频/图片，且新素材不能比引用它的片段短） |
| `materials.by_slot[]` | `track` + `segment` + `file`，可带 `source_start` / `duration`（秒）：逐镜换素材并重新指定取用范围 |
| `texts` | 轨道名 → 按片段顺序排列的新文案数组；条数必须与该轨道片段数一致 |
| `defaults` | `shrink`（默认 `cut_tail`）/ `extend`（默认 `cut_material_tail`）：新素材时长不匹配时的处理策略 |
| `require_all_slots` | 设为 `true` 时，清单没覆盖到的槽位会让该行失败 |

## 报告怎么读

- 退出码 `0` 全部成功、`1` 有行失败、`2` 清单/模板级别的错误（整批没跑）。
- 每行有 `replaced` 计数、`warnings`（例如片段被裁短）、`unfilled_slots`（还保留模板原样的槽位）
  与 `unreferenced_materials`（替换后不再被任何片段引用的旧素材，留着不删，只是提醒）。
- `warnings` 里出现"时长由 X 调整为 Y"说明新素材比原片段短，片段被裁短了；要保留原时长就换个
  更长的新素材，或改用 `by_slot` 显式给 `source_start` / `duration`。
- 失败的常见原因：素材路径不存在、文案条数对不上、目标草稿已存在、同名行、同名轨道、
  片段下标越界、把 `by_slot` 用在文本轨上、模板的根文件与活动时间线不一致。

## 不要做的事

- 别在剪映开着模板或目标草稿时跑：剪映退出时会用内存里的版本覆盖脚本写入。
- 别为了让报告变绿就把所有槽位都填上：`unfilled_slots` 只是提醒，不填就是保留模板原样。
- 别手写 `draft_content.json`：草稿是密文，一律走脚本或 MCP 工具。
- 别在失败后让脚本删草稿目录：剪映的草稿索引由剪映独占，脚本删目录会留下"草稿已损坏"的残留条目；
  要删就让用户在剪映里删。
- 用到**文字模板（花字）**的草稿要提前说明：那种片段一段里含多段文本，清单每片段只能给一段文案，
  脚本会直接报错而不是悄悄留下模板原文；让用户先在剪映里把它改成普通文本。
