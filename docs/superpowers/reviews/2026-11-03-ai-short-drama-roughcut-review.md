# AI 短剧智能粗剪 0.8.0 复审报告

**审查日期：** 2026-11-03  
**审查对象：** jianying-kit 0.8.0 智能粗剪功能  
**审查范围：** `ed65616..a71b6a5`  
**当前 HEAD：** `a71b6a5`  
**审查方式：** 只读代码审查、设计/规则/计划对照、实际测试与针对性复现  
**工作区状态：** 审查结束时 `git status --short` 无输出，未修改仓库文件

---

## 1. 总体结论

**发布建议：修完再交付。**

基础架构和主要链路已经成形，`build()` 确实会同时执行分组与计划校验，`--dry-run` 零写入、零 runner 调用，多段镜头转场下标及最后一个镜头 dissolve 跳过逻辑也正确。

但当前仍存在会影响交付正确性的阻断问题：

- Windows PowerShell 5.1 生成的 BOM JSON 无法读取。
- scan、grouping、plan 的素材根目录没有强绑定，可能静默使用错误目录的同名素材。
- 重复 shot/take ID 可以绕过覆盖校验。
- 未入选 take 可以不写入 alternates，违反素材全覆盖审计原则。
- 报告缺少备选 take、正常流程缺少 unresolved。
- build 在草稿碰撞检查前已经覆盖 `shotlist.json`。

现有测试全部通过，但没有覆盖上述关键路径。

---

## 2. 验证结果

| 项目 | 结果 |
| --- | --- |
| 全量 unittest | `70/70` 通过 |
| 模板 MCP 专项 unittest | `40/40` 通过 |
| `scripts\validate_plugin.ps1` | 通过 |
| `git diff --check` | 通过 |
| 工作区清洁检查 | 通过 |
| 实际剪映端到端验收 | 未执行 |

以下行为经代码检查或临时数据验证为正确：

- `build()` 同时执行 `validate_grouping` 与 `validate_plan`。
- 校验失败发生在写 `shotlist.json` 之前。
- `--dry-run` 不创建目录、不写文件、不调用 builder/transition runner。
- 多段镜头中，转场挂在前一镜最后一个 segment 的累计位置上。
- 最后一个镜头的 dissolve 会跳过并写入 `skipped`。
- MCP 六个工具可正常注册，原模板工具测试通过。
- `tests/test_jy_template.py` 将工具集合断言从 3 个更新为 6 个，修改合理。

---

## 3. Findings

### Critical

#### C-01 含 UTF-8 BOM 的 JSON 无法读取

**位置：**

- `E:\jianying-kit\tools\roughcut.py:20-21`
- `E:\jianying-kit\mcp_ext\server.py:72-77`
- `E:\jianying-kit\scripts\fix_bom.ps1:3-14`

**问题：**

CLI 和 MCP 均使用 `encoding="utf-8"` 读取用户 JSON。仓库同时通过 `fix_bom.ps1` 主动给两个 roughcut 示例写入 UTF-8 BOM，当前示例文件实际以 `EF BB BF` 开头。

使用随包示例运行 `roughcut.py validate` 时，实际返回：

```text
Unexpected UTF-8 BOM (decode using utf-8-sig)
exit=2
```

**影响：**

Windows PowerShell 5.1 写出的 `grouping.json`、`roughcut.json`，以及仓库自带示例，都无法被新功能读取。`validate_plugin.ps1` 使用 PowerShell `ConvertFrom-Json`，能接受 BOM，因此自检会误判通过。

**修复建议：**

- CLI、MCP 的所有用户 JSON 输入统一改用 `utf-8-sig`。
- 增加有 BOM、无 BOM、中文路径的读取测试。
- 明确仓库统一编码策略：Python 写入 UTF-8；读取端同时兼容 BOM。

---

#### C-02 素材根目录未强绑定，可能静默使用错误素材

**位置：**

- `E:\jianying-kit\tools\jy_roughcut.py:116-159`
- `E:\jianying-kit\tools\jy_roughcut.py:205-210`
- `E:\jianying-kit\tools\jy_roughcut.py:266-274`
- `E:\jianying-kit\tools\jy_roughcut.py:504-512`

**问题：**

- `validate_grouping` 不比较 `scan.root` 与 `grouping.root`。
- `validate_plan` 对 `plan.source_root` 与 `grouping.root` 不一致只产生 warning。
- `build()` 最终完全按 `plan.source_root` 解析文件路径。

实际构造：

```text
scan.root     = E:\A
grouping.root = E:\B
plan.source_root = E:\B
```

两个校验器均返回 `ok=True`。如果 B 目录存在同名文件，构建时会静默使用 B 中的素材，而不是 scan 对应的 A。

校验器也没有检查当前磁盘上的视频、SRT、BGM、voiceover 文件是否仍存在。

**影响：**

直接违反设计中的“静默错配”反目标，可能造成 take 张冠李戴，且报告中不可见。

**修复建议：**

- 规范化后要求 `scan.root == grouping.root == plan.source_root`，不一致作为 error。
- 对入选素材、备选素材及辅助文件执行 `is_file()` 检查。
- 如确需目录迁移，提供显式 rebase 并重新 scan，而不是依赖 warning。

---

#### C-03 重复 shot/take ID 会静默吞掉素材

**位置：**

- `E:\jianying-kit\tools\jy_roughcut.py:139-157`
- `E:\jianying-kit\tools\jy_roughcut.py:216-235`

**问题：**

`validate_grouping` 只检查 shot/take 标识非空，不检查唯一性。`validate_plan` 随后执行：

```python
shot_files[sid] = files
```

同 ID shot 会被覆盖。

实际构造两个 `shot="01"` 分组，分别包含 `a.mp4` 和 `b.mp4`，plan 只写一个 `shot="01"` 并选择 `b.mp4`，两个校验器仍均返回 `ok=True`，`a.mp4` 静默消失。

**影响：**

一个推断笔误即可让整条镜头从计划及草稿中消失，违反“全覆盖”原则。

**修复建议：**

- 规范化后校验 shot 全局唯一。
- 校验同一 shot 内 take ID 唯一。
- 重复项直接返回 error，并增加失败测试。

---

### Important

#### I-01 素材级“入选/备选/待确认”覆盖未真正校验

**位置：**

- `E:\jianying-kit\tools\jy_roughcut.py:249-299`
- `E:\jianying-kit\skills\rough-cut\SKILL.md:32,53`

**问题：**

- 未入选 take 不强制出现在 `alternates`。
- `alternates`、`transition_out`、`review` 字段可以整体缺失。
- grouping 的 `scheme`、`scheme_confidence`、`evidence` 及 unresolved 原因未强制。
- 全部素材进入 `unresolved`、plan 为空时，两个校验器仍返回 `ok=True`，dry-run 也返回成功。

实际构造同镜两条 take、plan 只选一条且 `alternates=[]`，校验通过。

**影响：**

违反规则 P5“每条素材有结局”，也违反技能硬规则“每个落选 take 必须备注”。用户无法从报告中恢复备选素材。

**修复建议：**

- 建立 take/file 级 outcome 集合。
- 未入选 take 必须在 `alternates` 中有记录和备注。
- 强制设计要求的必填字段与 evidence。
- 没有可建镜头时返回明确校验错误。

---

#### I-02 同名草稿碰撞时，shotlist 已被覆盖

**位置：**

- `E:\jianying-kit\tools\jy_roughcut.py:540-550`
- `E:\jianying-kit\tools\build_draft.py:195-202`

**问题：**

`build()` 先写 `out/shotlist.json`，之后才调用 `build_draft.py`；后者发现同名草稿时才拒绝。

实测 builder 抛“草稿已存在”后，`shotlist.json` 已经存在并被覆盖。

**影响：**

违反计划中的“已有同名草稿不得覆盖任何东西、不得写任何文件”，会破坏上一次生成的派生分镜表。

**修复建议：**

- 在写任何文件前完成草稿名碰撞预检。
- 或提供无副作用的 `--check-only` 构建预检入口。
- 增加“已有同名草稿时目录树保持不变”的测试。

---

#### I-03 `roughcut.md` 缺少关键审计内容

**位置：**

- `E:\jianying-kit\tools\roughcut.py:133-140`
- `E:\jianying-kit\tools\jy_roughcut.py:317-349`
- `E:\jianying-kit\skills\rough-cut\SKILL.md:21`

**问题：**

- 技能只调用 `report --plan ...`，CLI 在未传 `--grouping` 时使用 `None`，所以 unresolved 不会进入报告。
- 报告表格没有备选 take。
- 报告完全忽略 `plan.warnings`。
- 无剧本模式要求的“未对剧本验证”没有输出通道。

**影响：**

报告无法完成“按报告人工换 take”和审计待确认项的核心职责。

**修复建议：**

- `report` 默认加载 `<out>\grouping.json`。
- 逐镜表加入备选 take 和备注。
- 输出 plan warnings。
- 无 `script_ref` 或检测到无剧本模式时，明确写出“未对剧本验证”。

---

#### I-04 非法 dissolve 时长只报警告，build 仍会执行

**位置：**

- `E:\jianying-kit\tools\jy_roughcut.py:283-295`
- `E:\jianying-kit\tools\jy_roughcut.py:523-526`

**问题：**

规则要求 dissolve 时长为 0.3–0.8 秒，但校验器仅产生 warning。数值 `1.5` 会被原样传给 `jianying_tool.cli transition`。非数值字符串在校验阶段通过后，会于 build 阶段 `float()` 崩溃。

build 内部也没有把 validation warnings 带入结果。

**影响：**

`validate` 可返回 0 error，随后执行违反规则的转场或异常退出。

**修复建议：**

- 非有限数值或超出 0.3–0.8 必须作为 error。
- 若产品决定保留 warning 语义，build 必须跳过非法转场并明确记录，不能执行原值。

---

#### I-05 结构错误会让校验器崩溃，CLI 退出码不稳定

**位置：**

- `E:\jianying-kit\tools\jy_roughcut.py:110-124`
- `E:\jianying-kit\tools\jy_roughcut.py:216-228`
- `E:\jianying-kit\tools\jy_roughcut.py:283-284`
- `E:\jianying-kit\tools\roughcut.py:24-27`
- `E:\jianying-kit\tools\roughcut.py:103-117`
- `E:\jianying-kit\tools\roughcut.py:143-150`

**问题：**

- `shots` 写成字符串、`transition_out` 写成字符串时，会抛 `AttributeError`，不是返回 error 列表。
- canvas 只检查非空，不检查类型、正数和比例。
- 缺少 `source_root` 时，无 `--out` 会在输出目录推导阶段返回 2；有 `--out` 时由校验器返回 1。
- `OSError`、`UnicodeError` 等 IO/编码错误没有被统一映射为退出码 2。

**影响：**

同一份 plan 的退出码取决于参数组合；无效输入会打印 traceback，用户无法区分校验失败与 IO 错误。

**修复建议：**

- 对每层 JSON 先做类型与范围校验，累积到 error 列表。
- 在任何 out 路径推导前完成可独立检查的字段校验。
- CLI 将 IO/编码错误统一归为 2，结构校验问题稳定归为 1。

---

#### I-06 Windows CLI 中文输出乱码

**位置：**

- `E:\jianying-kit\tools\roughcut.py:36-37`
- `E:\jianying-kit\tools\roughcut.py:50-151`

**问题：**

当前环境代码页为 936，CLI 未重配 stdout/stderr。实际运行 `roughcut.py --help` 和错误输出均出现乱码。`frames`、`report`、`build` 返回的中文路径也可能被 Codex 以错误编码解析。

**影响：**

AI 无法正确读取诊断信息；中文素材目录下可能无法使用返回的 contact sheet、report、shotlist 路径。

**修复建议：**

在 `main()` 开头执行：

```python
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
```

与 `build_draft.py` 保持一致。

---

### Minor

#### M-01 联系表未执行“每张 ≤ 12 格”约束

**位置：**

- `E:\jianying-kit\tools\jy_roughcut.py:413-425`

**问题：**

默认每个文件抽 5 帧，并按 take 横向展开。3 个 take 即产生 15 个有效格，segmented take 增多时更大。

**影响：**

常见三 take 场景就会超出设计上限，图片变宽、标签缩小，降低 AI 看片质量。

**修复建议：**

按 ≤12 格拆成多张 `shot-05-1.jpg`、`shot-05-2.jpg`，或动态减少每个 take 的采样数并记录映射。

---

## 4. 未覆盖的测试与残余风险

### 4.1 未覆盖的关键测试

- `probe_media` 未测试“视频流时长 < 容器时长”及容器时长回退。
- 未测试 BOM/无 BOM、GBK stdout、中文文件名和中文目录。
- 未测试 scan/grouping/plan 根路径不一致。
- 未测试重复 shot/take ID、空 plan、全 unresolved、实际文件删除。
- 未测试 JSON 结构类型错误、`NaN`、canvas 非法数值。
- 未测试 source_audio 非法值、canvas 缺失、unresolved 出现在 alternates。
- 未测试 build 同名草稿零写入。
- 未测试 bgm、voiceover 路径解析。
- 未测试多段镜头转场下标和最后一个 dissolve 跳过；当前代码经临时数据复核正确。
- 未测试默认 `_default_grab`、ffmpeg 失败、联系表格数上限。
- 未测试技能实际使用的 `report --plan` 无 grouping 调用。
- 未进行真实剪映草稿的端到端验收。

### 4.2 自检脚本覆盖情况

`validate_plugin.ps1` 已覆盖：

- `tools/jy_roughcut.py`
- `tools/roughcut.py`
- `mcp_ext/server.py`
- `skills/rough-cut/SKILL.md`
- 两个 roughcut 示例
- MCP 服务器配置

尚未覆盖：

- `skills/rough-cut/references/decision-rules-v0.1.md`
- 新 CLI 是否真能读取示例 JSON
- 新 MCP 工具的实际 schema 和调用
- BOM 兼容性

### 4.3 残余风险

- `--existing` 仅对空草稿安全；非空轨道上的 segment 下标、`target_start` 和重叠行为未验证。
- `plan_to_shotlist` 显式写入 `target_start`，实际剪映时间取整后是否产生微重叠尚未验证。
- 命名推断完全依赖会话 AI，纯数字文件名等高风险场景没有自动化质量门。
- 尚未用真实一集素材跑完 scan → grouping → frames → plan → validate → build → 剪映验收。

---

## 5. 与设计或计划不符

| 偏差 | 判断 |
| --- | --- |
| 报告未列备选 take，正常流程未加载 unresolved | 不符合设计，必须修 |
| 无剧本模式未输出“未对剧本验证” | 不符合设计，必须修 |
| 联系表未限制每张 ≤12 格 | 计划遗漏了设计约束，应修 |
| `source_root` 不一致仅 warning | 与“禁止静默错配”反目标冲突，计划本身需调整 |
| 未校验 shot/take 冲突及 segmented 顺序 | 设计 §9 未实现完整 |
| 规则要求 60–74 分备选作为首选时写 review，设计只要求 `<0.6` | 阈值语义冲突，需要统一 |
| 计划示例要求两条 shot 且含 dissolve，实际只有一条 hard_cut | 轻微计划偏离，建议补齐示例 |
| build 内同时跑 grouping 与 plan 校验 | 合理增强 |
| MCP 扩展为六个工具、模板测试更新为六工具集合 | 合理，回归通过 |

---

## 6. 最终结论

**当前 HEAD 不应直接交付，需修完上述 Critical 和 Important 问题后再复审。**

优先修复顺序建议：

1. BOM 读取。
2. 根目录与实际文件存在性校验。
3. shot/take 唯一性。
4. 未入选 take 与 unresolved 的覆盖审计。
5. 报告内容完整性。
6. build 碰撞前零写入。
7. 转场与 JSON 结构校验。
8. Windows CLI 输出编码。

修复后至少补齐本报告“未覆盖的测试”中列出的关键失败模式，并完成一次真实剪映端到端验收。