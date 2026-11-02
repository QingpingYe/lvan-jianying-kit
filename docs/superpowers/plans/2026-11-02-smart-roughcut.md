# 智能粗剪（AI 短剧）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 jianying-kit 增加「AI 短剧智能粗剪」：一集素材 + 剧本（可选）进，产出分组表与剪辑决策表，校验通过后一键建成可在剪映精修的草稿。

**Architecture:** 新增 `tools/jy_roughcut.py` 作为唯一核心实现（扫描探测、分组校验、plan 校验、plan 转 shotlist、转场/静音挂载、报告、联系表）；`tools/roughcut.py` 是 CLI 薄包装，`mcp_ext/server.py` 扩 3 个 MCP 薄包装；「命名推断、看片、take 择优」三类判断留在会话里由 Codex 按《AI 短剧粗剪决策规则集 v0.1》执行，脚本只做确定性事务。

**Tech Stack:** Python（运行时 venv）、`av`（元数据）、`imageio_ffmpeg` + `Pillow`（抽帧/联系表）、`jianying_tool` 0.5.0 的 CLI（`transition` / `edit-segment`）与 `build_draft.py`、`mcp` 2.3.0 的 `MCPServer`、标准库 `unittest`、PowerShell 自检脚本。

**Spec:** `docs/superpowers/specs/2026-11-02-smart-roughcut-design.md`（规则依据：`docs/superpowers/specs/2026-11-02-ai-short-drama-roughcut-rules-v0.1.md`，两份都要读）

## Global Constraints

- 开发仓库是 `E:\jianying-kit`（版本 0.7.0 → 0.8.0）；宿主实际加载的是插件缓存目录，改完代码后需要同步缓存并重启 Codex（属于交付动作，不是任务）。
- 运行时解释器：`C:\Users\lvan\.jianying-kit\.venv\Scripts\python.exe`（测试里用 `sys.executable`，不要写死路径）。
- 不新增任何 pip 依赖；复用 `av` / `Pillow` / `imageio_ffmpeg`；测试只用标准库 `unittest`（venv 里没有 pytest）。
- 不修改第三方包 `jianying_tool` / `pyJianYingDraft` 的任何文件。
- 所有产物写 `<素材目录>\roughcut\`（可用 `--out` 覆盖），绝不修改、删除、重命名源素材；测试产物一律写临时目录。
- Python 源文件 UTF-8；中文注释与文档保留中文；提交信息用 `feat:` / `test:` / `docs:` 前缀，一次提交对应一个任务。
- 术语固定：素材 = clip，镜头 = shot，同镜候选 = take，分组表 = `grouping.json`，剪辑决策表 = edit plan（`roughcut.json`）。
- 转场枚举名用 `pyJianYingDraft` 的 `TransitionType.叠化`（默认 0.5 秒）；按库文档，**转场必须加在前一个片段上**。
- 命名推断不做成脚本规则：脚本只校验推断产物；推断与看片由会话内 AI 完成。
- 可机检的规则进校验器（覆盖率、区间、置信度、转场类型、unresolved 禁用）；判断类规则留给会话 AI，结论必须写进 plan 的 `reason` / `review`。

## Review Focus

1. **命名推断产物不完整或有重复覆盖**：校验器必须报错并阻止 build，绝不允许静默漏素材。
2. **取用区间超出视频流时长**（容器时长不可信，这是既有坑）：必须报错，不允许产出黑帧草稿。
3. **低置信镜头（confidence < 0.6）没有写进 `review`**：必须校验失败。
4. **`unresolved` 的素材出现在 plan 或 shotlist 里**：必须报错；不可识别素材不进成片时间线。
5. **build 遇到已有同名草稿、或 `--dry-run`**：不得覆盖任何东西、不得写任何文件；dry-run 走全量校验但零写入。

---

### Task 1: 共享核心骨架与 `scan`

**Files:**
- Create: `tools/jy_roughcut.py`
- Create: `tests/test_jy_roughcut.py`

**Interfaces:**
- Consumes: `av`（惰性导入，仅默认 prober 用）
- Produces:
  - `class RoughcutError(RuntimeError)`
  - `VIDEO_EXTS: tuple[str, ...] = (".mp4", ".mov", ".m4v", ".avi", ".mkv")`
  - `probe_media(path: Path) -> dict`：返回 `{"duration": float|None, "width": int|None, "height": int|None, "fps": float|None, "has_audio": bool, "warning": str|None}`；`duration` 取**视频流**时长（`stream.duration * stream.time_base`），视频流时长缺失时退回容器时长并把 warning 写成「容器时长」。
  - `scan(root: Path, out: Path | None = None, *, probe: Callable[[Path], dict] | None = None) -> dict`：写出 `out/scan.json`（`out` 默认 `<root>/roughcut`），返回 `{"version": 1, "root": str(root), "generated": ISO 时间, "clips": [...], "skipped": [...]}`；`clips[i] = {"file", "duration", "width", "height", "fps", "has_audio", "warning"}`；`skipped[i] = {"file", "reason"}`。

- [ ] **Step 1: 写失败测试**

```python
def test_scan_writes_metadata_and_skips_non_video(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.mp4").write_bytes(b"x")
        (root / "notes.txt").write_text("x", encoding="utf-8")
        meta = {"duration": 3.2, "width": 1080, "height": 1920, "fps": 30.0, "has_audio": False}
        data = jy_roughcut.scan(root, probe=lambda p: dict(meta))
        self.assertEqual([c["file"] for c in data["clips"]], ["a.mp4"])
        self.assertEqual(data["skipped"][0]["file"], "notes.txt")
        self.assertTrue((root / "roughcut" / "scan.json").is_file())

def test_probe_failure_keeps_the_clip_with_a_warning(self):
    # probe 抛 ValueError；断言该 clip 仍在 clips 里、duration 为 None、warning 非空，scan 不中断
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_roughcut.ScanTests -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'jy_roughcut'`

- [ ] **Step 3: 实现骨架与 `scan`**

文件枚举按 `VIDEO_EXTS` 小写匹配、按文件名排序；`scan` 对每个视频调 `probe`（默认 `probe_media`），异常捕获成 `warning`；`out.mkdir(parents=True, exist_ok=True)` 后写 `scan.json`（`ensure_ascii=False, indent=2`）。`probe_media` 用 `av.open` 取第一条视频流。

- [ ] **Step 4: 跑测试确认通过**

Run: `& $py -m unittest tests.test_jy_roughcut.ScanTests -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tools/jy_roughcut.py tests/test_jy_roughcut.py
git commit -m "feat: 智能粗剪核心（素材清点与探测）"
```

---

### Task 2: 分组表校验 `validate_grouping`

**Files:**
- Modify: `tools/jy_roughcut.py`
- Modify: `tests/test_jy_roughcut.py`

**Interfaces:**
- Consumes: Task 1 的 `scan` 输出结构
- Produces: `validate_grouping(grouping: dict, scan: dict) -> dict`，返回 `{"ok": bool, "errors": [str], "warnings": [str]}`。

校验规则（全部实现）：

1. `scan.clips` 里每个素材必须出现在 `shots[].takes[].files` / `unresolved[].file` / `excluded[].file` 中**恰好一次**；缺失或重复都进 `errors`。
2. 任何引用指向 `scan` 里不存在的文件，进 `errors`（`excluded` 允许引用 `skipped` 里的非视频文件）。
3. `kind` 只能是 `single` / `segmented`；`segmented` 但只有一个文件时进 `warnings`。
4. `shot` / `take` 标识与 `files` 列表不能为空。

- [ ] **Step 1: 写失败测试**

```python
def test_missing_coverage_is_an_error(self): ...        # scan 两条素材，grouping 只覆盖一条 → errors 非空、ok False
def test_duplicate_file_coverage_is_an_error(self): ... # 同一文件同时出现在 shots 与 unresolved → errors
def test_unknown_file_reference_is_an_error(self): ...  # grouping 引用了 scan 里没有的文件 → errors
def test_segmented_single_file_warns(self): ...         # kind=segmented 但 files 只有一项 → warnings
def test_valid_grouping_passes(self): ...               # 全覆盖、无重复 → ok True、errors 为空
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_roughcut.GroupingTests -v`
Expected: FAIL，`AttributeError: module 'jy_roughcut' has no attribute 'validate_grouping'`

- [ ] **Step 3: 实现 `validate_grouping`**

用 `collections.Counter` 统计每个文件被覆盖的次数（`shots` / `unresolved` / `excluded` 一起算），一次遍历产出全部错误，不提前返回。

- [ ] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_roughcut.GroupingTests -v`，Expected: PASS

```bash
git add tools/jy_roughcut.py tests/test_jy_roughcut.py
git commit -m "feat: 分组表校验（覆盖率与结构）"
```

---

### Task 3: 剪辑决策表校验 `validate_plan`

**Files:**
- Modify: `tools/jy_roughcut.py`
- Modify: `tests/test_jy_roughcut.py`

**Interfaces:**
- Consumes: Task 1 的 `scan` 结构、Task 2 的 `grouping` 结构
- Produces: `validate_plan(plan: dict, scan: dict, grouping: dict) -> dict`（与 `validate_grouping` 同形）。

校验规则（全部实现）：

1. `canvas` 必须含 `width` / `height` / `ratio` / `fps`；`episode` / `source_root` 非空（`source_root` 与 `grouping["root"]` 不一致时进 `warnings`）。
2. `grouping` 里每个 shot 必须在 plan 里出现恰好一次；出现 grouping 之外的 shot 也进 `errors`。
3. 每个 shot：`segments` 非空；`reason` 非空；`confidence` 在 [0,1]；`confidence < 0.6` 时 `review` 必须非空。
4. 每个 segment：`file` 属于该 shot 的 takes；`duration > 0`；`source_start >= 0`；`source_start + duration <= 视频流时长 + 1e-6`（用 scan 的 duration）。
5. `alternates[].file` 必须属于同一 shot 的 takes。
6. `grouping["unresolved"]` 里的文件出现在任何 segment / alternates 中时进 `errors`。
7. `transition_out.type`：`hard_cut` / `dissolve` 合法；`dissolve` 的 `duration` 在 0.3 到 0.8 之外时进 `warnings`；其他类型一律进 `warnings`（「只标注不执行」）。
8. `source_audio` 缺省或 `keep` / `mute`；其他值进 `errors`。

- [ ] **Step 1: 写失败测试**

```python
def test_interval_beyond_stream_duration_is_an_error(self):
    # scan 里 a.mp4 视频流 2.0s；plan 里 source_start=1.5, duration=1.0 → errors 含 "超出"
def test_missing_shot_is_an_error(self): ...             # grouping 两个 shot，plan 只有一个 → errors
def test_low_confidence_requires_review_entry(self): ... # confidence=0.5 且 review=[] → errors；补上 review 后 ok
def test_unresolved_file_in_plan_is_an_error(self): ...  # unresolved 的文件被写进 segments → errors
def test_dissolve_duration_out_of_range_warns(self): ... # dissolve duration=1.5 → warnings、ok True
def test_unknown_transition_type_only_warns(self): ...   # type="flash" → warnings、ok True
def test_valid_plan_passes(self): ...                    # 全量合法 → ok True、errors 为空
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_roughcut.PlanTests -v`
Expected: FAIL，`AttributeError: module 'jy_roughcut' has no attribute 'validate_plan'`

- [ ] **Step 3: 实现 `validate_plan`**

先建 `shot_files: dict[str, set[str]]`（shot → 可用文件集合）与 `unresolved_files: set[str]`，再按上面 8 条逐项检查；错误文案用中文、带 shot 号与文件名（例如 `shot 05: a.mp4 取用区间 1.5+1.0 超出视频流时长 2.0`）。

- [ ] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_roughcut.PlanTests -v`，Expected: PASS

```bash
git add tools/jy_roughcut.py tests/test_jy_roughcut.py
git commit -m "feat: 决策表校验（区间、置信度、转场、unresolved）"
```

---

### Task 4: `plan_to_shotlist` 转换

**Files:**
- Modify: `tools/jy_roughcut.py`
- Modify: `tests/test_jy_roughcut.py`

**Interfaces:**
- Consumes: Task 3 校验通过的 plan
- Produces: `plan_to_shotlist(plan: dict, *, draft_name: str | None = None) -> dict`
  - `draft_name = draft_name or plan.get("draft_name") or f"{plan['episode']}-粗剪"`
  - `shots`：按 plan 顺序摊平，保留 `file` / `source_start`（缺省 0.0）/ `duration`；`target_start` 从 0 起按 `duration` 累加（v1 不带 speed）
  - `canvas` 原样拷贝；`subtitles` / `bgm` / `voiceover` 存在时原样透传

- [ ] **Step 1: 写失败测试**

```python
def test_target_start_accumulates(self):
    # 三镜 duration 2.0 / 3.5 / 1.0 → target_start 0.0 / 2.0 / 5.5
def test_default_draft_name_uses_episode(self): ...   # episode="第3集" → draft_name "第3集-粗剪"
def test_passthrough_blocks_survive(self): ...        # subtitles/bgm/voiceover 内容与 plan 完全一致
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_roughcut.ShotlistTests -v`
Expected: FAIL，`AttributeError: module 'jy_roughcut' has no attribute 'plan_to_shotlist'`

- [ ] **Step 3: 实现 `plan_to_shotlist`**

纯函数、不碰文件系统；`target_start` 用 `round(x, 6)` 消除浮点尾巴。

- [ ] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_roughcut.ShotlistTests -v`，Expected: PASS

```bash
git add tools/jy_roughcut.py tests/test_jy_roughcut.py
git commit -m "feat: 决策表转分镜表"
```

---

### Task 5: 人读报告 `report`

**Files:**
- Modify: `tools/jy_roughcut.py`
- Modify: `tests/test_jy_roughcut.py`

**Interfaces:**
- Consumes: plan、可选 grouping
- Produces: `report(plan: dict, grouping: dict | None = None) -> str`（Markdown 文本）
  - 顶部：集名、总时长（各 shot duration 之和，保留 2 位）
  - 逐镜表：镜号 / 首选素材 / 取用区间 / 理由 / 置信度 / 转场
  - 待确认清单：`grouping["unresolved"]` + `confidence < 0.6` 或 `review` 非空的 shot
  - 无待确认项时写「无」

- [ ] **Step 1: 写失败测试**

```python
def test_report_lists_unresolved_and_low_confidence(self):
    # grouping.unresolved 含 1.mp4；plan 里 shot 07 confidence=0.5 review=["[低置信]"]
    # 断言 Markdown 同时包含 "1.mp4" 与 "07"
def test_report_includes_timeline_total(self): ...   # duration 2.0 + 3.0 → 报告含 "5.00"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_roughcut.ReportTests -v`
Expected: FAIL，`AttributeError: module 'jy_roughcut' has no attribute 'report'`

- [ ] **Step 3: 实现 `report`**

只拼字符串，不写文件（写文件是 CLI 的事）；表格内容里的 `|` 要转义。

- [ ] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_roughcut.ReportTests -v`，Expected: PASS

```bash
git add tools/jy_roughcut.py tests/test_jy_roughcut.py
git commit -m "feat: 粗剪人读报告"
```

---

### Task 6: 抽帧计划与联系表 `frames`

**Files:**
- Modify: `tools/jy_roughcut.py`
- Modify: `tests/test_jy_roughcut.py`

**Interfaces:**
- Consumes: grouping、scan
- Produces:
  - `frame_times(duration: float, count: int = 5) -> list[float]`：`count == 1` 返回 `[duration / 2]`；否则第 i 个时间 = `duration * (0.1 + 0.8 * i / (count - 1))`，保留 3 位小数。
  - `frames(grouping: dict, scan: dict, out: Path, *, count: int = 5, grab: Callable[[Path, float], "PIL.Image.Image"] | None = None) -> dict`：每个 shot 一张联系表（各 take 并排，2 列 × ceil(count/2) 行），文件名 `shot-<shot>.jpg`，写进 `out/frames/`，返回 `{"sheets": [str, ...], "shots": int}`。默认 `grab` 用 `imageio_ffmpeg.get_ffmpeg_exe()` + PIL 截帧。

- [ ] **Step 1: 写失败测试**

```python
def test_frame_times_are_inside_the_clip(self):
    self.assertEqual(jy_roughcut.frame_times(10.0, 3), [1.0, 5.0, 9.0])

def test_sheets_one_per_shot_with_injected_grab(self):
    # 注入 grab：返回 PIL 纯色图（120x68）；grouping 两个 shot（含一个 segmented）
    # 断言 sheets 数量 == 2、文件名含 shot 号、PIL 能打开且宽度 > 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_roughcut.FramesTests -v`
Expected: FAIL，`AttributeError: module 'jy_roughcut' has no attribute 'frame_times'`

- [ ] **Step 3: 实现 `frame_times` 与 `frames`**

抽帧时刻按 segment 编号摊平（segmented take 的几个文件按顺序各抽 `count` 帧）；每格左上角写 `文件名 @ 秒`；`grab` 与 PIL 导入都惰性化，测试注入时不加载 ffmpeg。

- [ ] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_roughcut.FramesTests -v`，Expected: PASS

```bash
git add tools/jy_roughcut.py tests/test_jy_roughcut.py
git commit -m "feat: 抽帧计划与同镜联系表"
```

---

### Task 7: 执行器 `build` 与 CLI `tools/roughcut.py`

**Files:**
- Modify: `tools/jy_roughcut.py`
- Create: `tools/roughcut.py`
- Modify: `tests/test_jy_roughcut.py`

**Interfaces:**
- Consumes: Task 3 的 `validate_plan`、Task 4 的 `plan_to_shotlist`
- Produces:
  - `build(plan: dict, scan: dict, grouping: dict, out: Path, *, draft_name: str | None = None, existing: bool = False, dry_run: bool = False, run_builder: Callable[[list[str]], dict] | None = None, run_transition: Callable[[list[str]], dict] | None = None) -> dict`
    - 先 `validate_plan`，有 `errors` 抛 `RoughcutError`；
    - `dry_run=True`：不写文件、不调用任何 runner，返回 `{"shotlist": str, "transitions_planned": int, "muted_planned": int}`；
    - 否则写 `out/shotlist.json`；调 `run_builder(["--shotlist", <路径>] + (["--name", draft_name] 若显式给) + (["--existing"] 若 existing))`，默认 runner 用 `sys.executable` 跑 `<根>/tools/build_draft.py`；
    - 转场：`transition_out.type == "dissolve"` 的 shot，按其**最后一个片段**在视频轨的累计下标调 `run_transition(["transition", "--draft", <草稿名>, "--track", <video_track>, "--segment", <下标>, "--name", "叠化", "--duration", <duration>])`，默认 runner 用 `sys.executable -m jianying_tool.cli`；最后一个 shot 的 `transition_out` 进 `skipped`；
    - 静音：`plan.get("source_audio") == "mute"` 时，对每个片段调 `edit-segment ... --mute`；
    - 返回 `{"shotlist": str, "builder": dict, "transitions_applied": int, "muted_segments": int, "skipped": [str, ...]}`。
  - CLI `tools/roughcut.py` 子命令：`scan --source [--out]`、`frames --grouping [--out] [--count]`、`validate --grouping --plan [--out]`、`build --plan [--grouping] [--name] [--existing] [--dry-run] [--out]`、`report --plan [--grouping] [--out]`；`--out` 缺省 `<source_root>/roughcut`；JSON 结果写 stdout，人类进度写 stderr；退出码：0 成功、1 校验错误、2 用法/IO 错误。

- [ ] **Step 1: 写失败测试**

```python
def test_build_dry_run_writes_nothing_and_calls_nothing(self):
    # run_builder/run_transition 用记录列表；断言两列表都为空、out 目录树未变
def test_build_applies_dissolve_on_the_previous_segment(self):
    # shot1（1 段，transition_out=dissolve）+ shot2；假 runner 记录参数
    # 断言 builder 收到 --shotlist；transition 参数含 "--segment" "0"（shot1 的最后一段）与 "--name" "叠化"
def test_build_mute_adds_edit_segment_calls(self): ...  # source_audio="mute" → 每个片段一条 --mute 调用
def test_cli_validate_exit_codes(self):
    # 合法 plan → returncode 0；越界 plan → returncode 1；缺文件 → returncode 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_roughcut.BuildTests -v`
Expected: FAIL，`AttributeError: module 'jy_roughcut' has no attribute 'build'`

- [ ] **Step 3: 实现 `build` 与 CLI**

CLI 复用 `build_draft.py` 的既有约定：读 `jianying-paths.json` 注入环境变量；`report` 子命令写 `out/roughcut.md`；JSON 结果统一写 stdout（与 `template_fill.py` 一致）。

- [ ] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_roughcut.BuildTests -v`，Expected: PASS

```bash
git add tools/jy_roughcut.py tools/roughcut.py tests/test_jy_roughcut.py
git commit -m "feat: 粗剪执行器与命令行"
```

---

### Task 8: MCP 三个工具

**Files:**
- Modify: `mcp_ext/server.py`
- Modify: `tests/test_jy_roughcut.py`

**Interfaces:**
- Consumes: `jy_roughcut` 的 `scan` / `validate_grouping` / `validate_plan` / `build`
- Produces（注册进现有 `jianying-ext` 服务器，与已有 3 个模板工具并存，共 6 个）：
  - `scan_roughcut_folder(folder: str, out: str | None = None) -> dict` → `jy_roughcut.scan`
  - `validate_roughcut_plan(plan_path: str, grouping_path: str | None = None) -> dict`：`scan.json` / `grouping.json` 默认从 plan 同目录读；返回 `validate_grouping` 与 `validate_plan` 合并后的 `{"ok", "errors", "warnings"}`
  - `build_roughcut_draft(plan_path: str, draft_name: str | None = None, existing: bool = False, out: str | None = None) -> dict` → `jy_roughcut.build`

- [ ] **Step 1: 写失败测试**

```python
def test_build_server_registers_six_tools(self):
    # asyncio.run(server.list_tools()) 名字集合 == 模板 3 个 + scan/validate/build 3 个
def test_validate_tool_reports_errors_from_disk(self):
    # 临时目录写 scan.json / grouping.json / roughcut.json（越界区间）；断言 ok False 且 errors 含 "超出"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_roughcut.McpTests -v`
Expected: FAIL，`AttributeError: module 'mcp_ext.server' has no attribute 'scan_roughcut_folder'`

- [ ] **Step 3: 实现三个薄包装**

照现有模板工具的模式：普通函数 + `build_server()` 里 `server.tool()(fn)`；`sys.path.insert` 复用 `tools/`；路径参数一律 `Path(...).expanduser()`。

- [ ] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_roughcut.McpTests -v`，Expected: PASS

```bash
git add mcp_ext/server.py tests/test_jy_roughcut.py
git commit -m "feat: 智能粗剪接入 MCP 工具层"
```

---

### Task 9: 技能、规则集归档、示例与自检脚本

**Files:**
- Create: `skills/rough-cut/SKILL.md`
- Create: `skills/rough-cut/references/decision-rules-v0.1.md`（从 `docs/superpowers/specs/2026-11-02-ai-short-drama-roughcut-rules-v0.1.md` 复制，保留原文）
- Create: `examples/roughcut-grouping.example.json`
- Create: `examples/roughcut.example.json`
- Modify: `scripts/validate_plugin.ps1`

**Interfaces:**
- Consumes: Task 7 的 CLI、规则集文档
- Produces: 触发词含「智能粗剪 / AI 短剧粗剪 / 挑 take / 粗剪决策」的技能；`validate_plugin.ps1` 检查新增技能与两个示例 JSON

- [ ] **Step 1: 写 `SKILL.md`**

frontmatter：`name: rough-cut`，description 写触发场景。正文六步：① `roughcut.py scan` ② 命名推断产 `grouping.json`（每个推断写 evidence）③ `roughcut.py frames` 看片建卡 ④ 按 `references/decision-rules-v0.1.md` 逐镜决策产 `roughcut.json` + `roughcut.md` ⑤ `roughcut.py validate` 必须 0 error ⑥ 待确认清单给用户过目后 `roughcut.py build`。

硬规则（照抄设计文档 §12）：不硬猜；不确定进 `unresolved` / `review`；不跳过 validate；源素材只读；改草稿前确认剪映已退出；改 plan 重建要换新草稿名。

- [ ] **Step 2: 写两个示例 JSON**

`examples/roughcut-grouping.example.json` 与 `examples/roughcut.example.json` 按设计文档 §8 字段给最小但完整可校验的示例（grouping 覆盖 3 个素材：2 个 take + 1 个 unresolved；plan 两条 shot、其中一条带 dissolve）。

- [ ] **Step 3: 更新 `validate_plugin.ps1`**

技能清单加 `rough-cut`；`Check-Json` 列表加两个新示例；文件检查块加 `tools/jy_roughcut.py`、`tools/roughcut.py`。

- [ ] **Step 4: 跑自检**

Run: `powershell -ExecutionPolicy Bypass -File scripts\validate_plugin.ps1`
Expected: `自检通过。`

- [ ] **Step 5: 提交**

```bash
git add skills/rough-cut examples/roughcut-grouping.example.json examples/roughcut.example.json scripts/validate_plugin.ps1
git commit -m "feat: 智能粗剪技能与示例"
```

---

### Task 10: 版本、文档与端到端验收

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Modify: `README.md`
- Modify: `使用说明.md`
- Modify: `工具清单.md`
- Create: `docs/superpowers/plans/2026-11-02-smart-roughcut.md`（本文件，收尾时入库）

**Interfaces:**
- Consumes: 前九个任务的全部产出
- Produces: 0.8.0 的插件包与三份用户文档

- [ ] **Step 1: 版本与能力描述**

`plugin.json`：`version` 改 `0.8.0`；`longDescription` 的 `74 MCP tools` 改 `77 MCP tools`（74 + 3），补一句智能粗剪能力；`defaultPrompt` 加一条「把这集 AI 短剧素材粗剪成草稿」。

- [ ] **Step 2: 三份文档**

- `工具清单.md`：新增「十六、智能粗剪（3 个工具）」列出 `scan_roughcut_folder` / `validate_roughcut_plan` / `build_roughcut_draft`；合计从 74 改 77。
- `使用说明.md`：新增「5.8 智能粗剪（AI 短剧）」：讲清产物（`grouping.json` / `roughcut.json` / `roughcut.md`）、六步流程、`--out` 默认目录、待确认清单、改 plan 要换草稿名；目录结构补 `tools/jy_roughcut.py`、`tools/roughcut.py`、`skills/rough-cut/`。
- `README.md`：「装完之后能说什么」加两条（例如「分析 `E:\素材\第3集`，同镜多条 take 挑最好的一条，生成粗剪草稿」）。

- [ ] **Step 3: 全量测试与自检**

Run: `& $py -m unittest discover -s tests -t . -v`，Expected: 全绿
Run: `powershell -ExecutionPolicy Bypass -File scripts\validate_plugin.ps1`，Expected: `自检通过。`

- [ ] **Step 4: 端到端验收（需要用户本机剪映环境与一集真实素材）**

1. `roughcut.py scan` 清点一集素材
2. 会话内做命名推断 + `frames` 看片 + 写 plan
3. `roughcut.py validate` 0 error
4. `roughcut.py build` 出草稿，剪映打开核对：镜序、取用区间、无黑帧、无重叠、叠化正确
5. 按 `roughcut.md` 的备选 take 手工换一条，确认可溯源

- [ ] **Step 5: 提交**

```bash
git add .codex-plugin/plugin.json README.md 使用说明.md 工具清单.md docs/superpowers/plans/2026-11-02-smart-roughcut.md
git commit -m "docs: 0.8.0 智能粗剪说明与计划归档"
```

---

## 同步到宿主

仓库改完后，宿主里生效需要三步（属于交付动作，不是任务）：

1. 把 `E:\jianying-kit` 的改动同步到插件缓存目录（或经 Codex 插件侧栏重新安装本仓库）。
2. 完全退出并重启 Codex 桌面端。
3. 新会话里跑一次「列出我的剪映草稿」确认 MCP 连通，再说一句「把这集 AI 短剧素材粗剪成草稿」验证新技能被加载。
