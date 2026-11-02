# 智能粗剪（AI 短剧素材）设计

日期：2026-11-02 · 目标版本：jianying-kit 0.7.0 → 0.8.0 · 状态：草稿（待用户确认）

## 1. 为什么做

插件现在两头都有、中间缺一块：

- 执行层完整：74 个 MCP 工具（片段、转场、特效、导出）、`build_draft.py`（分镜表 → 草稿）、doctor 体检。
- 理解层有一半：`analyze-video`（抽帧打标）、`transcribe_media`（本地转写）。
- 缺决策层：一集几十条 AI 生成片段摆在一起时，插件不知道保留哪条 take、从哪里取、怎么排、哪里用转场。

用户场景（2026-11-02 对齐）：

- 主体业务是 AI 短剧；一条素材对应一个镜头，同一镜头可能有多条 take。
- 一集的素材放在同一个文件夹；文件名有序号但**没有固定格式**：可能是纯数字（1、2、3…），
  可能是「集-镜-素材」，也可能是其他格式；同一镜头可能有多个文件（分段截取）。
- 命名格式需要 AI 看实际文件名推断。

参考（非依赖）：Rough-cut-wiki-video（剪映粗剪的 Agent Skill）确立的可借用原则：
edit plan 为唯一事实源、拿不准不猜进「待确认」、原素材只读、注册草稿前先校验；
RoughCut 的「编辑即数据、可撤销」思路。同类思路的完整清单见对话记录（2026-11-02）。

## 2. 目标

1. 一集素材（+ 剧本/分镜，可选）进，出两份产物：`roughcut.json`（剪辑决策表，edit plan）+ `roughcut.md`（人读报告）。
2. 命名推断：从不统一的文件名推断「镜头 ↔ take ↔ 分段」的组织关系，产出 `grouping.json`；
   推断结果可校对、可复用（重跑不重新猜）。
3. take 择优：同镜多 take 给首选 + 备选 + 理由（崩坏、人物一致性、动作完成度、情绪匹配）。
4. 裁切：给出取用区间（掐头去尾、避开崩坏段），区间必须合法（≤ 视频流时长）。
5. 组装：按剧本/分镜顺序排时间线；默认硬切，转场与特效只做标注；叠化可选执行。
6. 全程可审计：每个决定带证据与置信度；不确定项进「待确认」；校验通过才允许建草稿。

## 3. 非目标

- 不修改、不删除、不重命名任何源素材。
- v1 不自动加特效/滤镜/动画/蒙版（仅标注建议）；不自动选曲配乐（BGM 仍由现有 `bgm` 字段指定）。
- 不做剪映界面独占功能（识别字幕、文本朗读等）。
- 不引入新的重依赖（不装场景检测、不装视觉模型）；复用 `av` / `Pillow` / `faster-whisper`。
- 不做「删停顿/删口误」式语音粗剪（那是口播体裁的问题；AI 短剧对白走后期 voiceover）。
- 不做跨集素材调度。

## 4. 反目标（技术上跑通但视为失败）

- 静默错配：take 张冠李戴、镜序错乱，报告里看不出来。
- 无证据硬排：命名推断没把握时不标记，直接当确定结果使用。
- 越界产物：取用区间超出视频流时长、时间线重叠、黑帧，校验器没拦住。
- 不可追溯：入选/落选没有理由，用户无法按报告在剪映里换 take。
- 源素材被动过。

## 5. 约束

| 约束 | 影响 |
| --- | --- |
| 命名无固定格式 | 分组走「推断 + 校验 + 待确认」，不写死解析器 |
| 同镜可能多 take，也可能一条 take 分多段文件 | 数据模型同时表达 take 备选与分段接续 |
| AI 片段常见病：头尾不稳、中段崩坏、人物漂移 | 素材卡与审查要点围绕这些设计 |
| 一集几十条素材，看片成本随素材数线性涨 | 联系表（每张 ≤ 12 格）+ 同镜 take 并排对比控制成本 |
| 剧本/分镜可能缺失或很粗 | 允许无剧本模式：按文件名与画面自洽排序，并明确标注未对剧本验证 |
| 时长权威值是视频流时长 | 探测用 `av` 读视频流（容器常多 1 帧，这是既有坑） |
| 剪映草稿密文、剪映运行时不可写 | 沿用 native_codec 与「写入前退出剪映」机制 |
| 宿主加载的是插件缓存目录 | 开发在仓库，改完同步缓存并重启 Codex |

## 6. 方案选择（默认乙，待确认）

- 甲 · 纯技能：只加 `skills/rough-cut/SKILL.md`，零代码。快，但校验、转换、报告无法沉淀，跨会话复用弱。
- 乙 · 共享核心 + 双包装（选）：`tools/jy_roughcut.py` 承担确定性事务（清点、探测、分组校验、
  plan 校验、plan→shotlist、转场挂载、报告）；CLI 与 MCP 是薄包装；「判断」（命名推断、看片、
  take 择优）留在会话里由 Codex 多模态完成。结构对齐 template-batch。
- 丙 · 全自动本地 pipeline：转录 + 场景检测 + 本地视觉模型打分，端到端出草稿。
  链路最长、质量最不可控、依赖最重，不选。

选择理由：本任务难点在判断不在计算；判断留在会话可审计、可打断，事务沉到脚本保证确定性，
且零新增重依赖。用户对乙未提出异议（2026-11-02）。

## 7. 结构

```
jianying-kit/
├─ tools/
│   ├─ jy_roughcut.py        ← 新增：共享核心
│   ├─ roughcut.py           ← 新增：CLI 薄包装（scan / validate / build / report）
│   ├─ build_draft.py        （不动，被复用）
│   └─ ...
├─ mcp_ext/
│   └─ server.py             ← 改：+3 个工具
├─ skills/
│   └─ rough-cut/SKILL.md    ← 新增：命名推断、看片、edit plan、交付
├─ examples/
│   └─ roughcut.example.json ← 新增
├─ tests/
│   └─ test_jy_roughcut.py   ← 新增
├─ scripts/validate_plugin.ps1 ← 改：技能清单、新文件与 JSON 检查
├─ .codex-plugin/plugin.json ← 改：版本 0.8.0、能力描述
└─ README.md / 使用说明.md / 工具清单.md ← 改：能力入档
```

## 8. 数据模型（两个新增文件）

产物目录：默认写 `<素材目录>\roughcut\`，不改源视频；`--out` 可覆盖。

### 8.1 分组表 `grouping.json`（命名推断的产物）

```json
{
  "version": 1,
  "root": "E:\\素材\\第3集",
  "scheme": "ep-shot-take",
  "scheme_confidence": "high",
  "evidence": "文件名形如 03-05-02.mp4：第一段=集(03)、第二段=镜(05)、第三段=素材(02)；同镜连续编号成立",
  "shots": [
    {
      "shot": "05",
      "takes": [
        { "take": "01", "kind": "single", "files": ["03-05-01.mp4"] },
        { "take": "02", "kind": "segmented", "files": ["03-05-02-1.mp4", "03-05-02-2.mp4"] }
      ]
    }
  ],
  "unresolved": [{ "file": "1.mp4", "reason": "纯序号无法判断镜号" }],
  "excluded": [{ "file": "封面.png", "reason": "非视频" }]
}
```

规则：每个视频文件必须出现在 `shots` / `unresolved` / `excluded` 之一（校验器强制）。
`kind` 区分 `single`（单文件 take）与 `segmented`（一条 take 被截成多个文件，按序接续）。

### 8.2 剪辑决策表 `roughcut.json`（edit plan）

```json
{
  "version": 1,
  "episode": "第3集",
  "source_root": "E:\\素材\\第3集",
  "canvas": { "width": 1080, "height": 1920, "ratio": "9:16", "fps": 30 },
  "shots": [
    {
      "shot": "05",
      "script_ref": "第5镜：女主回头，看到桌上的信",
      "segments": [
        { "file": "03-05-02.mp4", "source_start": 0.4, "duration": 2.6 }
      ],
      "reason": "take02 人物一致、动作完整；take01 中段手部崩坏",
      "confidence": 0.86,
      "alternates": [{ "file": "03-05-01.mp4", "note": "中段手部崩坏" }],
      "transition_out": { "type": "hard_cut" },
      "effects": [],
      "review": []
    }
  ],
  "warnings": []
}
```

同镜的 `segments` 按顺序接续（同一 take 的分段或混段拼接）。`transition_out.type` v1 支持
`hard_cut` 与 `dissolve`（带 `duration`）；其他类型只写建议，不执行。`effects` 同理只标注。
人读版 `roughcut.md` 与 JSON 同时产出：逐镜表（首选/备选/取用区间/理由）+ 待确认清单 + 时间线摘要。

## 9. 共享核心 `tools/jy_roughcut.py`

| 函数 | 作用 |
| --- | --- |
| `scan(root)` | 清点视频文件 + 探测元数据（视频流时长/分辨率/帧率/音轨），写 `scan.json` |
| `validate_grouping(grouping, scan)` | 覆盖率强制、镜号/take 冲突、segmented 顺序 |
| `validate_plan(plan, scan)` | 区间越界、时间线重叠、缺镜、文件不存在、缺理由/置信度 |
| `plan_to_shotlist(plan)` | 生成 shotlist.json（`target_start` 自动累加） |
| `apply_transitions(draft, plan)` | 叠化经 `jianying_tool.cli transition` 应用；其余类型只报告 |
| `report(plan)` | 写 `roughcut.md` |

约定与 `build_draft.py` / `jy_template.py` 一致：自动读 `jianying-paths.json` 注入环境变量、
默认用运行时 venv 的 python、探测以视频流时长为准。

## 10. CLI `tools/roughcut.py`

- `scan --source <目录> [--out <目录>]`
- `validate --grouping <json> --plan <json>`（必须 0 error）
- `build --plan <json> [--name <草稿名>] [--existing] [--dry-run]`（内部调 `build_draft.py`，再挂叠化）
- `report --plan <json>`

改过 plan 后重建产生**新草稿**，不覆盖已交付的草稿（沿用 build_draft 的防覆盖行为）。

## 11. MCP 工具（`mcp_ext/server.py` 增 3 个，薄包装）

| 工具 | 作用 |
| --- | --- |
| `scan_roughcut_folder(folder)` | 清点 + 探测结果，供 AI 做命名推断 |
| `validate_roughcut_plan(plan_path, grouping_path)` | 分组与 plan 全量校验，返回 error/warning 清单 |
| `build_roughcut_draft(plan_path, draft_name, existing=False)` | plan → 草稿（含叠化应用与 doctor） |

## 12. 技能 `skills/rough-cut/SKILL.md`

流程（六步，教 AI 按序执行）：

1. **清点**：`roughcut.py scan` 列出全部素材与元数据。
2. **命名推断**：读文件名与目录结构，产出 `grouping.json`；每个推断写 evidence。
3. **看片建卡**：按镜头组联系表（同镜 take 并排），`view_image` 逐张看；
   对照剧本/分镜记录：头尾稳定性、中段崩坏、人物/服装一致性、动作完成度、情绪匹配。
4. **逐镜决策**：定首选/备选/取用区间/理由/置信度，写 `roughcut.json` 与 `roughcut.md`。
5. **校验**：`roughcut.py validate`，必须 0 error。
6. **交付**：待确认清单先给用户过目 → `roughcut.py build` → doctor → 剪映验收。

硬规则：不许无 evidence 硬猜；不确定进 `unresolved` / `review`；不许跳过 validate；
原素材只读；改草稿前确认剪映已退出目标草稿。

## 13. 命名推断细则（核心难点）

1. 分段：按非数字分隔符切段，提取数字与关键词（ep/shot/take/scene、集/镜/段）。
2. 统计：同一位置的数字序列是否连续、是否整批唯一，推断语义；分组规模（每镜 2~5 个 take 常见）作为旁证。
3. 歧义：判不准的编号不放进 `shots`，进 `unresolved`。
4. 纯数字文件名：只能确定顺序、不能确定镜号；配剧本时按画面匹配，否则全列待确认。
5. segmented 接续：同 take 多文件按尾段编号排序；编号缺失时用前段尾帧/后段首帧的画面连续性辅助判断，存疑进 `review`。
6. 兜底：文件修改时间 + 画面内容对剧本逐条匹配；无剧本时按文件名序 + 画面连续性，
   报告明确声明「未对剧本验证」。
7. 复用：`grouping.json` 经用户确认后视为事实源，后续步骤与重跑一律引用，不重新推断。

## 14. 测试

`tests/test_jy_roughcut.py`，标准库 unittest，不依赖剪映；探测结果可注入（mock），不依赖真实素材：

1. 纯数字命名：分组与镜序正确。
2. 「集-镜-素材」命名：同镜多 take 归组。
3. 混合/无规则命名：`unresolved` 全覆盖，不硬猜。
4. segmented（一 take 多段）：顺序与接续。
5. 校验器：覆盖率、越界（构造「视频流时长 < 容器时长」用例）、时间线重叠。
6. `plan_to_shotlist` 的 `target_start` 累加正确。
7. `report.md` 生成：待确认清单必含 unresolved 与低置信项。
8. `--dry-run` 走全量校验但不写任何文件（对齐 template-batch 的验收标准）。

## 15. 交付后如何验收

1. 用一集真实素材跑全链路：scan → 推断 → 看片 → plan → validate → build。
2. 剪映打开草稿：镜序、取用区间、无黑帧、无重叠；按报告能人工换 take。
3. 跑 `scripts/validate_plugin.ps1` 与 unittest。

## 16. 留待实现阶段的决定

- `grouping.json` / `roughcut.json` 字段的最终命名与报错文案。
- 联系表格数与布局；同镜 take 对比图的呈现方式。
- 叠化应用走 CLI 还是 MCP 的具体调用方式。
- `scan` 的并行度与探测失败降级策略。
- canvas 默认取哪个素材：建议众数分辨率/帧率，冲突时取第一条并告警。

## 17. 已知限制（要写进用户文档）

- 命名推断是启发式 + AI，可能出错；所有推断可人工校对，不确定项强制进待确认。
- 看片采样有限，可能漏掉短暂崩坏；报告与剪映复核兜底。
- v1 只执行硬切与叠化；其余转场/特效仅给建议。
- 素材规模大时看片与决策的耗时/token 成本随镜头数线性增长。
- 无剧本模式下镜序只能按文件名与画面自洽排序，戏剧顺序的正确性由用户负责。

## 18. 构建者复核记录

（写完设计后由独立复核者复核，问题与归类记于此处。）
