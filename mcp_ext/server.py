"""模板批量出片与智能粗剪的 MCP 工具层。

工具都是 ``tools/jy_template.py`` / ``tools/jy_roughcut.py`` 的薄包装：真正的读写、
替换、校验、编排逻辑在那边，这里只负责把参数接过来、把结果变成 MCP 能序列化的形状。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import jy_template  # noqa: E402
import jy_roughcut  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402


def _root(value: str | None) -> Path:
    return jy_template.resolve_root(value)


def _read_json(path: Path) -> Any:
    """读用户 JSON：兼容 Windows PowerShell 5.1 写出的 UTF-8 BOM。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def list_template_slots(draft: str, root: str | None = None) -> dict[str, Any]:
    """列出某个草稿的可替换槽位：轨道、片段下标、当前素材名与文案，以及能不能替换。"""
    return jy_template.list_slots(_root(root), draft)


def duplicate_local_draft(
    source: str, target: str, root: str | None = None, allow_replace: bool = False
) -> dict[str, Any]:
    """把 source 草稿复制成 target 草稿，并给副本一个独立的草稿身份。

    复制会排除 ``.jianying-tool-backups``（工具自身的写前备份），否则每复制一份都会把
    历史备份一起带上。已有同名草稿时默认拒绝，不覆盖用户已有草稿。
    """
    path = jy_template.duplicate(_root(root), source, target, allow_replace=allow_replace)
    return {"draft": str(path), "name": target}


def import_local_track(
    source_draft: str,
    target_draft: str,
    track: str,
    root: str | None = None,
    offset: float = 0.0,
    new_name: str | None = None,
) -> dict[str, Any]:
    """把一条轨道从 source_draft 整条搬进 target_draft（素材条目随轨道一起过去）。

    ``offset`` 是整条轨道的时间平移量（秒）。目标已有同名轨道、或已有同 id 素材时直接
    报错：底层保留素材 id，同一条轨道不能重复导入。
    """
    return jy_template.import_track(
        _root(root), source_draft, target_draft, track, offset=offset, new_name=new_name
    )


def scan_roughcut_folder(folder: str, out: str | None = None) -> dict[str, Any]:
    """清点一集素材并写出 scan.json，供 AI 做命名推断与看片。"""
    return jy_roughcut.scan(
        Path(folder).expanduser(),
        Path(out).expanduser() if out else None,
    )


def validate_roughcut_plan(plan_path: str, grouping_path: str | None = None) -> dict[str, Any]:
    """校验分组表与决策表；scan.json / grouping.json 默认从 plan 同目录读。"""
    plan_file = Path(plan_path).expanduser()
    plan = _read_json(plan_file)
    grouping_file = (
        Path(grouping_path).expanduser() if grouping_path else plan_file.parent / "grouping.json"
    )
    grouping = _read_json(grouping_file)
    scan = _read_json(plan_file.parent / "scan.json")
    g = jy_roughcut.validate_grouping(grouping, scan, check_files=True)
    p = jy_roughcut.validate_plan(plan, scan, grouping, check_files=True)
    return {
        "ok": g["ok"] and p["ok"],
        "errors": g["errors"] + p["errors"],
        "warnings": g["warnings"] + p["warnings"],
    }


def build_roughcut_draft(
    plan_path: str,
    draft_name: str | None = None,
    existing: bool = False,
    out: str | None = None,
    force: bool = False,
    replace_timeline: bool = False,
    draft_closed: bool = False,
) -> dict[str, Any]:
    """按决策表建剪映草稿（含叠化与静音），返回 build 报告。

    ``existing`` 只保证在空草稿上正确追加；``replace_timeline`` 允许在同一草稿里
    清空计划接管的轨道后重建；``draft_closed`` 表示用户已确认在剪映里关闭该草稿；
    ``force`` 才会跳过剪映占用/非空保护。
    """
    plan_file = Path(plan_path).expanduser()
    plan = _read_json(plan_file)
    out_dir = Path(out).expanduser() if out else plan_file.parent
    grouping = _read_json(out_dir / "grouping.json")
    scan = _read_json(out_dir / "scan.json")
    return jy_roughcut.build(
        plan, scan, grouping, out_dir,
        draft_name=draft_name, existing=existing, force=force,
        replace_timeline=replace_timeline, draft_closed=draft_closed,
    )


def build_server() -> MCPServer:
    """建好带六个工具的服务器；启动前先把剪映路径注入环境变量。"""
    jy_template.apply_jianying_env()
    server = MCPServer(
        "jianying-ext",
        title="Jianying Templates",
        description="模板批量出片与智能粗剪：列槽位、复制草稿、搬轨道、清点素材、校验决策表、按决策表建草稿。",
    )
    for tool in (
        list_template_slots,
        duplicate_local_draft,
        import_local_track,
        scan_roughcut_folder,
        validate_roughcut_plan,
        build_roughcut_draft,
    ):
        server.tool()(tool)
    return server


def main() -> int:
    build_server().run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
