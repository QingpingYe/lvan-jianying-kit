#!/usr/bin/env python
"""为一个已存在但缺少 draft_content.json 的草稿文件夹补一份空草稿内容。

用于修复"建草稿中途失败、文件夹和索引条目已产生"的情况：不删文件夹、
不改 draft_meta_info.json（保住剪映索引里的 draft_id），只补内容文件。

用法：
    python new_empty_content.py "<草稿文件夹>" [--width 1280] [--height 720] [--fps 24]
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

import pyJianYingDraft as draft


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("draft_dir")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=24)
    args = ap.parse_args()

    folder = Path(args.draft_dir)
    if not folder.is_dir():
        raise SystemExit(f"文件夹不存在：{folder}")
    content_path = folder / "draft_content.json"
    if content_path.exists():
        raise SystemExit(f"已存在内容文件，拒绝覆盖：{content_path}")

    script = draft.ScriptFile(args.width, args.height, args.fps, True)
    script.save_path = str(content_path)
    script.save()

    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["id"] = str(uuid.uuid4()).upper()
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"已补空草稿内容：{content_path}")
    print(f"  canvas={args.width}x{args.height} fps={args.fps} id={content['id']}")


if __name__ == "__main__":
    main()
