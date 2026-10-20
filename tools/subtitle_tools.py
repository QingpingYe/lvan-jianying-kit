#!/usr/bin/env python
"""剪映字幕批量工具：导出 / 批量回写（保留样式与时间轴）。

配合剪映自带的「识别字幕」使用：
  1. 在剪映里：文本 → 智能字幕 → 识别字幕（人工点一次，用剪映自己的 ASR）
  2. 导出：python subtitle_tools.py dump  --draft "<草稿名或路径>" --out subs.tsv
  3. 改文本：用 Excel/WPS/文本编辑器改 text 列（可对照剧本逐句校正）
  4. 回写：python subtitle_tools.py apply --draft "<草稿名或路径>" --tsv subs.tsv

只改文字内容，不动时间轴、字体、描边、位置等样式。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from jianying_tool.native_codec import decode_file, project_content_path
from jianying_tool.timeline import update_project_json

TEXT_TRACK_TYPES = {"text", "subtitle"}
HEADER = ["track", "index", "start", "end", "text"]


def load_jianying_paths() -> dict[str, str]:
    """读取安装器写入的 jianying-paths.json（剪映或草稿目录不在默认位置时用）。"""
    here = Path(__file__).resolve().parent
    for base in (here.parent, here):
        path = base / "jianying-paths.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {k: str(v) for k, v in data.items() if isinstance(v, str) and v}
    return {}


# 让读取加密草稿的解码逻辑也能拿到 JY_INSTALL_DIR
for _key, _value in load_jianying_paths().items():
    os.environ.setdefault(_key, _value)


def default_draft_root() -> Path:
    env_root = os.environ.get("JIANYING_DRAFT_ROOT")
    if env_root:
        return Path(env_root)
    recorded = load_jianying_paths().get("JIANYING_DRAFT_ROOT")
    if recorded:
        return Path(recorded)
    return Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro/User Data/Projects/com.lveditor.draft"


def resolve_project(draft: str, root: Path | None) -> Path:
    p = Path(draft)
    if p.is_dir():
        return p.resolve()
    base = (root or default_draft_root()).resolve()
    candidate = base / draft
    if not candidate.is_dir():
        raise SystemExit(f"找不到草稿：{draft}（已尝试 {candidate}）")
    return candidate


def load_text(content: dict) -> str:
    materials = {m.get("id"): m for m in content.get("materials", {}).get("texts", [])}
    rows = []
    for track in content.get("tracks", []):
        if track.get("type") not in TEXT_TRACK_TYPES:
            continue
        for i, seg in enumerate(track.get("segments", [])):
            material = materials.get(seg.get("material_id"))
            text = ""
            if material is not None:
                raw = material.get("content", "{}")
                try:
                    body = json.loads(raw) if isinstance(raw, str) else dict(raw)
                    text = body.get("text", "")
                except (TypeError, json.JSONDecodeError):
                    text = "<解析失败>"
            tr = seg.get("target_timerange") or {}
            start = int(tr.get("start", 0)) / 1e6
            end = (int(tr.get("start", 0)) + int(tr.get("duration", 0))) / 1e6
            rows.append([track.get("name", ""), i, f"{start:.3f}", f"{end:.3f}", text])
    return rows


def cmd_dump(args: argparse.Namespace) -> None:
    project = resolve_project(args.draft, args.root)
    content, _ = decode_file(project_content_path(project))
    rows = load_text(content)
    if not rows:
        raise SystemExit("这个草稿里没有文字/字幕轨道")
    out = Path(args.out) if args.out else None
    if out is None:
        print("\t".join(HEADER))
        for r in rows:
            print("\t".join(str(x) for x in r))
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
    print(f"已导出 {len(rows)} 条字幕 -> {out}")


def cmd_apply(args: argparse.Namespace) -> None:
    project = resolve_project(args.draft, args.root)
    tsv = Path(args.tsv)
    if not tsv.is_file():
        raise SystemExit(f"找不到 TSV：{tsv}")

    updates: dict[tuple[str, int], str] = {}
    with tsv.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            track = (row.get("track") or "").strip()
            index = int((row.get("index") or "").strip())
            updates[(track, index)] = text
    if not updates:
        raise SystemExit("TSV 里没有可回写的文本")

    changed = 0
    missing: list[str] = []

    def mutate(content: dict) -> None:
        nonlocal changed
        materials = {m.get("id"): m for m in content.get("materials", {}).get("texts", [])}
        tracks = {t.get("name"): t for t in content.get("tracks", [])}
        for (track_name, index), text in updates.items():
            track = tracks.get(track_name)
            if track is None or track.get("type") not in TEXT_TRACK_TYPES:
                missing.append(f"{track_name}#{index}（轨道不存在）")
                continue
            segments = track.get("segments", [])
            if not 0 <= index < len(segments):
                missing.append(f"{track_name}#{index}（序号越界）")
                continue
            material = materials.get(segments[index].get("material_id"))
            if material is None:
                missing.append(f"{track_name}#{index}（找不到文本素材）")
                continue
            body = json.loads(material["content"]) if isinstance(material.get("content"), str) else dict(material.get("content") or {})
            old = body.get("text", "")
            if old == text:
                continue
            body["text"] = text
            old_len, new_len = len(old), len(text)
            for style in body.get("styles", []):
                span = style.get("range")
                if not isinstance(span, list) or len(span) != 2:
                    continue
                start, end = int(span[0]), int(span[1])
                if start == 0 and end == old_len:
                    style["range"] = [0, new_len]
                else:
                    style["range"] = [min(start, new_len), min(max(start, end), new_len)]
            material["content"] = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            changed += 1

    if args.dry_run:
        content, _ = decode_file(project_content_path(project))
        mutate(content)
        print(f"[dry-run] 将更新 {changed} 条，跳过 {len(missing)} 条")
        for item in missing:
            print(f"  跳过 {item}")
        return

    update_project_json(project, mutate)
    print(f"已更新 {changed} 条字幕（样式与时间轴保持不变）")
    for item in missing:
        print(f"  跳过 {item}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="剪映字幕导出/批量回写")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("dump", help="导出字幕为 TSV")
    d.add_argument("--draft", required=True, help="草稿名或草稿文件夹路径")
    d.add_argument("--root", type=Path, help="草稿根目录")
    d.add_argument("--out", help="输出 TSV（不填则打印到终端）")
    d.set_defaults(func=cmd_dump)

    a = sub.add_parser("apply", help="把 TSV 里的文本回写到草稿")
    a.add_argument("--draft", required=True)
    a.add_argument("--root", type=Path)
    a.add_argument("--tsv", required=True)
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
