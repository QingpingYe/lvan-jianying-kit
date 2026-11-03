"""智能粗剪的共享核心。

判断类工作（命名推断、看片、take 择优）在会话里由 AI 完成；本模块只做
确定性事务：清点探测、校验、转换、执行与报告。
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

VIDEO_EXTS: tuple[str, ...] = (".mp4", ".mov", ".m4v", ".avi", ".mkv")
"""参与粗剪的视频扩展名（小写匹配）。"""

MAX_SHEET_CELLS = 12
"""单张联系表的格数上限（设计约束，防止图片过宽、标签不可读）。"""


class RoughcutError(RuntimeError):
    """智能粗剪流程中的可预期错误。"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _is_number(value: Any) -> bool:
    """真数值：排除 bool，要求有限（挡住 NaN / Infinity）。"""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _norm_root(value: Any) -> str:
    """把根目录规范成可比较形式：大小写、分隔符与末尾斜杠差异不算不一致。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def _resolve_media_path(root: Path, name: str) -> Path:
    """素材相对路径按根目录解析；绝对路径原样使用。"""
    path = Path(name)
    return path if path.is_absolute() else root / path


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
    shots = grouping.get("shots")
    for shot in shots if isinstance(shots, list) else []:
        if not isinstance(shot, dict):
            continue
        takes = shot.get("takes")
        for take in takes if isinstance(takes, list) else []:
            if not isinstance(take, dict):
                continue
            take_files = take.get("files")
            if isinstance(take_files, list):
                files.extend(f for f in take_files if isinstance(f, str))
    return files


def _named_entries(grouping: dict[str, Any], key: str, errors: list[str]) -> list[str]:
    """读 unresolved / excluded 这类 {file, reason} 列表，顺带做结构校验。"""
    raw = grouping.get(key)
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        errors.append(f"{key} 必须是数组")
        return []
    names: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            errors.append(f"{key} 里的条目必须是对象")
            continue
        name = item.get("file")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{key} 条目缺少 file")
            continue
        if not str(item.get("reason") or "").strip():
            errors.append(f"{key} 条目缺少 reason：{name}")
        names.append(name)
    return names


def validate_grouping(
    grouping: dict[str, Any],
    scan: dict[str, Any],
    *,
    check_files: bool = False,
) -> dict[str, Any]:
    """校验命名推断产物的分组表：覆盖率、根目录绑定、唯一性、结构、segmented 顺序。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(grouping, dict):
        return {"ok": False, "errors": ["grouping 必须是 JSON 对象"], "warnings": []}
    if not isinstance(scan, dict):
        return {"ok": False, "errors": ["scan 必须是 JSON 对象"], "warnings": []}

    for key in ("root", "scheme", "scheme_confidence", "evidence"):
        value = grouping.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"grouping 缺少必填字段：{key}")
    grouping_root = _norm_root(grouping.get("root"))
    scan_root = _norm_root(scan.get("root"))
    if grouping_root and scan_root and grouping_root != scan_root:
        errors.append(
            f"grouping.root 与 scan.root 不一致：{grouping.get('root')} vs {scan.get('root')}"
        )

    raw_shots = grouping.get("shots")
    if not isinstance(raw_shots, list):
        errors.append("shots 必须是数组")
        raw_shots = []
    raw_clips = scan.get("clips")
    clips = raw_clips if isinstance(raw_clips, list) else []
    known = {
        c["file"] for c in clips if isinstance(c, dict) and isinstance(c.get("file"), str)
    }
    raw_skipped = scan.get("skipped")
    skipped = {
        s["file"]
        for s in (raw_skipped if isinstance(raw_skipped, list) else [])
        if isinstance(s, dict) and isinstance(s.get("file"), str)
    }
    shot_files = _take_files(grouping)
    unresolved = _named_entries(grouping, "unresolved", errors)
    excluded = _named_entries(grouping, "excluded", errors)
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

    seen_shots: set[str] = set()
    for shot in raw_shots:
        if not isinstance(shot, dict):
            errors.append("shots 里的条目必须是对象")
            continue
        sid = shot.get("shot")
        if not isinstance(sid, str) or not sid.strip():
            errors.append("shots 里存在空 shot 标识")
            sid = ""
        else:
            if sid in seen_shots:
                errors.append(f"shot {sid}: 标识重复")
            seen_shots.add(sid)
        takes = shot.get("takes")
        if not isinstance(takes, list):
            errors.append(f"shot {sid}: takes 必须是数组")
            continue
        if not takes:
            errors.append(f"shot {sid}: 没有任何 take")
        seen_takes: set[str] = set()
        for take in takes:
            if not isinstance(take, dict):
                errors.append(f"shot {sid}: take 条目必须是对象")
                continue
            tid = take.get("take")
            if not isinstance(tid, str) or not tid.strip():
                errors.append(f"shot {sid}: take 标识为空")
                tid = ""
            else:
                if tid in seen_takes:
                    errors.append(f"shot {sid}: take {tid}: 标识重复")
                seen_takes.add(tid)
            files = take.get("files")
            if not isinstance(files, list) or not files:
                errors.append(f"shot {sid} take {tid}: files 为空")
                files = []
            elif not all(isinstance(f, str) and f.strip() for f in files):
                errors.append(f"shot {sid} take {tid}: files 里必须是非空字符串")
            kind = take.get("kind")
            if kind not in {"single", "segmented"}:
                errors.append(f"shot {sid} take {tid}: kind 非法：{kind}")
            elif kind == "segmented" and len(files) < 2:
                warnings.append(f"shot {sid} take {tid}: segmented 但只有一个文件")
            if check_files and grouping_root:
                for name in files:
                    if isinstance(name, str) and name.strip():
                        path = _resolve_media_path(Path(grouping["root"]), name)
                        if not path.is_file():
                            errors.append(f"素材文件不存在：{name}")

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
    *,
    check_files: bool = False,
) -> dict[str, Any]:
    """校验剪辑决策表：结构、区间、缺镜、覆盖审计、置信度、unresolved 禁用与转场。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(plan, dict):
        return {"ok": False, "errors": ["plan 必须是 JSON 对象"], "warnings": []}
    if not isinstance(scan, dict):
        return {"ok": False, "errors": ["scan 必须是 JSON 对象"], "warnings": []}
    if not isinstance(grouping, dict):
        return {"ok": False, "errors": ["grouping 必须是 JSON 对象"], "warnings": []}

    canvas = plan.get("canvas")
    if not isinstance(canvas, dict):
        errors.append("canvas 必须是对象")
    else:
        for key in ("width", "height", "fps"):
            value = canvas.get(key)
            if not _is_number(value) or float(value) <= 0:
                errors.append(f"canvas.{key} 必须是正数：{value}")
        ratio = canvas.get("ratio")
        if not isinstance(ratio, str) or not ratio.strip():
            errors.append("canvas 缺少 ratio")
    episode = plan.get("episode")
    if not isinstance(episode, str) or not episode.strip():
        errors.append("episode 不能为空")

    source_root = plan.get("source_root")
    plan_root = _norm_root(source_root)
    grouping_root = _norm_root(grouping.get("root"))
    scan_root = _norm_root(scan.get("root"))
    if not plan_root:
        errors.append("source_root 不能为空")
    else:
        if grouping_root and plan_root != grouping_root:
            errors.append(
                f"source_root 与分组表 root 不一致：{source_root} vs {grouping.get('root')}"
            )
        if scan_root and plan_root != scan_root:
            errors.append(f"source_root 与 scan.root 不一致：{source_root} vs {scan.get('root')}")

    source_audio = plan.get("source_audio", "keep")
    if source_audio not in {"keep", "mute"}:
        errors.append(f"source_audio 非法：{source_audio}")

    shot_takes: dict[str, list[list[str]]] = {}
    raw_grouping_shots = grouping.get("shots")
    for shot in raw_grouping_shots if isinstance(raw_grouping_shots, list) else []:
        if not isinstance(shot, dict):
            continue
        sid = shot.get("shot")
        if not isinstance(sid, str) or not sid.strip():
            continue
        files: list[str] = []
        takes = shot.get("takes")
        for take in takes if isinstance(takes, list) else []:
            if isinstance(take, dict) and isinstance(take.get("files"), list):
                files.extend(f for f in take["files"] if isinstance(f, str))
        shot_takes.setdefault(sid, []).append(files)
    shot_files = {sid: {f for take in takes for f in take} for sid, takes in shot_takes.items()}
    raw_unresolved = grouping.get("unresolved")
    unresolved_files = {
        item.get("file")
        for item in (raw_unresolved if isinstance(raw_unresolved, list) else [])
        if isinstance(item, dict)
    }
    raw_clips = scan.get("clips")
    clips = {
        c["file"]: c
        for c in (raw_clips if isinstance(raw_clips, list) else [])
        if isinstance(c, dict) and isinstance(c.get("file"), str)
    }

    shots = plan.get("shots")
    if not isinstance(shots, list):
        errors.append("shots 必须是数组")
        shots = []
    if not shots:
        errors.append("plan 里没有任何镜头")

    seen: set[str] = set()
    for index, entry in enumerate(shots):
        if not isinstance(entry, dict):
            errors.append(f"shots[{index}] 必须是对象")
            continue
        sid = entry.get("shot")
        if not isinstance(sid, str) or not sid.strip():
            errors.append(f"shots[{index}]: shot 标识为空")
            continue
        if sid in seen:
            errors.append(f"shot {sid}: 在 plan 里出现多次")
        seen.add(sid)
        if sid not in shot_files:
            errors.append(f"shot {sid}: 不在分组表里")
            continue
        allowed = shot_files[sid]

        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"shot {sid}: 缺少 reason")

        if not isinstance(entry.get("review"), list):
            errors.append(f"shot {sid}: 缺少 review 数组")
            review: list[Any] = []
        else:
            review = entry["review"]

        confidence = entry.get("confidence")
        if not _is_number(confidence) or not 0 <= float(confidence) <= 1:
            errors.append(f"shot {sid}: confidence 非法：{confidence}")
        elif float(confidence) < 0.6:
            errors.append(
                f"shot {sid}: confidence {confidence} 低于 0.6，按规则应淘汰，不得作为入选镜头"
            )
        elif float(confidence) < 0.75 and not review:
            errors.append(
                f"shot {sid}: confidence {confidence} 处于 0.6 到 0.75，必须写入 review"
            )

        segments = entry.get("segments")
        if not isinstance(segments, list) or not segments:
            errors.append(f"shot {sid}: 没有任何片段")
            segments = []
        selected: set[str] = set()
        for seg in segments:
            if not isinstance(seg, dict):
                errors.append(f"shot {sid}: segment 条目必须是对象")
                continue
            name = seg.get("file")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"shot {sid}: segment 缺少 file")
                continue
            selected.add(name)
            if name not in allowed:
                errors.append(f"shot {sid}: segment 文件不属于该镜头：{name}")
            if name in unresolved_files:
                errors.append(f"shot {sid}: 使用了 unresolved 素材：{name}")
            duration = seg.get("duration")
            start = seg.get("source_start", 0.0)
            if not _is_number(duration) or float(duration) <= 0:
                errors.append(f"shot {sid}: duration 非法：{duration}")
                continue
            if not _is_number(start) or float(start) < 0:
                errors.append(f"shot {sid}: source_start 非法：{start}")
                continue
            meta = clips.get(name)
            if meta is None:
                errors.append(f"shot {sid}: 素材不在 scan 里：{name}")
            elif meta.get("duration") is None:
                errors.append(f"shot {sid}: 素材没有可用时长：{name}")
            elif float(start) + float(duration) > meta["duration"] + 1e-6:
                errors.append(
                    f"shot {sid}: {name} 取用区间 {start}+{duration} 超出视频流时长 {meta['duration']}"
                )

        alternates = entry.get("alternates")
        if not isinstance(alternates, list):
            errors.append(f"shot {sid}: 缺少 alternates 数组")
            alternates = []
        alt_names: set[str] = set()
        for alt in alternates:
            if not isinstance(alt, dict):
                errors.append(f"shot {sid}: alternates 条目必须是对象")
                continue
            name = alt.get("file")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"shot {sid}: 备选条目缺少 file")
                continue
            if name in alt_names:
                errors.append(f"shot {sid}: 备选文件重复：{name}")
            alt_names.add(name)
            if name not in allowed:
                errors.append(f"shot {sid}: 备选文件不属于该镜头：{name}")
            if name in unresolved_files:
                errors.append(f"shot {sid}: 备选使用了 unresolved 素材：{name}")
            if name in selected:
                errors.append(f"shot {sid}: 素材既入选又列为备选：{name}")
            note = alt.get("note")
            if not isinstance(note, str) or not note.strip():
                errors.append(f"shot {sid}: 备选文件缺少 note：{name}")

        for take in shot_takes.get(sid, []):
            for name in take:
                if name not in selected and name not in alt_names:
                    errors.append(f"shot {sid}: 未入选素材必须写入 alternates 并备注：{name}")

        transition = entry.get("transition_out")
        if not isinstance(transition, dict):
            errors.append(f"shot {sid}: 缺少 transition_out 对象")
            transition = {"type": "hard_cut"}
        ttype = transition.get("type")
        if ttype not in {"hard_cut", "dissolve"}:
            warnings.append(f"shot {sid}: 转场类型 {ttype} 只标注不执行")
        elif ttype == "dissolve":
            t_duration = transition.get("duration")
            if not _is_number(t_duration) or not 0.3 <= float(t_duration) <= 0.8:
                errors.append(f"shot {sid}: 叠化时长必须在 0.3 到 0.8 秒之间：{t_duration}")

        if check_files and plan_root and isinstance(source_root, str):
            root = Path(source_root)
            for name in sorted(selected | alt_names):
                if not _resolve_media_path(root, name).is_file():
                    errors.append(f"shot {sid}: 素材文件不存在：{name}")

    if check_files and plan_root and isinstance(source_root, str):
        root = Path(source_root)
        for block_key, field in (("subtitles", "srt"), ("bgm", "file"), ("voiceover", "script")):
            block = plan.get(block_key)
            if isinstance(block, dict):
                value = block.get(field)
                if isinstance(value, str) and value.strip() and not _resolve_media_path(root, value).is_file():
                    errors.append(f"{block_key}.{field} 文件不存在：{value}")

    for sid in sorted(set(shot_takes) - seen):
        errors.append(f"shot {sid}: 分组表里有但 plan 里缺少")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def report(plan: dict[str, Any], grouping: dict[str, Any] | None = None) -> str:
    """生成人读报告（Markdown 文本；写文件是调用方的事）。"""
    raw_shots = plan.get("shots") if isinstance(plan, dict) else None
    shots = [shot for shot in (raw_shots if isinstance(raw_shots, list) else []) if isinstance(shot, dict)]
    total = 0.0
    for shot in shots:
        raw_segments = shot.get("segments")
        for seg in raw_segments if isinstance(raw_segments, list) else []:
            if isinstance(seg, dict) and _is_number(seg.get("duration")):
                total += float(seg["duration"])
    script_checked = any(
        isinstance(shot.get("script_ref"), str) and shot["script_ref"].strip() for shot in shots
    )
    lines = [
        f"# 粗剪报告：{plan.get('episode', '') if isinstance(plan, dict) else ''}",
        "",
        f"- 总时长：{total:.2f} 秒",
        f"- 镜头数：{len(shots)}",
    ]
    if not script_checked:
        lines.append("- 未对剧本验证：本集没有可用的剧本/分镜对照，镜序按文件名与画面连续性推断")
    lines += [
        "",
        "## 逐镜表",
        "",
        "| 镜号 | 首选素材 | 取用区间 | 备选 | 理由 | 置信度 | 转场 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for shot in shots:
        sid = shot.get("shot")
        raw_segments = shot.get("segments")
        segments = [seg for seg in (raw_segments if isinstance(raw_segments, list) else []) if isinstance(seg, dict)]
        files = " + ".join(str(seg.get("file")) for seg in segments)
        ranges = " + ".join(
            f"{seg.get('source_start', 0)}~{round(float(seg.get('source_start', 0)) + float(seg.get('duration', 0)), 3)}"
            for seg in segments
        )
        raw_alternates = shot.get("alternates")
        alternates = [alt for alt in (raw_alternates if isinstance(raw_alternates, list) else []) if isinstance(alt, dict)]
        alt_text = "；".join(
            f"{alt.get('file')}（{alt.get('note', '')}）" for alt in alternates
        ) or "无"
        reason = str(shot.get("reason", "")).replace("|", "\\|")
        confidence = shot.get("confidence")
        transition_block = shot.get("transition_out")
        transition = (
            transition_block.get("type", "hard_cut")
            if isinstance(transition_block, dict)
            else "hard_cut"
        )
        lines.append(
            f"| {sid} | {files} | {ranges} | {alt_text} | {reason} | {confidence} | {transition} |"
        )

    warnings = plan.get("warnings") if isinstance(plan, dict) else None
    lines += ["", "## 计划警告", ""]
    lines += [f"- {w}" for w in warnings] if isinstance(warnings, list) and warnings else ["- 无"]

    lines += ["", "## 待确认清单", ""]
    items: list[str] = []
    raw_unresolved = grouping.get("unresolved") if isinstance(grouping, dict) else None
    for item in raw_unresolved if isinstance(raw_unresolved, list) else []:
        if isinstance(item, dict):
            items.append(f"- 未归类素材：{item.get('file')}（{item.get('reason', '')}）")
    for shot in shots:
        confidence = shot.get("confidence")
        low = _is_number(confidence) and float(confidence) < 0.75
        raw_review = shot.get("review")
        review = raw_review if isinstance(raw_review, list) else []
        if low or review:
            detail = "；".join(str(x) for x in review)
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
    """每个镜头一张联系表（单张 ≤ 12 格，超出拆页）：各 take 并排，take 内按 2 列排布。"""
    from PIL import Image, ImageDraw

    out = Path(out)
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    grab = grab or _default_grab
    raw_clips = scan.get("clips")
    clips = {
        c["file"]: c
        for c in (raw_clips if isinstance(raw_clips, list) else [])
        if isinstance(c, dict) and isinstance(c.get("file"), str)
    }
    root = Path(scan.get("root", "."))
    cell_w, cell_h, label_h = 240, 135, 18
    sheets: list[str] = []
    cells: list[int] = []
    shot_count = 0

    raw_shots = grouping.get("shots")
    for shot in raw_shots if isinstance(raw_shots, list) else []:
        if not isinstance(shot, dict):
            continue
        raw_takes = shot.get("takes")
        files_per_take: list[list[str]] = []
        for take in raw_takes if isinstance(raw_takes, list) else []:
            if isinstance(take, dict) and isinstance(take.get("files"), list):
                files_per_take.append([f for f in take["files"] if isinstance(f, str)])
        total_files = sum(len(files) for files in files_per_take)
        if total_files == 0:
            continue
        per_file = max(1, min(count, MAX_SHEET_CELLS // total_files))
        pairs_per_take: list[list[tuple[str, float]]] = []
        for files in files_per_take:
            pairs: list[tuple[str, float]] = []
            for name in files:
                duration = float((clips.get(name) or {}).get("duration") or 1.0)
                pairs.extend((name, t) for t in frame_times(duration, per_file))
            pairs_per_take.append(pairs)
        if not pairs_per_take:
            continue
        pages = _paginate_take_blocks(pairs_per_take)
        shot_count += 1
        multi = len(pages) > 1
        for page_index, blocks in enumerate(pages, start=1):
            rows = max(1, max((len(block) + 1) // 2 for block in blocks))
            cols = 2 * len(blocks)
            sheet = Image.new("RGB", (cols * cell_w, rows * (cell_h + label_h)), (18, 18, 18))
            draw = ImageDraw.Draw(sheet)
            for block, pairs in enumerate(blocks):
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
            suffix = f"-{page_index}" if multi else ""
            sheet_path = frames_dir / f"shot-{shot.get('shot')}{suffix}.jpg"
            sheet.save(sheet_path, quality=85)
            sheets.append(str(sheet_path))
            cells.append(sum(len(block) for block in blocks))

    return {"sheets": sheets, "shots": shot_count, "cells": cells}


def _paginate_take_blocks(
    pairs_per_take: list[list[tuple[str, float]]],
) -> list[list[list[tuple[str, float]]]]:
    """把各 take 的帧块分页，保证每页 ≤ MAX_SHEET_CELLS 格；帧块本身尽量不跨页。"""
    pages: list[list[list[tuple[str, float]]]] = []
    page: list[list[tuple[str, float]]] = []
    used = 0
    for pairs in pairs_per_take:
        if not pairs:
            continue
        blocks = [
            pairs[i : i + MAX_SHEET_CELLS] for i in range(0, len(pairs), MAX_SHEET_CELLS)
        ]
        for block in blocks:
            if page and used + len(block) > MAX_SHEET_CELLS:
                pages.append(page)
                page = []
                used = 0
            page.append(block)
            used += len(block)
    if page:
        pages.append(page)
    return pages


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


def _resolve_from(base: Path, value: str) -> str:
    """相对路径按 base 解析成绝对路径；绝对路径原样返回。"""
    path = Path(value)
    return str(path if path.is_absolute() else (base / path).resolve())


def _default_draft_exists(name: str) -> bool | None:
    """检查草稿根目录里是否已有同名草稿；环境不可用时返回 None，交给 build_draft.py 兜底。"""
    try:
        import jy_template
    except Exception:  # noqa: BLE001 - 单独使用核心函数时不强依赖模板层
        return None
    try:
        root = jy_template.resolve_root()
    except Exception:  # noqa: BLE001 - 探测不到草稿目录时不拦截
        return None
    return (Path(root) / name).exists()


def build(
    plan: dict[str, Any],
    scan: dict[str, Any],
    grouping: dict[str, Any],
    out: Path,
    *,
    draft_name: str | None = None,
    existing: bool = False,
    dry_run: bool = False,
    check_files: bool = True,
    draft_exists: Callable[[str], bool | None] | None = None,
    run_builder: Callable[[list[str]], dict[str, Any]] | None = None,
    run_transition: Callable[[list[str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """校验通过后：草稿碰撞预检 → 写 shotlist → 建草稿 → 挂叠化 → 按需静音。"""
    out = Path(out)
    grouping_validation = validate_grouping(grouping, scan, check_files=check_files)
    validation = validate_plan(plan, scan, grouping, check_files=check_files)
    errors = grouping_validation["errors"] + validation["errors"]
    warnings = grouping_validation["warnings"] + validation["warnings"]
    if errors:
        raise RoughcutError("校验失败：\n  " + "\n  ".join(errors))

    name = draft_name or plan.get("draft_name") or f"{plan['episode']}-粗剪"
    present = (draft_exists or _default_draft_exists)(name)
    if present is True and not existing:
        raise RoughcutError(f"草稿已存在：{name}。换一个 draft_name，或确认后加 --existing")
    if present is False and existing:
        raise RoughcutError(f"--existing 模式要求草稿已存在，但没有找到：{name}")
    shotlist = plan_to_shotlist(plan, draft_name=name)
    source_root = Path(plan["source_root"])
    shotlist["shots"] = [
        {**entry, "file": _resolve_from(source_root, entry["file"])}
        for entry in shotlist["shots"]
    ]
    for key, field in (("subtitles", "srt"), ("bgm", "file"), ("voiceover", "script")):
        block = shotlist.get(key)
        if isinstance(block, dict) and block.get(field):
            shotlist[key] = {**block, field: _resolve_from(source_root, block[field])}
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
            "warnings": warnings,
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
        "warnings": warnings,
    }
