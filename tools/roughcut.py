#!/usr/bin/env python
"""智能粗剪命令行：scan / frames / validate / build / report。

判断类工作（命名推断、看片、take 择优）在会话里由 AI 完成；本脚本只跑
确定性事务。退出码：0 成功、1 校验/结构错误、2 用法或 IO/编码错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jy_roughcut  # noqa: E402


class _StructureError(RuntimeError):
    """输入 JSON 的顶层结构不对（不是对象）。"""


def _load(path) -> object:
    """读用户 JSON：兼容 Windows PowerShell 5.1 写出的 UTF-8 BOM。"""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _expect_dict(data: object, label: str) -> dict:
    if not isinstance(data, dict):
        raise _StructureError(f"{label} 必须是 JSON 对象，实际是 {type(data).__name__}")
    return data


def _out_from_plan(plan: dict, explicit: str | None, plan_path) -> Path:
    if explicit:
        return Path(explicit)
    root = plan.get("source_root")
    if isinstance(root, str) and root.strip():
        return Path(root) / "roughcut"
    return Path(plan_path).resolve().parent


def _out_from_grouping(grouping: dict, explicit: str | None, grouping_path) -> Path:
    if explicit:
        return Path(explicit)
    root = grouping.get("root")
    if isinstance(root, str) and root.strip():
        return Path(root) / "roughcut"
    return Path(grouping_path).resolve().parent


def _print(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _validated(scan: dict, grouping: dict, plan: dict) -> dict:
    g = jy_roughcut.validate_grouping(grouping, scan, check_files=True)
    p = jy_roughcut.validate_plan(plan, scan, grouping, check_files=True)
    return {
        "ok": g["ok"] and p["ok"],
        "errors": g["errors"] + p["errors"],
        "warnings": g["warnings"] + p["warnings"],
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    parser = argparse.ArgumentParser(description="AI 短剧智能粗剪")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--source", required=True)
    p.add_argument("--out")

    p = sub.add_parser("frames")
    p.add_argument("--grouping", required=True)
    p.add_argument("--out")
    p.add_argument("--count", type=int, default=5)

    p = sub.add_parser("validate")
    p.add_argument("--grouping", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--out")

    p = sub.add_parser("build")
    p.add_argument("--plan", required=True)
    p.add_argument("--grouping")
    p.add_argument("--name")
    p.add_argument("--existing", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")

    p = sub.add_parser("report")
    p.add_argument("--plan", required=True)
    p.add_argument("--grouping")
    p.add_argument("--out")

    args = parser.parse_args(argv)
    try:
        try:
            import jy_template

            jy_template.apply_jianying_env()
        except Exception:  # noqa: BLE001 - 注入失败不阻塞纯校验
            pass

        if args.command == "scan":
            data = jy_roughcut.scan(Path(args.source), Path(args.out) if args.out else None)
            _print({"ok": True, "clips": len(data["clips"]), "skipped": len(data["skipped"])})
            return 0

        if args.command == "frames":
            grouping = _expect_dict(_load(args.grouping), "grouping")
            out = _out_from_grouping(grouping, args.out, args.grouping)
            scan = _expect_dict(_load(out / "scan.json"), "scan")
            result = jy_roughcut.frames(grouping, scan, out, count=args.count)
            _print({"ok": True, **result})
            return 0

        if args.command == "validate":
            plan = _expect_dict(_load(args.plan), "plan")
            grouping = _expect_dict(_load(args.grouping), "grouping")
            out = _out_from_plan(plan, args.out, args.plan)
            scan = _expect_dict(_load(out / "scan.json"), "scan")
            result = _validated(scan, grouping, plan)
            _print(result)
            return 0 if result["ok"] else 1

        if args.command == "build":
            plan = _expect_dict(_load(args.plan), "plan")
            out = _out_from_plan(plan, args.out, args.plan)
            grouping = _expect_dict(
                _load(args.grouping) if args.grouping else _load(out / "grouping.json"),
                "grouping",
            )
            scan = _expect_dict(_load(out / "scan.json"), "scan")
            result = _validated(scan, grouping, plan)
            if not result["ok"]:
                _print(result)
                return 1
            built = jy_roughcut.build(
                plan,
                scan,
                grouping,
                out,
                draft_name=args.name,
                existing=args.existing,
                dry_run=args.dry_run,
            )
            _print({"ok": True, **built})
            return 0

        if args.command == "report":
            plan = _expect_dict(_load(args.plan), "plan")
            out = _out_from_plan(plan, args.out, args.plan)
            grouping = None
            if args.grouping:
                grouping = _expect_dict(_load(args.grouping), "grouping")
            else:
                default_grouping = out / "grouping.json"
                if default_grouping.is_file():
                    grouping = _expect_dict(_load(default_grouping), "grouping")
            text = jy_roughcut.report(plan, grouping)
            out.mkdir(parents=True, exist_ok=True)
            report_path = out / "roughcut.md"
            report_path.write_text(text, encoding="utf-8")
            _print({"ok": True, "report": str(report_path)})
            return 0
    except _StructureError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        jy_roughcut.RoughcutError,
        KeyError,
    ) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
