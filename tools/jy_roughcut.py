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

    # 同一素材默认只能出现一次（P5 全覆盖、禁止静默复用）；确需复用时
    # 必须显式写进 media_reuse 白名单，报告和校验都能看见。
    reuse_raw = grouping.get("media_reuse")
    if reuse_raw is None:
        reuse_raw = []
    if not isinstance(reuse_raw, list) or not all(
        isinstance(item, str) and item.strip() for item in reuse_raw
    ):
        errors.append("media_reuse 必须是文件名字符串数组")
        reuse_raw = []
    reuse = set(reuse_raw)
    for name, times in sorted(counts.items()):
        if times > 1 and name not in reuse:
            errors.append(f"文件被覆盖 {times} 次（应为 1 次，或列入 media_reuse）：{name}")
        if name not in known and name not in skipped:
            errors.append(f"引用了 scan 里不存在的文件：{name}")
    for name in sorted(reuse):
        if counts.get(name, 0) < 2:
            warnings.append(f"media_reuse 声明的文件没有被实际复用：{name}")
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
    selected_counts: Counter[str] = Counter()
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
            selected_counts[name] += 1
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
            mute = seg.get("mute")
            if mute is not None and not isinstance(mute, bool):
                errors.append(f"shot {sid}: segment.mute 必须是布尔值：{mute}")
            volume = seg.get("volume")
            if volume is not None and (not _is_number(volume) or float(volume) < 0):
                errors.append(f"shot {sid}: segment.volume 必须是非负有限数：{volume}")
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

    reuse_raw = plan.get("media_reuse")
    if reuse_raw is None:
        reuse_raw = []
    if not isinstance(reuse_raw, list) or not all(
        isinstance(item, str) and item.strip() for item in reuse_raw
    ):
        errors.append("media_reuse 必须是文件名字符串数组")
        reuse = set()
    else:
        reuse = set(reuse_raw)
    for name, times in sorted(selected_counts.items()):
        if times > 1 and name not in reuse:
            errors.append(f"素材被多个镜头复用但未列入 media_reuse：{name}（{times} 次）")
    for name in sorted(reuse):
        if selected_counts.get(name, 0) < 2:
            warnings.append(f"media_reuse 声明的素材没有被实际复用：{name}")

    audio_tracks = plan.get("audio_tracks")
    if audio_tracks is not None:
        if not isinstance(audio_tracks, list):
            errors.append("audio_tracks 必须是数组")
        else:
            by_track: dict[str, list[tuple[float, float, int]]] = {}
            for index, item in enumerate(audio_tracks):
                if not isinstance(item, dict):
                    errors.append(f"audio_tracks[{index}] 必须是对象")
                    continue
                name = item.get("file")
                if not isinstance(name, str) or not name.strip():
                    errors.append(f"audio_tracks[{index}]: 缺少 file")
                    continue
                track_name = item.get("track", "audio")
                if not isinstance(track_name, str) or not track_name.strip():
                    errors.append(f"audio_tracks[{index}]: track 不能为空")
                    continue
                duration = item.get("duration")
                if not _is_number(duration) or float(duration) <= 0:
                    errors.append(f"audio_tracks[{index}]: duration 非法：{duration}")
                    continue
                target_start = item.get("target_start", 0.0)
                source_start = item.get("source_start", 0.0)
                if not _is_number(target_start) or float(target_start) < 0:
                    errors.append(f"audio_tracks[{index}]: target_start 非法：{target_start}")
                    continue
                if not _is_number(source_start) or float(source_start) < 0:
                    errors.append(f"audio_tracks[{index}]: source_start 非法：{source_start}")
                    continue
                for key in ("volume", "fade_in", "fade_out"):
                    value = item.get(key)
                    if value is not None and (not _is_number(value) or float(value) < 0):
                        errors.append(f"audio_tracks[{index}]: {key} 非法：{value}")
                if check_files and plan_root and isinstance(source_root, str):
                    if not _resolve_media_path(Path(source_root), name).is_file():
                        errors.append(f"audio_tracks[{index}]: 音频文件不存在：{name}")
                start = float(target_start)
                end = start + float(duration)
                for other_start, other_end, other_index in by_track.get(track_name, []):
                    if start < other_end - 1e-9 and other_start < end - 1e-9:
                        errors.append(
                            f"audio_tracks[{index}] 与 audio_tracks[{other_index}]"
                            f" 在同一音轨 {track_name} 上重叠"
                        )
                by_track.setdefault(track_name, []).append((start, end, index))

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
    reuse = plan.get("media_reuse") if isinstance(plan, dict) else None
    if isinstance(reuse, list) and reuse:
        lines.append(f"- 复用素材白名单：{'、'.join(str(item) for item in reuse)}")
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

    audio_lines: list[str] = []
    for shot in shots:
        raw_segments = shot.get("segments")
        segments = [
            seg for seg in (raw_segments if isinstance(raw_segments, list) else [])
            if isinstance(seg, dict)
        ]
        for index, segment in enumerate(segments, start=1):
            changes: list[str] = []
            if segment.get("mute") is True:
                changes.append("静音")
            if segment.get("volume") is not None:
                changes.append(f"音量 {segment.get('volume')}")
            if changes:
                audio_lines.append(
                    f"- 镜头 {shot.get('shot')} 第 {index} 段：{'、'.join(changes)}"
                )
    raw_audio_tracks = plan.get("audio_tracks") if isinstance(plan, dict) else None
    for item in raw_audio_tracks if isinstance(raw_audio_tracks, list) else []:
        if isinstance(item, dict):
            audio_lines.append(
                f"- 音轨 {item.get('track', 'audio')}：{item.get('file')}"
                f" @ {item.get('target_start', 0)}s，时长 {item.get('duration')}s"
            )
    lines += ["", "## 音频处理", ""]
    lines += audio_lines or ["- 无"]

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


def decode_audio_pcm(
    media: Path,
    *,
    ffmpeg: str | None = None,
    runner: Callable[..., Any] | None = None,
    sample_rate: int = 16000,
) -> bytes:
    """用 ffmpeg 解出 16kHz 单声道 PCM，避开 PyAV 与 faster-whisper 的版本冲突。"""
    import subprocess

    media = Path(media)
    if not media.is_file():
        raise FileNotFoundError(f"媒体文件不存在：{media}")
    executable = ffmpeg
    if executable is None:
        try:
            import imageio_ffmpeg

            executable = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:  # noqa: BLE001 - 统一给出可执行的修复提示
            raise RoughcutError(
                "找不到 ffmpeg：请安装 imageio-ffmpeg，或用 --ffmpeg 指定可执行文件。"
            ) from exc
    command = [
        str(executable),
        "-nostdin",
        "-v", "error",
        "-i", str(media),
        "-vn",
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", str(int(sample_rate)),
        "pipe:1",
    ]
    run = runner or subprocess.run
    proc = run(command, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RoughcutError(f"ffmpeg 解码失败（exit {proc.returncode}）：{detail}")
    if not proc.stdout:
        raise RoughcutError(f"媒体没有可解码的音轨：{media}")
    return proc.stdout


def transcribe_media(
    media: Path,
    *,
    model: str = "small",
    language: str | None = None,
    model_dir: str | Path | None = None,
    vad: bool = True,
    beam_size: int = 5,
    initial_prompt: str | None = None,
    split_gap: float = 0.25,
    decode: Callable[[Path], bytes] | None = None,
    engine_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """离线转写单个素材：ffmpeg 解码 → numpy 数组 → faster-whisper。

    jianying-tool 0.5.0 自带的 transcribe 在 Python 3.14 + PyAV 19 下不可用
    （av.open 不接受 metadata_errors），这里绕开 PyAV，让粗剪流程仍能做
    音画对位核验。
    """
    media = Path(media)
    if not media.is_file():
        raise FileNotFoundError(f"媒体文件不存在：{media}")
    decoder = decode or decode_audio_pcm
    pcm = decoder(media)
    if not pcm:
        raise RoughcutError(f"媒体没有可解码的音轨：{media}")
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RoughcutError("缺少 numpy：请先按 setup 技能安装运行时依赖。") from exc
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    factory = engine_factory
    if factory is None:
        try:
            from faster_whisper import WhisperModel
        except ModuleNotFoundError as exc:
            raise RoughcutError(
                '未安装 faster-whisper：请执行 pip install "jianying-tool[asr]"。'
            ) from exc
        engine_kwargs: dict[str, Any] = {"device": "cpu", "compute_type": "int8"}
        if model_dir:
            engine_kwargs["download_root"] = str(Path(model_dir))
        factory = lambda: WhisperModel(model, **engine_kwargs)  # noqa: E731
    engine = factory()
    segments, info = engine.transcribe(
        audio,
        language=language,
        initial_prompt=initial_prompt,
        word_timestamps=True,
        vad_filter=bool(vad),
        no_speech_threshold=0.9 if not vad else 0.6,
        beam_size=max(1, int(beam_size)),
    )
    utterances = _words_to_utterances(segments, gap=float(split_gap))
    return {
        "version": 1,
        "file": str(media),
        "model": model,
        "language": getattr(info, "language", None) or (language or ""),
        "utterances": utterances,
    }


def _words_to_utterances(segments: Any, *, gap: float = 0.25) -> list[dict[str, Any]]:
    """把 faster-whisper 的输出拆成逐句时间戳。

    带 initial_prompt 时 Whisper 常把一整段对白合成一个 segment；词级时间戳里
    行首空格与句间停顿仍是可靠的句子边界，这里据此还原逐句区间。
    """
    utterances: list[dict[str, Any]] = []
    for segment in segments:
        words = getattr(segment, "words", None)
        if not words:
            text = str(getattr(segment, "text", "")).strip()
            if text:
                utterances.append(
                    {
                        "start": round(float(getattr(segment, "start", 0.0)), 3),
                        "end": round(float(getattr(segment, "end", 0.0)), 3),
                        "text": text,
                    }
                )
            continue
        buffer: list[Any] = []
        for word in words:
            raw = str(getattr(word, "word", ""))
            starts_new = bool(raw[:1].isspace())
            if buffer and not starts_new:
                try:
                    starts_new = (
                        float(getattr(word, "start", 0.0))
                        - float(getattr(buffer[-1], "end", 0.0))
                        > gap
                    )
                except (TypeError, ValueError):
                    starts_new = False
            if starts_new and buffer:
                item = _utterance_from_words(buffer)
                if item:
                    utterances.append(item)
                buffer = []
            buffer.append(word)
        if buffer:
            item = _utterance_from_words(buffer)
            if item:
                utterances.append(item)
    return utterances


def _utterance_from_words(words: list[Any]) -> dict[str, Any] | None:
    text = "".join(str(getattr(word, "word", "")) for word in words).strip()
    if not text:
        return None
    return {
        "start": round(float(getattr(words[0], "start", 0.0)), 3),
        "end": round(float(getattr(words[-1], "end", 0.0)), 3),
        "text": text,
    }


def transcript_to_srt(transcript: dict[str, Any]) -> str:
    """把 transcribe 的 JSON 结果转成 SRT 文本。"""
    def stamp(seconds: float) -> str:
        milliseconds = max(0, int(round(float(seconds) * 1000)))
        hours, rest = divmod(milliseconds, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        secs, millis = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    blocks = []
    for index, utterance in enumerate(transcript.get("utterances") or [], start=1):
        blocks.append(
            f"{index}\n{stamp(utterance.get('start', 0))} --> {stamp(utterance.get('end', 0))}\n"
            f"{utterance.get('text', '')}\n"
        )
    return "\n".join(blocks)


def _basename_key(value: Any) -> str:
    try:
        return Path(str(value)).name.casefold()
    except (TypeError, ValueError):
        return str(value).casefold()


def audit_plan(
    plan: dict[str, Any],
    timeline: dict[str, Any],
    transcripts: list[dict[str, Any]] | None = None,
    *,
    fps: float = 30.0,
    tolerance_frames: float = 1.0,
) -> dict[str, Any]:
    """对照 plan 与草稿时间线，并把转写台词映射回镜头与时间线位置。"""
    errors: list[str] = []
    warnings: list[str] = []
    planned: list[dict[str, Any]] = []
    cursor = 0.0
    for entry in plan.get("shots") or []:
        if not isinstance(entry, dict):
            continue
        for segment in entry.get("segments") or []:
            if not isinstance(segment, dict) or not _is_number(segment.get("duration")):
                continue
            duration = float(segment["duration"])
            planned.append(
                {
                    "index": len(planned),
                    "shot": entry.get("shot"),
                    "file": segment.get("file"),
                    "source_start": float(segment.get("source_start", 0.0)),
                    "duration": duration,
                    "target_start": round(cursor, 6),
                }
            )
            cursor += duration

    track_name = plan.get("video_track", "main_video")
    tracks = timeline.get("tracks") if isinstance(timeline, dict) else {}
    actual = ((tracks or {}).get(track_name) or {}).get("segments") or []
    tolerance = max(1e-6, float(tolerance_frames) / max(float(fps), 1e-9))
    if len(actual) != len(planned):
        errors.append(f"{track_name} 段数不一致：计划 {len(planned)}，草稿 {len(actual)}")
    for index, (wanted, written) in enumerate(zip(planned, actual)):
        checks = (
            ("source_start", "source_start", 1),
            ("duration", "source_duration", 1),
            ("target_start", "target_start", index + 1),
        )
        for plan_key, actual_key, frame_scale in checks:
            delta = abs(float(wanted[plan_key]) - float(written.get(actual_key) or 0.0))
            check_tolerance = tolerance * frame_scale
            if delta > check_tolerance:
                errors.append(
                    f"{track_name} 段{index} {plan_key} 与草稿不一致："
                    f"计划 {wanted[plan_key]:.3f}s，草稿 {float(written.get(actual_key) or 0.0):.3f}s"
                    f"（差 {delta:.3f}s，容差 {check_tolerance:.3f}s）"
                )

    by_file: dict[str, list[dict[str, Any]]] = {}
    for item in planned:
        by_file.setdefault(_basename_key(item.get("file")), []).append(item)
    dialogue: list[dict[str, Any]] = []
    for transcript in transcripts or []:
        if not isinstance(transcript, dict):
            continue
        source_file = transcript.get("file")
        matched_segments = by_file.get(_basename_key(source_file), [])
        for utterance in transcript.get("utterances") or []:
            if not isinstance(utterance, dict):
                continue
            start = float(utterance.get("start") or 0.0)
            end = float(utterance.get("end") or start)
            entry: dict[str, Any] = {
                "source_file": str(source_file or ""),
                "text": str(utterance.get("text") or "").strip(),
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "shot": None,
                "timeline_start": None,
                "timeline_end": None,
                "trimmed": False,
                "status": "not_in_plan",
            }
            hit = None
            for segment in matched_segments:
                seg_start = float(segment["source_start"])
                seg_end = seg_start + float(segment["duration"])
                if start < seg_end - 1e-9 and end > seg_start + 1e-9:
                    hit = segment
                    break
            if hit is not None:
                seg_start = float(hit["source_start"])
                seg_end = seg_start + float(hit["duration"])
                visible_start = max(start, seg_start)
                visible_end = min(end, seg_end)
                base = float(hit["target_start"])
                entry["shot"] = hit.get("shot")
                entry["timeline_start"] = round(base + (visible_start - seg_start), 3)
                entry["timeline_end"] = round(base + (visible_end - seg_start), 3)
                entry["trimmed"] = (
                    visible_start > start + 1e-9 or visible_end < end - 1e-9
                )
                entry["status"] = "in_timeline"
            elif matched_segments:
                entry["status"] = "cut_out"
            dialogue.append(entry)

    silence: list[dict[str, Any]] = []
    for written in actual:
        volume = written.get("volume")
        if volume is None:
            continue
        try:
            muted = float(volume) == 0.0
        except (TypeError, ValueError):
            muted = False
        if muted:
            silence.append(
                {
                    "segment": written.get("index"),
                    "start": written.get("target_start"),
                    "end": round(
                        float(written.get("target_start") or 0.0)
                        + float(written.get("target_duration") or 0.0),
                        3,
                    ),
                }
            )

    audio_tracks: list[dict[str, Any]] = []
    for name, track in (tracks or {}).items():
        if not isinstance(track, dict) or track.get("type") != "audio":
            continue
        for written in track.get("segments") or []:
            audio_tracks.append(
                {
                    "track": name,
                    "segment": written.get("index"),
                    "start": written.get("target_start"),
                    "end": round(
                        float(written.get("target_start") or 0.0)
                        + float(written.get("target_duration") or 0.0),
                        3,
                    ),
                    "volume": written.get("volume"),
                }
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "tolerance": round(tolerance, 6),
        "planned": planned,
        "actual": actual,
        "dialogue": dialogue,
        "silence": silence,
        "audio_tracks": audio_tracks,
    }


def audit_markdown(result: dict[str, Any]) -> str:
    """把 audit_plan 的结果渲染成人读 Markdown。"""
    lines = [
        "# 音画对位审计",
        "",
        f"- 结论：{'通过' if result.get('ok') else '发现不一致'}",
        f"- 帧容差：{result.get('tolerance', 0):.4f}s",
    ]
    errors = result.get("errors") or []
    if errors:
        lines += ["", "## 计划与草稿差异", *[f"- {error}" for error in errors]]
    lines += ["", "## 台词 → 画面", "", "| 台词 | 映射镜头 | 时间线 | 状态 |", "| --- | --- | --- | --- |"]
    for entry in result.get("dialogue") or []:
        timeline = (
            f"{entry.get('timeline_start')}–{entry.get('timeline_end')}"
            if entry.get("timeline_start") is not None
            else "—"
        )
        lines.append(
            f"| {entry.get('text', '')} | {entry.get('shot') or '—'} | {timeline} | "
            f"{entry.get('status')}{'（部分裁剪）' if entry.get('trimmed') else ''} |"
        )
    silence = result.get("silence") or []
    if silence:
        lines += ["", "## 静音区间", "", "| 段 | 时间线 |", "| --- | --- |"]
        for entry in silence:
            lines.append(f"| {entry.get('segment')} | {entry.get('start')}–{entry.get('end')} |")
    audio = result.get("audio_tracks") or []
    if audio:
        lines += ["", "## 分离音轨", "", "| 轨道 | 段 | 时间线 |", "| --- | --- | --- |"]
        for entry in audio:
            lines.append(
                f"| {entry.get('track')} | {entry.get('segment')} | {entry.get('start')}–{entry.get('end')} |"
            )
    return "\n".join(lines)


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


def _default_draft_status(name: str) -> dict[str, Any] | None:
    """读取草稿的占用/内容概况；环境不可用时返回 None。"""
    try:
        import jy_template
    except Exception:  # noqa: BLE001 - 单独使用核心函数时不强依赖模板层
        return None
    try:
        return jy_template.draft_status(None, name)
    except Exception:  # noqa: BLE001 - 探测不到草稿时交给 build_draft.py 兜底
        return None


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
    draft_status: Callable[[str], dict[str, Any] | None] | None = None,
    force: bool = False,
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
    track = plan.get("video_track", "main_video")
    if existing:
        status_reader = draft_status or _default_draft_status
        try:
            status = status_reader(name)
        except Exception:  # noqa: BLE001 - 探测失败不能伪造成安全
            status = None
        if isinstance(status, dict):
            video = int(status.get("video_segments") or 0)
            audio = int(status.get("audio_segments") or 0)
            readable = bool(status.get("readable"))
            managed = bool(status.get("managed"))
            if (
                status.get("host_running")
                and (video or audio or (managed and not readable))
                and not force
            ):
                raise RoughcutError(
                    f"剪映正在运行，且目标草稿 {name} 已有内容"
                    f"（{video} 个视频片段 / {audio} 个音频片段）。"
                    "剪映内存版本可能覆盖本次写入：请先退出该草稿或完全退出剪映，"
                    "确认风险后再加 --force。"
                )
            if video and not force:
                raise RoughcutError(
                    f"目标草稿非空：--existing 草稿的 {track} 轨道已有 {video} 个视频片段；"
                    "本模式只保证在空草稿上正确追加，请换新草稿名，"
                    "或确认后加 --force。"
                )
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

    shots = plan.get("shots") or []
    transitions: list[tuple[int, float]] = []
    segment_edits: list[tuple[int, dict[str, Any]]] = []
    skipped: list[str] = []
    cursor = 0
    for index, entry in enumerate(shots):
        segments = entry.get("segments") or []
        for offset, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            changes: dict[str, Any] = {}
            if segment.get("mute") is True:
                changes["mute"] = True
            if segment.get("volume") is not None:
                changes["volume"] = float(segment["volume"])
            if changes:
                segment_edits.append((cursor + offset, changes))
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
    audio_tracks = plan.get("audio_tracks") or []

    if dry_run:
        return {
            "shotlist": str(shotlist_path),
            "transitions_planned": len(transitions),
            "muted_planned": total_segments if mute else 0,
            "segment_edits_planned": len(segment_edits),
            "audio_tracks_planned": len(audio_tracks),
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
    if force:
        builder_args += ["--force"]
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

    edited = 0
    muted = 0
    muted_indices = {
        segment_index for segment_index, changes in segment_edits if changes.get("mute")
    }
    for segment_index, changes in segment_edits:
        command = [
            "edit-segment",
            "--draft", name,
            "--track", track,
            "--segment", str(segment_index),
        ]
        if changes.get("mute"):
            command.append("--mute")
            muted += 1
        if "volume" in changes:
            command += ["--volume", str(changes["volume"])]
        runner(command)
        edited += 1
    if mute:
        for segment_index in range(total_segments):
            if segment_index in muted_indices:
                continue
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

    audio_applied = 0
    for item in audio_tracks:
        command = [
            "append-audio",
            "--draft", name,
            "--audio", _resolve_from(source_root, item["file"]),
            "--track", str(item.get("track", "audio")),
        ]
        for key, flag in (
            ("target_start", "--target-start"),
            ("source_start", "--source-start"),
            ("duration", "--source-duration"),
            ("volume", "--volume"),
            ("fade_in", "--fade-in"),
            ("fade_out", "--fade-out"),
            ("speed", "--speed"),
        ):
            if item.get(key) is not None:
                command += [flag, str(item[key])]
        runner(command)
        audio_applied += 1

    manifest = {
        "version": 1,
        "draft_name": name,
        "episode": plan.get("episode"),
        "video_track": track,
        "shots": shotlist["shots"],
        "source_audio": plan.get("source_audio", "keep"),
        "transitions_planned": [
            {"segment_index": index, "duration": duration}
            for index, duration in transitions
        ],
        "transitions_applied": applied,
        "muted_segments": muted,
        "segment_edits": [
            {"segment_index": segment_index, **changes}
            for segment_index, changes in segment_edits
        ],
        "segment_edits_applied": edited,
        "audio_tracks": [
            {**item, "file": _resolve_from(source_root, item["file"])}
            for item in audio_tracks
        ],
        "audio_tracks_applied": audio_applied,
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "shotlist": str(shotlist_path),
        "manifest": str(manifest_path),
        "builder": builder_result,
        "transitions_applied": applied,
        "muted_segments": muted,
        "segment_edits_applied": edited,
        "audio_tracks_applied": audio_applied,
        "skipped": skipped,
        "warnings": warnings,
    }
