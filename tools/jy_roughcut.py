"""智能粗剪的共享核心。

判断类工作（命名推断、看片、take 择优）在会话里由 AI 完成；本模块只做
确定性事务：清点探测、校验、转换、执行与报告。
"""

from __future__ import annotations

import json
from collections import Counter
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


def _take_files(grouping: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for shot in grouping.get("shots", []):
        for take in shot.get("takes", []):
            files.extend(take.get("files") or [])
    return files


def validate_grouping(grouping: dict[str, Any], scan: dict[str, Any]) -> dict[str, Any]:
    """校验命名推断产物的分组表：覆盖率、结构、segmented 顺序。"""
    errors: list[str] = []
    warnings: list[str] = []
    known = {c["file"] for c in scan.get("clips", [])}
    skipped = {s["file"] for s in scan.get("skipped", [])}
    shot_files = _take_files(grouping)
    unresolved = [item.get("file") for item in grouping.get("unresolved", [])]
    excluded = [item.get("file") for item in grouping.get("excluded", [])]
    counts = Counter(shot_files + unresolved + excluded)

    for name, times in sorted(counts.items()):
        if times > 1:
            errors.append(f"文件被覆盖 {times} 次（应为 1 次）：{name}")
        if name not in known and name not in skipped:
            errors.append(f"引用了 scan 里不存在的文件：{name}")
    for name in shot_files + unresolved:
        if name in skipped:
            errors.append(f"非视频文件不能作为素材：{name}")
    for name in sorted(known):
        if counts.get(name, 0) == 0:
            errors.append(f"素材未被覆盖：{name}")

    for shot in grouping.get("shots", []):
        sid = shot.get("shot")
        if not sid:
            errors.append("shots 里存在空 shot 标识")
        takes = shot.get("takes") or []
        if not takes:
            errors.append(f"shot {sid}: 没有任何 take")
        for take in takes:
            tid = take.get("take")
            if not tid:
                errors.append(f"shot {sid}: take 标识为空")
            files = take.get("files") or []
            if not files:
                errors.append(f"shot {sid} take {tid}: files 为空")
            kind = take.get("kind")
            if kind not in {"single", "segmented"}:
                errors.append(f"shot {sid} take {tid}: kind 非法：{kind}")
            elif kind == "segmented" and len(files) < 2:
                warnings.append(f"shot {sid} take {tid}: segmented 但只有一个文件")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def plan_to_shotlist(plan: dict[str, Any], *, draft_name: str | None = None) -> dict[str, Any]:
    """把剪辑决策表摊平成 build_draft.py 能吃的分镜表。"""
    name = draft_name or plan.get("draft_name") or f"{plan['episode']}-粗剪"
    shots: list[dict[str, Any]] = []
    cursor = 0.0
    for entry in plan.get("shots") or []:
        for seg in entry.get("segments") or []:
            duration = float(seg["duration"])
            shots.append(
                {
                    "file": seg["file"],
                    "source_start": float(seg.get("source_start", 0.0)),
                    "duration": duration,
                    "target_start": round(cursor, 6),
                }
            )
            cursor += duration
    shotlist: dict[str, Any] = {
        "draft_name": name,
        "canvas": dict(plan.get("canvas") or {}),
        "shots": shots,
    }
    for key in ("subtitles", "bgm", "voiceover"):
        if plan.get(key):
            shotlist[key] = plan[key]
    return shotlist


def validate_plan(
    plan: dict[str, Any],
    scan: dict[str, Any],
    grouping: dict[str, Any],
) -> dict[str, Any]:
    """校验剪辑决策表：区间、缺镜、置信度、unresolved 禁用与转场类型。"""
    errors: list[str] = []
    warnings: list[str] = []

    canvas = plan.get("canvas") or {}
    for key in ("width", "height", "ratio", "fps"):
        if canvas.get(key) in (None, ""):
            errors.append(f"canvas 缺少 {key}")
    if not plan.get("episode"):
        errors.append("episode 不能为空")
    if not plan.get("source_root"):
        errors.append("source_root 不能为空")
    elif grouping.get("root") and plan.get("source_root") != grouping.get("root"):
        warnings.append(
            f"source_root 与分组表 root 不一致：{plan.get('source_root')} vs {grouping.get('root')}"
        )

    source_audio = plan.get("source_audio", "keep")
    if source_audio not in {"keep", "mute"}:
        errors.append(f"source_audio 非法：{source_audio}")

    shot_files: dict[str, set[str]] = {}
    for shot in grouping.get("shots", []):
        sid = str(shot.get("shot"))
        files: set[str] = set()
        for take in shot.get("takes", []):
            files.update(take.get("files") or [])
        shot_files[sid] = files
    unresolved_files = {item.get("file") for item in grouping.get("unresolved", [])}
    clips = {c["file"]: c for c in scan.get("clips", [])}

    seen: set[str] = set()
    for entry in plan.get("shots") or []:
        sid = str(entry.get("shot"))
        if sid in seen:
            errors.append(f"shot {sid}: 在 plan 里出现多次")
        seen.add(sid)
        if sid not in shot_files:
            errors.append(f"shot {sid}: 不在分组表里")
            continue
        allowed = shot_files[sid]

        if not entry.get("reason"):
            errors.append(f"shot {sid}: 缺少 reason")
        confidence = entry.get("confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= float(confidence) <= 1
        ):
            errors.append(f"shot {sid}: confidence 非法：{confidence}")
        elif float(confidence) < 0.6 and not (entry.get("review") or []):
            errors.append(f"shot {sid}: confidence {confidence} 低于 0.6，必须写入 review")

        segments = entry.get("segments") or []
        if not segments:
            errors.append(f"shot {sid}: 没有任何片段")
        for seg in segments:
            name = seg.get("file")
            if name not in allowed:
                errors.append(f"shot {sid}: segment 文件不属于该镜头：{name}")
            if name in unresolved_files:
                errors.append(f"shot {sid}: 使用了 unresolved 素材：{name}")
            duration = seg.get("duration")
            start = seg.get("source_start", 0.0)
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
                errors.append(f"shot {sid}: duration 非法：{duration}")
                continue
            if not isinstance(start, (int, float)) or isinstance(start, bool) or start < 0:
                errors.append(f"shot {sid}: source_start 非法：{start}")
                continue
            meta = clips.get(name)
            if meta is None:
                errors.append(f"shot {sid}: 素材不在 scan 里：{name}")
            elif meta.get("duration") is None:
                errors.append(f"shot {sid}: 素材没有可用时长：{name}")
            elif start + duration > meta["duration"] + 1e-6:
                errors.append(
                    f"shot {sid}: {name} 取用区间 {start}+{duration} 超出视频流时长 {meta['duration']}"
                )

        for alt in entry.get("alternates") or []:
            name = alt.get("file")
            if name not in allowed:
                errors.append(f"shot {sid}: 备选文件不属于该镜头：{name}")
            if name in unresolved_files:
                errors.append(f"shot {sid}: 备选使用了 unresolved 素材：{name}")

        transition = entry.get("transition_out") or {"type": "hard_cut"}
        ttype = transition.get("type")
        if ttype in {"hard_cut", "dissolve"}:
            if ttype == "dissolve":
                t_duration = transition.get("duration")
                if (
                    not isinstance(t_duration, (int, float))
                    or isinstance(t_duration, bool)
                    or not 0.3 <= float(t_duration) <= 0.8
                ):
                    warnings.append(f"shot {sid}: 叠化时长不在 0.3 到 0.8 秒：{t_duration}")
        else:
            warnings.append(f"shot {sid}: 转场类型 {ttype} 只标注不执行")

    for sid in sorted(set(shot_files) - seen):
        errors.append(f"shot {sid}: 分组表里有但 plan 里缺少")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def report(plan: dict[str, Any], grouping: dict[str, Any] | None = None) -> str:
    """生成人读报告（Markdown 文本；写文件是调用方的事）。"""
    shots = plan.get("shots") or []
    total = sum(
        float(seg.get("duration", 0))
        for shot in shots
        for seg in (shot.get("segments") or [])
    )
    lines = [
        f"# 粗剪报告：{plan.get('episode', '')}",
        "",
        f"- 总时长：{total:.2f} 秒",
        f"- 镜头数：{len(shots)}",
        "",
        "## 逐镜表",
        "",
        "| 镜号 | 首选素材 | 取用区间 | 理由 | 置信度 | 转场 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for shot in shots:
        sid = shot.get("shot")
        segments = shot.get("segments") or []
        files = " + ".join(str(seg.get("file")) for seg in segments)
        ranges = " + ".join(
            f"{seg.get('source_start', 0)}~{round(float(seg.get('source_start', 0)) + float(seg.get('duration', 0)), 3)}"
            for seg in segments
        )
        reason = str(shot.get("reason", "")).replace("|", "\\|")
        confidence = shot.get("confidence")
        transition = (shot.get("transition_out") or {}).get("type", "hard_cut")
        lines.append(f"| {sid} | {files} | {ranges} | {reason} | {confidence} | {transition} |")

    lines += ["", "## 待确认清单", ""]
    items: list[str] = []
    for item in (grouping or {}).get("unresolved", []):
        items.append(f"- 未归类素材：{item.get('file')}（{item.get('reason', '')}）")
    for shot in shots:
        confidence = shot.get("confidence")
        low = (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and float(confidence) < 0.6
        )
        if low or (shot.get("review") or []):
            detail = "；".join(str(x) for x in (shot.get("review") or []))
            items.append(f"- 镜头 {shot.get('shot')}：{detail or '低置信，需人工复核'}")
    lines += items or ["- 无"]
    return "\n".join(lines)


def frame_times(duration: float, count: int = 5) -> list[float]:
    """一个素材内均匀取帧的时刻：10% 到 90%，count == 1 时取中点。"""
    duration = float(duration)
    if count <= 1:
        return [round(duration / 2, 3)]
    return [round(duration * (0.1 + 0.8 * i / (count - 1)), 3) for i in range(count)]


def _default_grab(path: Path, t: float):  # pragma: no cover - 需要本机 ffmpeg
    """用本机 ffmpeg 抽单帧，返回 PIL 图像。"""
    import io
    import subprocess

    import imageio_ffmpeg
    from PIL import Image

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run(
        [
            exe,
            "-ss",
            str(t),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RoughcutError(f"抽帧失败：{path.name} @ {t}s")
    return Image.open(io.BytesIO(proc.stdout)).convert("RGB")


def frames(
    grouping: dict[str, Any],
    scan: dict[str, Any],
    out: Path,
    *,
    count: int = 5,
    grab: Callable[[Path, float], Any] | None = None,
) -> dict[str, Any]:
    """每个镜头一张联系表：各 take 并排，take 内按 2 列排布该 take 的帧。"""
    from PIL import Image, ImageDraw

    out = Path(out)
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    grab = grab or _default_grab
    clips = {c["file"]: c for c in scan.get("clips", [])}
    root = Path(scan.get("root", "."))
    cell_w, cell_h, label_h = 240, 135, 18
    sheets: list[str] = []

    for shot in grouping.get("shots") or []:
        pairs_per_take: list[list[tuple[str, float]]] = []
        for take in shot.get("takes") or []:
            pairs: list[tuple[str, float]] = []
            for name in take.get("files") or []:
                duration = float((clips.get(name) or {}).get("duration") or 1.0)
                pairs.extend((name, t) for t in frame_times(duration, count))
            pairs_per_take.append(pairs)
        if not pairs_per_take:
            continue

        rows = max(1, max((len(pairs) + 1) // 2 for pairs in pairs_per_take))
        cols = 2 * len(pairs_per_take)
        sheet = Image.new("RGB", (cols * cell_w, rows * (cell_h + label_h)), (18, 18, 18))
        draw = ImageDraw.Draw(sheet)
        for block, pairs in enumerate(pairs_per_take):
            for index, (name, t) in enumerate(pairs):
                x = (block * 2 + index % 2) * cell_w
                y = (index // 2) * (cell_h + label_h)
                try:
                    frame = grab(root / name, t).convert("RGB")
                except Exception:  # noqa: BLE001 - 单帧失败画灰块，不中断
                    frame = Image.new("RGB", (cell_w, cell_h), (60, 0, 0))
                frame.thumbnail((cell_w, cell_h))
                sheet.paste(
                    frame,
                    (x + (cell_w - frame.width) // 2, y + (cell_h - frame.height) // 2),
                )
                draw.text((x + 4, y + cell_h + 2), f"{name} @ {t}", fill=(230, 230, 230))
        sheet_path = frames_dir / f"shot-{shot.get('shot')}.jpg"
        sheet.save(sheet_path, quality=85)
        sheets.append(str(sheet_path))

    return {"sheets": sheets, "shots": len(sheets)}


def _default_builder_runner(args: list[str]) -> dict[str, Any]:  # pragma: no cover - 需要本机环境
    """默认用 build_draft.py 建草稿。"""
    import subprocess
    import sys

    cmd = [sys.executable, str(Path(__file__).resolve().parent / "build_draft.py"), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RoughcutError(
            f"build_draft.py 失败（exit {proc.returncode}）：\n{proc.stdout}\n{proc.stderr}"
        )
    return {"stdout": (proc.stdout or "").strip()}


def _default_cli_runner(args: list[str]) -> dict[str, Any]:  # pragma: no cover - 需要本机环境
    """默认用 jianying_tool.cli 挂转场/静音。"""
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "jianying_tool.cli", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RoughcutError(
            f"jianying_tool.cli 失败（exit {proc.returncode}）：{' '.join(args)}\n{proc.stdout}\n{proc.stderr}"
        )
    return {}


def build(
    plan: dict[str, Any],
    scan: dict[str, Any],
    grouping: dict[str, Any],
    out: Path,
    *,
    draft_name: str | None = None,
    existing: bool = False,
    dry_run: bool = False,
    run_builder: Callable[[list[str]], dict[str, Any]] | None = None,
    run_transition: Callable[[list[str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """校验通过后：写 shotlist → 建草稿 → 挂叠化 → 按需静音。"""
    out = Path(out)
    validation = validate_plan(plan, scan, grouping)
    if validation["errors"]:
        raise RoughcutError("决策表校验失败：\n  " + "\n  ".join(validation["errors"]))

    name = draft_name or plan.get("draft_name") or f"{plan['episode']}-粗剪"
    shotlist = plan_to_shotlist(plan, draft_name=name)
    shotlist_path = out / "shotlist.json"
    track = plan.get("video_track", "main_video")

    shots = plan.get("shots") or []
    transitions: list[tuple[int, float]] = []
    skipped: list[str] = []
    cursor = 0
    for index, entry in enumerate(shots):
        segments = entry.get("segments") or []
        transition = entry.get("transition_out") or {}
        if transition.get("type") == "dissolve":
            if index < len(shots) - 1:
                duration = transition.get("duration") or 0.5
                transitions.append((cursor + len(segments) - 1, float(duration)))
            else:
                skipped.append("最后一个镜头的 transition_out 无处可挂，已跳过")
        cursor += len(segments)
    total_segments = cursor
    mute = plan.get("source_audio") == "mute"

    if dry_run:
        return {
            "shotlist": str(shotlist_path),
            "transitions_planned": len(transitions),
            "muted_planned": total_segments if mute else 0,
        }

    out.mkdir(parents=True, exist_ok=True)
    shotlist_path.write_text(
        json.dumps(shotlist, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    builder = run_builder or _default_builder_runner
    builder_args = ["--shotlist", str(shotlist_path)]
    if draft_name:
        builder_args += ["--name", name]
    if existing:
        builder_args += ["--existing"]
    builder_result = builder(builder_args) or {}

    runner = run_transition or _default_cli_runner
    applied = 0
    for segment_index, duration in transitions:
        runner(
            [
                "transition",
                "--draft",
                name,
                "--track",
                track,
                "--segment",
                str(segment_index),
                "--name",
                "叠化",
                "--duration",
                str(duration),
            ]
        )
        applied += 1

    muted = 0
    if mute:
        for segment_index in range(total_segments):
            runner(
                [
                    "edit-segment",
                    "--draft",
                    name,
                    "--track",
                    track,
                    "--segment",
                    str(segment_index),
                    "--mute",
                ]
            )
            muted += 1

    return {
        "shotlist": str(shotlist_path),
        "builder": builder_result,
        "transitions_applied": applied,
        "muted_segments": muted,
        "skipped": skipped,
    }
