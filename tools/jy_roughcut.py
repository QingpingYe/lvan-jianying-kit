"""智能粗剪的共享核心。

判断类工作（命名推断、看片、take 择优）在会话里由 AI 完成；本模块只做
确定性事务：清点探测、校验、转换、执行与报告。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

VIDEO_EXTS: tuple[str, ...] = (".mp4", ".mov", ".m4v", ".avi", ".mkv")
"""参与粗剪的视频扩展名（小写匹配）。"""


class RoughcutError(RuntimeError):
    """智能粗剪流程中的可预期错误。"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def probe_media(path: Path) -> dict[str, Any]:
    """读单个视频的元数据；时长以视频流为准（容器常比视频流多 1 帧）。

    视频流时长缺失时退回容器时长，并把「容器时长」写进 warning。
    """
    import av  # 惰性导入：测试注入 probe 时不依赖 av

    result: dict[str, Any] = {
        "duration": None,
        "width": None,
        "height": None,
        "fps": None,
        "has_audio": False,
        "warning": None,
    }
    with av.open(str(path)) as container:
        video = next((s for s in container.streams if s.type == "video"), None)
        if video is None:
            raise ValueError(f"没有视频流：{path.name}")
        if video.duration is not None and video.time_base is not None:
            result["duration"] = float(video.duration * video.time_base)
        elif container.duration is not None:
            result["duration"] = float(container.duration / av.time_base)
            result["warning"] = "视频流时长缺失，退回容器时长"
        if video.codec_context.width:
            result["width"] = int(video.codec_context.width)
        if video.codec_context.height:
            result["height"] = int(video.codec_context.height)
        if video.average_rate:
            result["fps"] = float(video.average_rate)
        result["has_audio"] = any(s.type == "audio" for s in container.streams)
    return result


def scan(
    root: Path,
    out: Path | None = None,
    *,
    probe: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """清点一集素材并写出 scan.json；探测失败只记 warning，不中断整批。"""
    root = Path(root).resolve()
    out = Path(out).resolve() if out is not None else root / "roughcut"
    probe = probe or probe_media
    clips: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_EXTS:
            skipped.append({"file": path.name, "reason": "非视频"})
            continue
        item: dict[str, Any] = {
            "file": path.name,
            "duration": None,
            "width": None,
            "height": None,
            "fps": None,
            "has_audio": False,
            "warning": None,
        }
        try:
            item.update(probe(path))
        except Exception as exc:  # noqa: BLE001 - 单文件探测失败不中断整批
            item["warning"] = f"探测失败：{exc}"
        clips.append(item)
    data = {
        "version": 1,
        "root": str(root),
        "generated": _now_iso(),
        "clips": clips,
        "skipped": skipped,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "scan.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data
