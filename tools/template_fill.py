#!/usr/bin/env python
"""模板批量出片：一份清单 JSON 进，N 个剪映草稿出。

用法：
    python template_fill.py --plan plan.json
    python template_fill.py --plan plan.json --dry-run
    python template_fill.py --plan plan.json --rows 1-3

清单格式见 examples/templatefill.example.json；退出码 0 = 全部成功，
1 = 有行失败，2 = 清单/参数/模板级别的错误（整批没跑）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jy_template  # noqa: E402


def parse_rows(value: str) -> set[int]:
    """解析 ``--rows``：支持 ``1-3``、``1,3`` 与混合写法，行号 1-based。"""
    picked: set[int] = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            picked.update(range(int(start), int(end) + 1))
        else:
            picked.add(int(chunk))
    if not picked:
        raise ValueError("没有解析出任何行号")
    return picked


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按清单把模板草稿批量套成多个草稿")
    parser.add_argument("--plan", required=True, help="清单 JSON 路径")
    parser.add_argument("--root", help="剪映草稿目录，默认读 jianying-paths.json 或自动探测")
    parser.add_argument("--rows", help="只跑指定行，例如 1-3 或 1,3（1-based）")
    parser.add_argument("--dry-run", action="store_true", help="只做预检，不写任何文件")
    return parser


def report_line(row: dict) -> str:
    mark = "ok" if row["draft_ok"] else "failed"
    text = f"[{mark}] {row['row']}"
    if row["error"]:
        text += f"：{row['error']}"
    return text


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    jy_template.apply_jianying_env()
    plan_path = Path(args.plan).expanduser().resolve()

    try:
        plan = jy_template.load_plan(plan_path)
    except FileNotFoundError:
        print(f"清单不存在：{plan_path}", file=sys.stderr)
        return 2
    except jy_template.TemplateError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        selected = parse_rows(args.rows) if args.rows else None
    except ValueError as exc:
        print(f"--rows 解析失败：{exc}", file=sys.stderr)
        return 2

    try:
        root = jy_template.resolve_root(args.root)
        report = jy_template.fill(
            root,
            plan,
            base_dir=plan_path.parent,
            dry_run=args.dry_run,
            rows=selected,
        )
    except jy_template.TemplateError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for row in report["rows"]:
        print(report_line(row), file=sys.stderr)
        for warning in row["warnings"]:
            print(f"    ! {warning}", file=sys.stderr)
        if row["unfilled_slots"]:
            print(f"    - 仍保留模板原样：{', '.join(row['unfilled_slots'])}", file=sys.stderr)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
