# 模板批量出片（剪同款流水线）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 jianying-kit 插件支持"拿一个现成草稿当模板，批量复制出 N 个草稿并逐份替换素材与文案"，批量走脚本、微调走 MCP 工具，两者共用同一份核心逻辑。

**Architecture:** 新增 `tools/jy_template.py` 作为唯一的核心实现（草稿读写、复制、槽位枚举、替换、批量编排）；`tools/template_fill.py` 是清单驱动的 CLI 薄包装，`mcp_ext/server.py` 是暴露 3 个工具的 MCP 薄包装。密文草稿统一走 `jianying_tool.native_codec` 解密 + 影子明文文件 + `ScriptFile._load_template` + `update_project_json` 回写的既有管线。

**Tech Stack:** Python 3.14（运行时 venv）、pyJianYingDraft 0.3.0、jianying-tool 0.5.0（`native_codec` / `inspect` / `metadata`）、`mcp` 2.3.0 的 `MCPServer`、标准库 `unittest`、PowerShell 启动器。

**Spec:** `docs/superpowers/specs/2026-11-02-template-batch-design.md`

## Global Constraints

- 开发仓库是 `E:\jianying-kit`（版本 0.6.0 → 0.7.0）；宿主实际加载的是 `C:\Users\lvan\.codex\plugins\cache\jianying-kit\jianying-kit\0.6.0\`。仓库在沙盒可写范围之外，所有落盘必须先把文件写进 `work\staging\`，再经用户批准拷贝进仓库。
- 运行时解释器：`C:\Users\lvan\.jianying-kit\.venv\Scripts\python.exe`（测试里用 `sys.executable`，不要写死路径）。
- 不新增任何 pip 依赖，不改 `install.ps1`；测试只用标准库 `unittest`（venv 里没有 pytest）。
- 不修改第三方包 `jianying_tool` / `pyJianYingDraft` 的任何文件。
- 复制草稿时必须排除 `.jianying-tool-backups`；草稿目录之外的素材路径原样保留、不拷入。
- `dump`/测试产物一律写在临时目录，不得写进 `D:\JianyingPro Drafts`（真实草稿库）之外的任何用户目录。
- Python 源文件用 UTF-8、ASCII 标点优先；中文注释与文档保留中文。
- 提交信息用 `feat:` / `test:` / `docs:` 前缀，一次提交对应一个任务。
- 术语固定：模板 = `template`，新草稿 = `target`，行 = `row`，槽位 = `slot`。

## Review Focus

1. **模板里存在同名轨道**（例如两条都叫 `text` 的文本轨）：必须明确失败并说明是哪条轨道歧义，绝不允许随便挑一条替换。
2. **`texts` 数组长度与该轨道片段数不一致**：必须整行失败，不允许只换前几个然后报告成功。
3. **目标草稿名已存在**：必须拒绝执行（除非显式允许覆盖），绝不覆盖用户已有草稿。
4. **模板引用的素材在草稿目录之外**：路径原样保留、文件不拷入草稿，复制后仍指向原素材。
5. **两行用了同一个草稿名**：第二行必须失败并指出与哪一行撞名，不允许后写覆盖先写。

---

### Task 1: 共享核心的草稿读写与复制

**Files:**
- Create: `tools/jy_template.py`
- Create: `tests/__init__.py`
- Create: `tests/test_jy_template.py`

**Interfaces:**
- Consumes: `jianying_tool.native_codec.decode_file/project_content_path/update_project_json`、`jianying_tool.metadata.write_draft_meta_info`、`jianying_tool.paths.draft_root`
- Produces:
  - `SEC: int = 1_000_000`
  - `BACKUP_DIR: str = ".jianying-tool-backups"`
  - `class TemplateError(RuntimeError)`
  - `class RowFailed(TemplateError)`，构造签名 `RowFailed(row: str, reason: str)`，属性 `.row` / `.reason`
  - `resolve_root(root: str | Path | None = None) -> Path`
  - `read_draft(root: Path, name: str) -> tuple[dict, str]`
  - `write_draft(root: Path, name: str, content: dict) -> None`
  - `duplicate(root: Path, template: str, target: str, *, allow_replace: bool = False) -> Path`
  - 测试辅助 `tests/test_jy_template.py::make_template_draft(root: Path, name: str, *, material_name: str = "占位.mp4", texts: tuple[str, ...] = ("第1集",), video_track: str = "main_video", text_track: str = "title") -> Path`（用 `object.__new__(VideoMaterial)` 造假素材，不碰真实媒体文件）

- [x] **Step 1: 写失败测试**

```python
import sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import jy_template  # noqa: E402


class DuplicateTests(unittest.TestCase):
    def test_duplicate_gives_the_copy_its_own_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            target = jy_template.duplicate(root, "模板", "第1集")
            src = json.loads((root / "模板" / "draft_meta_info.json").read_text(encoding="utf-8"))
            dst = json.loads((target / "draft_meta_info.json").read_text(encoding="utf-8"))
            self.assertNotEqual(src["draft_id"], dst["draft_id"])
            self.assertEqual(dst["draft_name"], "第1集")
            self.assertEqual(dst["draft_fold_path"], str(target).replace("\\", "/"))
```

- [x] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest discover -s tests -t . -v`（`$py` 为运行时 venv 的 python）
Expected: FAIL，`ModuleNotFoundError: No module named 'jy_template'`（或 `AttributeError: duplicate`）

- [x] **Step 3: 实现骨架**

`tools/jy_template.py` 里：

- `resolve_root` 按 `显式参数 > 环境变量 JIANYING_DRAFT_ROOT > jianying_tool.paths.draft_root()` 解析；返回值必须 `is_dir()`，否则抛 `TemplateError`。
- `read_draft`/`write_draft` 用 `project_content_path(project)` 定位时间线文件；`write_draft` 走 `update_project_json(project, lambda c: (c.clear(), c.update(content)))`，由它负责多时间线一致性校验与密文回写。
- `duplicate` 自己走 `shutil.copytree(template_path, target_path, ignore=shutil.ignore_patterns(BACKUP_DIR), dirs_exist_ok=allow_replace)`，然后调用 `write_draft_meta_info(target_path, root=root)`；模板不存在抛 `FileNotFoundError`，目标已存在且未允许覆盖抛 `FileExistsError`。

- [x] **Step 4: 跑测试确认通过**

Run: `& $py -m unittest discover -s tests -t . -v`
Expected: PASS

- [x] **Step 5: 补三条边界测试并让它们通过**

```python
    def test_duplicate_excludes_the_backup_directory(self): ...      # 模板里预置 .jianying-tool-backups/x.json，断言副本里没有
    def test_duplicate_refuses_an_existing_target(self): ...         # 先建同名草稿，断言 FileExistsError 且原草稿内容未变
    def test_duplicate_keeps_outside_media_paths_untouched(self): ... # 素材 path 写成 E:\素材\x.mp4，断言副本里的 path 完全一致且未新增文件
    def test_divergent_active_timeline_is_refused(self): ...          # 手工把 Timelines/<id>/draft_content.json 改成与根文件不同，
                                                                      # 断言 write_draft 抛 DraftDecodeError（沿用 update_project_json 的拒绝行为）
```

- [x] **Step 6: 跑测试并提交**

Run: `& $py -m unittest discover -s tests -t . -v`，Expected: PASS

```bash
git add tools/jy_template.py tests/__init__.py tests/test_jy_template.py
git commit -m "feat: 模板批量核心（草稿读写与复制）"
```

---

### Task 2: 槽位枚举

**Files:**
- Modify: `tools/jy_template.py`
- Modify: `tests/test_jy_template.py`

**Interfaces:**
- Consumes: Task 1 的 `read_draft`、`resolve_root`
- Produces: `list_slots(root: Path, draft: str) -> dict`，返回
  ```python
  {
    "draft": "模板名",
    "tracks": [
      {"name": "main_video", "type": "video", "replaceable": True, "ambiguous_name": False,
       "segments": [{"index": 0, "kind": "material", "material_name": "占位.mp4",
                     "material_path": "E:\\素材\\a.mp4", "target_start": 0.0, "duration": 2.0,
                     "source_start": 0.0, "source_duration": 2.0, "speed": 1.0}]},
      {"name": "title", "type": "text", "replaceable": True, "ambiguous_name": False,
       "segments": [{"index": 0, "kind": "text", "text": "第1集", "target_start": 0.0, "duration": 2.0}]},
    ],
  }
  ```
  时间一律换算成秒（微秒 / `SEC`，保留 6 位小数）。

- [x] **Step 1: 写失败测试**

```python
class SlotTests(unittest.TestCase):
    def test_lists_media_and_text_slots_in_seconds(self):
        # 造模板（1 个 2 秒视频片段 + 1 条 2 秒文本），断言 tracks 里两条都被列出、
        # duration == 2.0、kind 分别为 "material" / "text"

    def test_marks_unmodifiable_tracks_and_ambiguous_names(self):
        # 造两条同名文本轨：断言两条都 replaceable=True 且 ambiguous_name=True；
        # 再造一条特效轨：断言 replaceable=False
```

- [x] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_template.SlotTests -v`
Expected: FAIL，`AttributeError: module 'jy_template' has no attribute 'list_slots'`

- [x] **Step 3: 实现 `list_slots`**

直接用 `jianying_tool.inspect.inspect_draft(root / draft)`：它已经给出每段的 `index` / `material_name` / `material_path` / `target_timerange` / `source_timerange` / `speed` / `text`。`replaceable` 由 `pyJianYingDraft.track.TrackType` 的 `allow_modify` 决定（`video`/`audio`/`text` 为 True）；`ambiguous_name` 在"同类型同名字的轨道数 > 1"时为 True。轨道顺序按 `inspect_draft` 的 `tracks` 顺序原样保留。

- [x] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_template -v`，Expected: PASS

```bash
git add tools/jy_template.py tests/test_jy_template.py
git commit -m "feat: 模板槽位枚举"
```

---

### Task 3: 单行替换（影子明文 + 模板 API）

**Files:**
- Modify: `tools/jy_template.py`
- Modify: `tests/test_jy_template.py`

**Interfaces:**
- Consumes: Task 1 的 `read_draft`/`write_draft`/`RowFailed`；Task 2 的槽位模型
- Produces:
  - `_load_video_material(path: Path)` —— 生产实现为 `pyJianYingDraft.VideoMaterial(str(path))`；测试 monkeypatch 这个函数返回假素材
  - `apply_row(root: Path, plan: dict, row: dict, *, base_dir: Path) -> dict`，返回该行的替换明细：
    ```python
    {"row": "第1集", "draft": "<目标草稿路径>", "replaced": {"by_name": 1, "by_slot": 2, "texts": 3},
     "warnings": ["track 'main_video' 片段 0 被裁到 4.0s"], "unfilled_slots": ["title[1]"]}
    ```
  - `import_track(root: Path, source_draft: str, target_draft: str, track: str, *, offset: float = 0.0,
    new_name: str | None = None) -> dict`，返回 `{"source": ..., "target": ..., "track": ..., "imported": True}`

- [x] **Step 1: 写失败测试**

```python
class ApplyRowTests(unittest.TestCase):
    def test_replace_by_name_updates_every_referencing_segment(self): ...
    def test_replace_by_slot_reselects_the_source_range(self): ...
    def test_shorter_material_truncates_the_segment_and_warns(self): ...
    def test_text_replacement_keeps_the_style_material(self): ...
    def test_text_count_mismatch_fails_the_row(self): ...
    def test_by_name_shorter_than_the_shortest_segment_fails_the_row(self): ...
    def test_ambiguous_track_name_fails_the_row(self): ...
    def test_import_track_copies_the_track_and_its_materials(self): ...
    def test_import_track_refuses_a_name_that_already_exists(self): ...
```

`test_shorter_material_truncates_the_segment_and_warns` 的断言：原片段 target 时长 2.0s，新素材 1.0s（假素材 duration 设为 `1_000_000`），替换后 `target_timerange.duration == 1_000_000`，且 `warnings` 里出现该轨道名与片段序号。

- [x] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_template.ApplyRowTests -v`
Expected: FAIL，`AttributeError: module 'jy_template' has no attribute 'apply_row'`

- [x] **Step 3: 实现影子管线与三类替换**

`_edit_with_shadow(project: Path, operation) -> None`：

1. `content, _ = read_draft(project.parent, project.name)`
2. 写影子文件 `project / f".draft_content.jy-template-{uuid4().hex}.json"`（`json.dumps(content, ensure_ascii=False, indent=4)`）
3. `script = ScriptFile._load_template(str(shadow))`；`operation(script)`；`script.save()`
4. 读回影子明文 → `write_draft(project.parent, project.name, updated)`
5. `finally` 里删影子文件

三类替换（都从 `script.get_imported_track(...)` 取轨道）：

- `by_name`：`script.replace_material_by_name(名字, _load_video_material(路径))`；替换前先查该素材名被引用片段的最短 `target_timerange.duration`，新素材更短则抛 `RowFailed`
- `by_slot`：`script.replace_material_by_seg(track, index, material, source_timerange, handle_shrink=ShrinkMode.cut_tail, handle_extend=ExtendMode.cut_material_tail)`，`source_timerange` 由 `source_start`/`duration` 换算成微秒（缺省则不传）
- `texts`：按轨道名取文本轨，长度与片段数不一致直接 `RowFailed`，否则逐段 `script.replace_text(track, index, text)`（默认 `recalc_style=True`）

`import_track` 走同一条影子管线，但目标是"另一个草稿"：用 `DraftFolder(root).load_template(source_draft)` 取源脚本，
`source.get_imported_track(...)` 找到那条轨道，再在目标草稿的脚本上 `script.import_track(source, track, offset=..., new_name=...)`。
目标里已存在同名轨道或同一素材 id 时抛 `TemplateError`（底层保留素材 id，明确不支持重复导入）。

- [x] **Step 4: 跑测试确认通过**

Run: `& $py -m unittest tests.test_jy_template.ApplyRowTests -v`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add tools/jy_template.py tests/test_jy_template.py
git commit -m "feat: 模板单行替换"
```

---

### Task 4: 整批编排与报告

**Files:**
- Modify: `tools/jy_template.py`
- Modify: `tests/test_jy_template.py`

**Interfaces:**
- Consumes: Task 1 的 `duplicate`、Task 3 的 `apply_row`、`RowFailed`
- Produces:
  - `load_plan(path: Path) -> dict`（校验 `template` 与 `rows` 必填、`rows` 非空）
  - `fill(root: Path, plan: dict, *, base_dir: Path, dry_run: bool = False, rows: set[int] | None = None) -> dict`，返回
    ```python
    {"template": "模板", "rows": [{"row": "第1集", "draft": "第1集", "draft_ok": True,
      "error": None, "replaced": {...}, "warnings": [...], "unfilled_slots": [...]}],
     "ok_count": 1, "failed_count": 0}
    ```
  - `rows` 参数是 1-based 行号集合，`None` 表示全部

- [x] **Step 1: 写失败测试**

```python
class FillTests(unittest.TestCase):
    def test_failed_row_does_not_stop_the_batch(self): ...     # 第 1 行素材缺失、第 2 行正常 → 第 2 行仍产出，failed_count == 1
    def test_duplicate_row_names_fail_the_second_row(self): ...  # 两行同名 → 第二行 error 指出与第一行撞名
    def test_dry_run_writes_nothing(self): ...                   # 运行前后目录树 + mtime 完全一致
    def test_rows_filter_runs_only_the_selected_rows(self): ...
    def test_missing_template_fails_the_whole_plan(self): ...     # 抛 TemplateError，不产出任何草稿
    def test_existing_target_draft_fails_that_row(self): ...      # 目标名已被别的草稿占用 → 该行失败且不覆盖
    def test_partial_row_lists_unfilled_slots_as_warnings(self): ...
    def test_require_all_slots_turns_warnings_into_a_failed_row(self): ...  # 顶层 "require_all_slots": true
```

- [x] **Step 2: 跑测试确认失败**

Run: `& $py -m unittest tests.test_jy_template.FillTests -v`
Expected: FAIL，`AttributeError: module 'jy_template' has no attribute 'fill'`

- [x] **Step 3: 实现 `fill`**

预检（`dry_run` 也执行，且只读）：模板目录存在 → 逐行解析目标名（缺省 `<template>-<行号>`）→ 检查目标不重名、不撞已有草稿 → 检查所有素材文件存在（相对路径按 `base_dir` 解析）→ 解析轨道与槽位。

执行：逐行 `duplicate` → `apply_row` → 汇总。任何一行抛 `RowFailed` / `FileNotFoundError` / `AttributeError`（`AmbiguousTrack`、`AmbiguousMaterial`）只让该行失败，异常文本写进 `error`，批继续。`dry_run=True` 时只跑预检并返回同一形状的报告。

未填槽位：把清单**没有覆盖**的槽位逐条写进该行 `unfilled_slots`；清单顶层 `"require_all_slots": true` 时，
非空 `unfilled_slots` 直接让该行失败（错误文案要指出第一个未填槽位）。

- [x] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest tests.test_jy_template -v`，Expected: PASS

```bash
git add tools/jy_template.py tests/test_jy_template.py
git commit -m "feat: 模板批量编排与报告"
```

---

### Task 5: CLI、示例清单与技能

**Files:**
- Create: `tools/template_fill.py`
- Create: `examples/templatefill.example.json`
- Create: `skills/template-batch/SKILL.md`
- Modify: `tests/test_jy_template.py`

**Interfaces:**
- Consumes: `jy_template.load_plan` / `fill` / `resolve_root` / `TemplateError`
- Produces: 命令行 `python tools/template_fill.py --plan <清单> [--root <草稿目录>] [--rows 1-3] [--dry-run] [--python <解释器>]`；stdout 最后一段是一个 JSON 报告，有失败行时退出码 1、预检失败时退出码 2

- [x] **Step 1: 写失败测试**

```python
class CliTests(unittest.TestCase):
    def test_dry_run_reports_ok_and_writes_nothing(self):
        # 用 sys.executable 跑 tools/template_fill.py --plan <临时清单> --root <临时草稿根> --dry-run
        # 断言 returncode == 0、stdout 能解析出 JSON 报告、目录树未变
    def test_missing_plan_file_exits_2(self): ...
```

- [x] **Step 2: 跑测试确认失败并实现**

Run: `& $py -m unittest tests.test_jy_template.CliTests -v`，Expected: FAIL（脚本不存在）

实现要点：`--rows` 解析 `1-3` 与 `1,3` 两种写法；复用 `build_draft.py` 的既有约定（读同级或上级的 `jianying-paths.json` 注入 `JIANYING_INSTALL_ROOT` / `JY_INSTALL_DIR` / `JIANYING_DRAFT_ROOT`；`--python` 未给时用 `sys.executable`）；人类可读的逐行进度写 stderr，JSON 报告写 stdout。

- [x] **Step 3: 示例清单与技能**

`examples/templatefill.example.json` 按设计文档 §9 的字段给出可读示例（模板名、defaults、两行、by_name/by_slot/texts 各有出现）。

`skills/template-batch/SKILL.md` 的 frontmatter `name: template-batch`，description 要写明触发场景（"按模板批量出片""剪同款""套模板出 N 集"）。正文覆盖：路径解析（runtime.json / `<root>`）、先 `list_template_slots` 再写清单、清单字段表、跑 `template_fill.py`、怎么读报告（截断警告、失败行、未填槽位）、禁止事项（剪映开着目标草稿时不要跑、不要填满不打算换的槽位）。

- [x] **Step 4: 跑测试确认通过并提交**

Run: `& $py -m unittest discover -s tests -t . -v`，Expected: PASS

```bash
git add tools/template_fill.py examples/templatefill.example.json skills/template-batch/SKILL.md tests/test_jy_template.py
git commit -m "feat: 模板批量出片脚本与技能"
```

---

### Task 6: MCP 工具与启动器

**Files:**
- Create: `mcp_ext/__init__.py`
- Create: `mcp_ext/server.py`
- Modify: `.mcp.json`
- Modify: `scripts/launch_mcp.ps1`
- Modify: `scripts/launch_mcp.cmd`
- Modify: `scripts/validate_plugin.ps1`
- Modify: `tests/test_jy_template.py`

**Interfaces:**
- Consumes: `jy_template` 全部公开函数
- Produces:
  - `mcp_ext/server.py` 里三个普通函数（MCP 注册用的就是它们本身）：`list_template_slots(draft: str, root: str | None = None) -> dict`、`duplicate_local_draft(source: str, target: str, root: str | None = None, allow_replace: bool = False) -> dict`、`import_local_track(source_draft: str, target_draft: str, track: str, root: str | None = None, offset: float = 0.0, new_name: str | None = None) -> dict`
  - `build_server() -> MCPServer`，把上面三个函数注册进去
  - `.mcp.json` 增加第二个 server，键名 `jianying-ext`

- [x] **Step 1: 写失败测试**

```python
class McpExtTests(unittest.TestCase):
    def test_build_server_registers_the_three_tools(self):
        # asyncio.run(server.list_tools()) 的函数名集合 == {"list_template_slots", "duplicate_local_draft", "import_local_track"}
    def test_duplicate_tool_returns_the_new_draft_path(self): ...
    def test_tools_reject_a_missing_source_draft(self): ...
```

- [x] **Step 2: 跑测试确认失败并实现服务**

Run: `& $py -m unittest tests.test_jy_template.McpExtTests -v`，Expected: FAIL（`ModuleNotFoundError: mcp_ext`）

`mcp_ext/server.py` 顶部先 `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))` 再 `import jy_template`；`if __name__ == "__main__": build_server().run()`（与 `jianying_tool.mcp_server` 的启动方式一致）。三个工具都只是 `jy_template` 的转发：`list_template_slots` → `list_slots`，`duplicate_local_draft` → `duplicate`，`import_local_track` → `import_track`；`root` 参数统一走 `resolve_root(root)`，`TemplateError` 直接向上抛（MCP 层会把它变成工具错误）。

- [x] **Step 3: 接进宿主加载路径**

`scripts/launch_mcp.ps1` 增加 `param([string]$Module = "jianying_tool.mcp_server")`，把 `-Module` 值传给 `& $python -m $Module`，并在启动前把插件根写进 `$env:PYTHONPATH`（保留已有行为的默认值，老配置不传参数时完全不变）。

`scripts/launch_mcp.cmd` 把 `%~2` 透传给 ps1（为空时不传该参数）。

`.mcp.json` 增加：

```json
"jianying-ext": {
  "command": "cmd.exe",
  "args": ["/d", "/s", "/c", "call",
           "${CLAUDE_PLUGIN_ROOT}/scripts/launch_mcp.cmd",
           "${CLAUDE_PLUGIN_ROOT}/scripts/launch_mcp.ps1",
           "mcp_ext.server"],
  "cwd": "${CLAUDE_PLUGIN_ROOT}",
  "startup_timeout_sec": 120
}
```

`scripts/validate_plugin.ps1`：技能清单加 `template-batch`；`.mcp.json` 检查里断言 `jianying-ext` 存在且模块名是 `mcp_ext.server`。

- [x] **Step 4: 跑测试与结构自检**

Run: `& $py -m unittest discover -s tests -t . -v`，Expected: PASS
Run: `powershell -ExecutionPolicy Bypass -File scripts\validate_plugin.ps1`，Expected: `自检通过。`

- [x] **Step 5: 提交**

```bash
git add mcp_ext .mcp.json scripts/launch_mcp.ps1 scripts/launch_mcp.cmd scripts/validate_plugin.ps1 tests/test_jy_template.py
git commit -m "feat: 模板能力接入 MCP 工具层"
```

---

### Task 7: 版本、文档与端到端验收

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Modify: `README.md`
- Modify: `使用说明.md`
- Modify: `工具清单.md`
- Create: `docs/superpowers/specs/2026-11-02-template-batch-design.md`（从 staging 拷入）
- Create: `docs/superpowers/plans/2026-11-02-template-batch.md`（本文件）

**Interfaces:**
- Consumes: 前六个任务的全部产出
- Produces: 0.7.0 的插件包与三份用户文档

- [x] **Step 1: 版本与能力描述**

`.codex-plugin/plugin.json`：`version` 改 `0.7.0`；`longDescription` 里"71 MCP tools"改成 `74 MCP tools`（原 71 + 3），并把 skills 列表补上模板批量出片。

- [x] **Step 2: 三份文档**

- `工具清单.md`：新增一节"十五、模板批量出片（新增 3 个工具）"，列出三个工具与 `template_fill.py` 脚本；"覆盖情况小结"的工具总数从 71 改成 74。
- `使用说明.md`：日常使用一节加"5.7 用模板批量出片"，给出完整的清单 JSON 示例、`--dry-run` 先看报告的建议、"剪映开着目标草稿不要跑"的提醒；"目录结构"补 `tools/jy_template.py`、`tools/template_fill.py`、`mcp_ext/`、`skills/template-batch/`。
- `README.md`：把新能力加进"装完之后能说什么"，例如「用《模板》按这份清单套 10 条」。

- [x] **Step 3: 端到端验收（需要用户本机剪映环境）**（真跑已做，剩用户在剪映首页核对）

1. `& $py -m unittest discover -s tests -t .`，Expected: 全绿
2. `powershell -ExecutionPolicy Bypass -File scripts\validate_plugin.ps1`，Expected: `自检通过。`
3. 对真实草稿跑槽位枚举：`& $py tools\template_fill.py --plan <3 行清单> --root "D:\JianyingPro Drafts" --dry-run`，人工核对槽位与目标名
4. 真跑一次 3 行清单 → 在剪映首页确认 3 个草稿可打开、时长正确、样式与模板一致、素材已换
5. 把结果写回本任务说明，并把 spec/plan 拷进仓库 `docs/superpowers/`

- [x] **Step 4: 提交**

```bash
git add .codex-plugin/plugin.json README.md 使用说明.md 工具清单.md docs
git commit -m "docs: 0.7.0 模板批量出片说明与设计归档"
```

---

## 同步到宿主

仓库改完后，宿主里生效需要三步（属于交付动作，不是任务）：

1. 把 `E:\jianying-kit` 的改动同步到 `C:\Users\lvan\.codex\plugins\cache\jianying-kit\jianying-kit\0.7.0\`（或在 Codex 插件侧栏重新安装本仓库）。
2. 完全退出并重启 Codex 桌面端。
3. 新会话里说「列出我的剪映草稿」确认 MCP 仍然连通，再说一句模板批量出片的话验证新技能被加载。
