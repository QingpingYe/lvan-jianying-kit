"""模板批量出片的共享核心。

把"拿一个现成草稿当模板、复制成新草稿、替换其中的素材与文案"这件事做成一组
普通函数：``tools/template_fill.py``（清单驱动的 CLI）与 ``mcp_ext/server.py``
（MCP 工具）都只是它的薄包装。

草稿读写一律走 jianying_tool 的编解码层：新版剪映的 ``draft_content.json``
是密文，只有本机剪映自带的 ``videoeditor.dll`` 能读能写；同时它会校验根文件与
活动时间线是否一致，不一致就拒绝，避免把两个时间线写岔。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from jianying_tool.metadata import write_draft_meta_info
from jianying_tool.inspect import inspect_draft
from jianying_tool.native_codec import decode_file, project_content_path, update_project_json
from jianying_tool.paths import draft_root

SEC = 1_000_000
"""一秒的微秒数，剪映内部时间单位。"""

BACKUP_DIR = ".jianying-tool-backups"
"""工具自身写草稿前的备份目录：复制草稿时必须排除，否则体积按份数翻倍。"""


class TemplateError(RuntimeError):
    """模板批量出片过程中的可预期错误。"""


class RowFailed(TemplateError):
    """清单里某一行失败（只影响该行，不中断整批）。"""

    def __init__(self, row: str, reason: str) -> None:
        super().__init__(f"{row}: {reason}")
        self.row = row
        self.reason = reason


def resolve_root(root: str | Path | None = None) -> Path:
    """解析草稿根目录：显式参数 > ``JIANYING_DRAFT_ROOT`` > 工具自动探测。"""
    if root is not None:
        candidate = Path(root).expanduser()
    elif os.environ.get("JIANYING_DRAFT_ROOT"):
        candidate = Path(os.environ["JIANYING_DRAFT_ROOT"]).expanduser()
    else:
        candidate = draft_root()
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise TemplateError(f"草稿根目录不存在：{candidate}")
    return candidate


def apply_jianying_env(start: Path | None = None) -> dict[str, str]:
    """把安装器探测到的剪映路径读进环境变量。

    剪映装在非默认位置（或草稿目录被改过）时，编解码层靠这几个变量找到
    ``videoeditor.dll``；查找顺序与 ``build_draft.py`` 一致：运行时目录、
    插件根目录、脚本所在目录。
    """
    here = start or Path(__file__).resolve().parent
    candidates: list[Path] = []
    if os.environ.get("JIANYING_KIT_HOME"):
        candidates.append(Path(os.environ["JIANYING_KIT_HOME"]) / "jianying-paths.json")
    try:
        # 插件模式的运行时目录（安装器默认写在这里），脚本不一定挨着它
        candidates.append(Path.home() / ".jianying-kit" / "jianying-paths.json")
    except RuntimeError:
        pass
    candidates += [here / "jianying-paths.json", here.parent / "jianying-paths.json"]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        applied = {
            str(key): str(value)
            for key, value in data.items()
            if isinstance(value, str) and value
        }
        for key, value in applied.items():
            os.environ.setdefault(key, value)
        return applied
    return {}


def _project(root: Path, name: str) -> Path:
    """定位草稿目录，不存在就报错（而不是默默新建一个空目录）。"""
    project = root / name
    if not name or not project.is_dir():
        raise FileNotFoundError(f"草稿不存在：{project}")
    return project


def _safe_name(name: Any) -> str:
    """草稿名必须是单层目录名：挡住分隔符、``..`` 与盘符，避免写到草稿根目录之外。

    这个名字会被 ``root / name`` 直接拼成路径，MCP 工具又是给 AI 调的，
    所以必须在写盘之前挡住 `..\\..\\x` 这类输入。
    """
    text = str(name or "").strip()
    if not text:
        raise TemplateError("草稿名不能为空")
    if text in {".", ".."} or any(token in text for token in ("/", "\\", ":", "\0")):
        raise TemplateError(f"草稿名必须是单层目录名，不能带路径分隔符：{name}")
    return text


def _assert_template_writable(root: Path, name: str) -> None:
    """确认模板的根文件与活动时间线一致。

    写回时 ``update_project_json`` 也会做同样的比较；提前比一次，可以让整批在
    复制出任何草稿之前就失败，而不是每行复制完再各自报错。
    """
    from jianying_tool.native_codec import _project_documents, decode_bytes

    documents = _project_documents(root / name)
    decoded = [decode_bytes(path.read_bytes())[0] for path in documents]
    primary = decoded[0]
    for other in decoded[1:]:
        if other.get("id") != primary.get("id") or other != primary:
            raise TemplateError(
                f"模板草稿的根文件与活动时间线内容不一致，先在剪映里打开并保存一次：{name}"
            )


def read_draft(root: Path, name: str) -> tuple[dict[str, Any], str]:
    """读草稿内容，返回 ``(明文内容, 格式名)``；密文由本机编解码层解开。"""
    project = _project(resolve_root(root), name)
    return decode_file(project_content_path(project))


def write_draft(root: Path, name: str, content: dict[str, Any]) -> None:
    """把草稿内容写回去，按原格式重新加密，并校验多时间线一致性。"""
    project = _project(resolve_root(root), name)
    update_project_json(project, lambda current: (current.clear(), current.update(content)))


def duplicate(root: Path, template: str, target: str, *, allow_replace: bool = False) -> Path:
    """把 ``template`` 草稿复制成 ``target``，并给副本一个独立的草稿身份。

    排除 ``.jianying-tool-backups``；草稿目录之外的素材路径原样保留。
    """
    root = resolve_root(root)
    target = _safe_name(target)
    source = _project(root, template)
    destination = root / target
    if destination.exists() and not allow_replace:
        raise FileExistsError(f"草稿已存在：{destination}")
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=allow_replace,
        ignore=shutil.ignore_patterns(BACKUP_DIR),
    )
    write_draft_meta_info(destination, root=root)
    return destination


def _seconds(value: Any) -> float | None:
    """微秒 → 秒，保留 6 位小数；缺值原样返回 None。"""
    return None if value is None else round(int(value) / SEC, 6)


def _replaceable(track_type: str | None) -> bool:
    """该轨道类型能否在模板模式下改（沿用 pyJianYingDraft 的 allow_modify）。"""
    from pyJianYingDraft.track import TrackType

    member = next((item for item in TrackType if item.name == track_type), None)
    return bool(member and member.value.allow_modify)


def _slot(track_type: str | None, segment: dict[str, Any]) -> dict[str, Any]:
    """把 inspect_draft 的一段换算成槽位描述。"""
    target = segment.get("target_timerange") or {}
    slot: dict[str, Any] = {
        "index": segment.get("index"),
        "kind": "text" if track_type == "text" else "material",
        "target_start": _seconds(target.get("start")),
        "duration": _seconds(target.get("duration")),
    }
    if track_type == "text":
        slot["text"] = segment.get("text")
        return slot
    source = segment.get("source_timerange") or {}
    slot.update(
        {
            "material_name": segment.get("material_name"),
            "material_path": segment.get("material_path"),
            "source_start": _seconds(source.get("start")),
            "source_duration": _seconds(source.get("duration")),
            "speed": segment.get("speed", 1.0),
        }
    )
    return slot


def list_slots(root: Path, draft: str) -> dict[str, Any]:
    """列出草稿里可替换的槽位：素材槽（视频/音频轨）与文本槽（文本轨）。

    槽位只是"给人和 AI 看的清单"；真正替换时按轨道名 + 片段下标定位。
    """
    project = _project(resolve_root(root), draft)
    info = inspect_draft(project)
    tracks = info.get("tracks") or []
    name_counts: dict[tuple[str, str], int] = {}
    for track in tracks:
        key = (str(track.get("type")), str(track.get("name")))
        name_counts[key] = name_counts.get(key, 0) + 1
    return {
        "draft": draft,
        "tracks": [
            {
                "name": track.get("name"),
                "type": track.get("type"),
                "replaceable": _replaceable(track.get("type")),
                "ambiguous_name": name_counts[
                    (str(track.get("type")), str(track.get("name")))
                ] > 1,
                "segments": [
                    _slot(track.get("type"), segment)
                    for segment in track.get("segments") or []
                ],
            }
            for track in tracks
        ],
    }


# --------------------------------------------------------------------------
# 单行替换
# --------------------------------------------------------------------------


def _media_path(base_dir: Path, value: str) -> Path:
    """素材路径：绝对路径照用，相对路径按清单所在目录解析。"""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def _load_video_material(path: Path):
    """读一个本地视频/图片素材（探时长与尺寸）。测试里会被换成假素材。"""
    from pyJianYingDraft import VideoMaterial

    return VideoMaterial(str(path))


def _write_shadow(project: Path, content: dict[str, Any]) -> Path:
    """把明文内容写成影子文件：pyJianYingDraft 只认明文 JSON。"""
    shadow = project / f".draft_content.jy-template-{uuid4().hex}.json"
    shadow.write_text(json.dumps(content, ensure_ascii=False, indent=4), encoding="utf-8")
    return shadow


def _load_template_script(shadow: Path):
    from pyJianYingDraft.script_file import ScriptFile

    return ScriptFile._load_template(str(shadow))


def _edit_with_shadow(project: Path, operation) -> None:
    """在影子明文文件上改草稿，成功后按原格式（可能是密文）写回，最后删影子。"""
    content, _ = read_draft(project.parent, project.name)
    shadow = _write_shadow(project, content)
    try:
        script = _load_template_script(shadow)
        operation(script)
        script.save()
        updated = json.loads(shadow.read_text(encoding="utf-8"))
        _prune_dangling_text_refs(updated)
        write_draft(project.parent, project.name, updated)
    finally:
        shadow.unlink(missing_ok=True)


def _prune_dangling_text_refs(content: dict[str, Any]) -> None:
    """删掉文本片段里指向不存在素材的附加引用。

    pyJianYingDraft 给每个片段的 ``extra_material_refs`` 都塞了一个 speed id，
    但文本片段并不会把对应素材写进 ``materials.speeds``，于是留下一个悬空引用：
    doctor 会当成"素材缺失"报出来，跨草稿搬轨道时还会让库自己断言失败。
    当前剪映的文本片段不需要 speed 素材，直接丢掉是安全的（与 jianying_tool
    处理 SRT 导入后的清理规则一致）。
    """
    known = _material_ids(content)
    for track in content.get("tracks") or []:
        if track.get("type") not in {"text", "subtitle"}:
            continue
        for segment in track.get("segments") or []:
            refs = segment.get("extra_material_refs")
            if isinstance(refs, list):
                segment["extra_material_refs"] = [ref for ref in refs if ref in known]


def _material_ids(content: dict[str, Any]) -> set[str]:
    """草稿里所有素材条目的 id。"""
    ids: set[str] = set()
    for pool in (content.get("materials") or {}).values():
        if isinstance(pool, list):
            ids.update(item["id"] for item in pool if isinstance(item, dict) and item.get("id"))
    return ids


def _segment_reference_ids(segments: Any) -> set[str]:
    """一组片段引用到的素材 id（本体 + 附加引用）。"""
    ids: set[str] = set()
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        if segment.get("material_id"):
            ids.add(segment["material_id"])
        ids.update(segment.get("extra_material_refs") or [])
    return ids


def _track_type_of(content: dict[str, Any], track_name: str) -> str:
    """按轨道名取类型；不存在或同名不同类型都报错。"""
    matches = sorted({
        str(track.get("type"))
        for track in content.get("tracks") or []
        if track.get("name") == track_name
    })
    if not matches:
        raise TemplateError(f"轨道不存在：{track_name}")
    if len(matches) > 1:
        raise TemplateError(f"同名轨道类型不一致：{track_name}（{', '.join(matches)}）")
    return matches[0]


def _find_text_track(content: dict[str, Any], track_name: str) -> dict[str, Any]:
    """按名字取唯一的文本轨。"""
    matches = [
        track for track in content.get("tracks") or [] if track.get("name") == track_name
    ]
    if not matches:
        raise TemplateError(f"轨道不存在：{track_name}")
    if len(matches) > 1:
        raise TemplateError(f"轨道名不唯一（{len(matches)} 条）：{track_name}")
    if matches[0].get("type") != "text":
        raise TemplateError(f"轨道 {track_name} 不是文本轨（{matches[0].get('type')}）")
    return matches[0]


def _required_material_length(content: dict[str, Any], material_name: str) -> int | None:
    """按名字替换时，新素材至少要多长才够用（微秒）；没有片段引用则 None。

    按"取用范围终点"算而不是片段时长：``replace_material_by_name`` 保留原来的
    ``source_timerange``，如果片段从素材第 6 秒开始取 2 秒，新素材就必须 ≥ 8 秒。
    """
    ids = {
        item.get("id")
        for item in (content.get("materials") or {}).get("videos") or []
        if item.get("material_name") == material_name
    }
    if not ids:
        return None
    needed: list[int] = []
    for track in content.get("tracks") or []:
        for segment in track.get("segments") or []:
            if segment.get("material_id") not in ids:
                continue
            source = segment.get("source_timerange") or {}
            if source:
                needed.append(int(source.get("start") or 0) + int(source.get("duration") or 0))
            else:
                needed.append(int((segment.get("target_timerange") or {}).get("duration") or 0))
    return max(needed) if needed else None


def _track_segments(content: dict[str, Any], track_name: str) -> list[dict[str, Any]]:
    """取某条轨道的片段列表；同名轨道不唯一时报错（与底层 get_imported_track 一致）。"""
    matches = [track for track in content.get("tracks") or [] if track.get("name") == track_name]
    if not matches:
        raise TemplateError(f"轨道不存在：{track_name}")
    if len(matches) > 1:
        raise TemplateError(f"轨道名不唯一（{len(matches)} 条）：{track_name}")
    return list(matches[0].get("segments") or [])


def _text_template_requirement(content: dict[str, Any], material_id: Any) -> int | None:
    """该片段是文字模板（花字）时要几段文本；普通文本返回 None。

    底层对文字模板是"给了几段就换几段"，给少了会把模板原文悄悄留在草稿里，
    所以必须在预检阶段就看出来。
    """
    for template in (content.get("materials") or {}).get("text_templates") or []:
        if isinstance(template, dict) and template.get("id") == material_id:
            return len(template.get("text_info_resources") or [])
    return None


def _unreferenced_materials(content: dict[str, Any]) -> list[str]:
    """列出没有被任何片段引用的视频/音频/文本素材（替换后留下的旧素材就在这）。"""
    referenced: set[str] = set()
    for track in content.get("tracks") or []:
        referenced |= _segment_reference_ids(track.get("segments"))
    labels: list[str] = []
    for pool_name in ("videos", "audios", "texts"):
        for item in (content.get("materials") or {}).get(pool_name) or []:
            if not isinstance(item, dict) or item.get("id") in referenced:
                continue
            label = (
                item.get("material_name")
                or item.get("name")
                or item.get("path")
                or item.get("id")
            )
            labels.append(f"{label}（{pool_name}）")
    return sorted(set(str(label) for label in labels))


def _source_timerange(source_start: Any, duration: Any):
    """把清单里的秒数取用范围换算成 Timerange；两个都缺省时返回 None。"""
    if source_start is None and duration is None:
        return None
    if duration is None:
        raise TemplateError("by_slot 指定了 source_start 就必须同时给 duration")
    from pyJianYingDraft.time_util import Timerange

    return Timerange(
        int(round(float(source_start or 0.0) * SEC)),
        int(round(float(duration) * SEC)),
    )


def _slot_key(track_type: str | None, track_name: str, index: int) -> str:
    kind = "text" if track_type == "text" else "material"
    return f"{track_name}[{index}]:{kind}"


def _unfilled_slots(content: dict[str, Any], covered: set[str]) -> list[str]:
    """列出清单没有覆盖到的槽位，提醒用户哪些地方还留着模板原样。"""
    unfilled: list[str] = []
    for track in content.get("tracks") or []:
        track_type = track.get("type")
        if track_type not in {"video", "audio", "text"}:
            continue
        for index, _segment in enumerate(track.get("segments") or []):
            key = _slot_key(track_type, str(track.get("name")), index)
            if key not in covered:
                unfilled.append(key)
    return sorted(unfilled)


def _covered_slots(
    content: dict[str, Any],
    by_name: set[str],
    by_slot: list[dict[str, Any]],
    texts: dict[str, list[Any]],
) -> set[str]:
    """清单覆盖到的槽位集合，用来算 unfilled_slots。"""
    covered: set[str] = set()
    if by_name:
        named_ids = {
            item.get("id")
            for item in (content.get("materials") or {}).get("videos") or []
            if item.get("material_name") in by_name
        }
        for track in content.get("tracks") or []:
            for index, segment in enumerate(track.get("segments") or []):
                if segment.get("material_id") in named_ids:
                    covered.add(_slot_key(track.get("type"), str(track.get("name")), index))
    for item in by_slot:
        track_name = str(item["track"])
        covered.add(
            _slot_key(
                _track_type_of(content, track_name), track_name, int(item["segment"])
            )
        )
    for track_name in texts:
        track = _find_text_track(content, str(track_name))
        for index, _segment in enumerate(track.get("segments") or []):
            covered.add(_slot_key("text", str(track_name), index))
    return covered


def _enum_member(enum_cls, value: str, field: str):
    """把清单里的字符串映射到 pyJianYingDraft 的策略枚举；非法值直接报错。"""
    try:
        return enum_cls[value]
    except KeyError as exc:
        allowed = ", ".join(member.name for member in enum_cls)
        raise TemplateError(f"{field} 取值不支持：{value}（可选 {allowed}）") from exc


def _prepare_row(
    content: dict[str, Any], row: dict[str, Any], base_dir: Path, defaults: dict[str, Any]
) -> dict[str, Any]:
    """只读预检一行：素材在不在、轨道能不能换、文案条数对不对、时长够不够。

    ``fill`` 的 dry-run 与 ``apply_row`` 共用这一份检查，保证"预检通过"与
    "真跑能过"是同一套标准。返回可直接执行的操作清单。
    """
    try:
        materials = row.get("materials") or {}
        by_name = materials.get("by_name") or {}
        by_slot = materials.get("by_slot") or []
        texts = row.get("texts") or {}

        loaded_by_name: list[tuple[str, Any]] = []
        for material_name, value in by_name.items():
            path = _media_path(base_dir, str(value))
            if not path.is_file():
                raise TemplateError(f"素材不存在：{path}")
            material = _load_video_material(path)
            if getattr(material, "material_type", "video") != "photo":
                needed = _required_material_length(content, str(material_name))
                if needed is None:
                    raise TemplateError(
                        f"没有片段引用名为 {material_name} 的视频素材"
                        f"（按名字替换只支持视频/图片，音频请用 by_slot）"
                    )
                if int(material.duration) < needed:
                    raise TemplateError(
                        f"新素材 {material.material_name} 时长 {material.duration / SEC:.3f}s"
                        f" 短于引用它的片段所需的取用范围 {needed / SEC:.3f}s；"
                        f"按名字替换不改片段的时长与取用范围，请改用 by_slot 指定取用范围"
                    )
            loaded_by_name.append((str(material_name), material))

        resolved_slots: list[dict[str, Any]] = []
        for item in by_slot:
            for field in ("track", "segment", "file"):
                if item.get(field) is None:
                    raise TemplateError(f"by_slot 缺少字段 {field}：{item}")
            track_name = str(item["track"])
            track_type = _track_type_of(content, track_name)
            if track_type not in {"video", "audio"}:
                raise TemplateError(
                    f"轨道 {track_name}（{track_type}）不能换素材："
                    f"by_slot 只支持视频/音频轨，文本请用 texts"
                )
            segments = _track_segments(content, track_name)
            index = int(item["segment"])
            if not 0 <= index < len(segments):
                raise TemplateError(
                    f"轨道 {track_name} 只有 {len(segments)} 个片段，"
                    f"清单里写的是片段 {index}"
                )
            path = _media_path(base_dir, str(item["file"]))
            if not path.is_file():
                raise TemplateError(f"素材不存在：{path}")
            resolved_slots.append(
                {
                    "track": track_name,
                    "track_type": track_type,
                    "segment": index,
                    "material": _load_video_material(path),
                    "timerange": _source_timerange(item.get("source_start"), item.get("duration")),
                }
            )

        resolved_texts: list[tuple[str, list[Any]]] = []
        for track_name, values in texts.items():
            if not isinstance(values, list):
                raise TemplateError(f"texts.{track_name} 必须是数组")
            track = _find_text_track(content, str(track_name))
            segments = track.get("segments") or []
            count = len(segments)
            if len(values) != count:
                raise TemplateError(
                    f"文本轨 {track_name} 有 {count} 个片段，但给了 {len(values)} 条文案"
                )
            for index, (value, segment) in enumerate(zip(values, segments)):
                required = _text_template_requirement(content, segment.get("material_id"))
                if required is None:
                    if not isinstance(value, str):
                        raise TemplateError(
                            f"文本轨 {track_name} 片段 {index} 是普通文本，文案必须是字符串"
                        )
                elif required > 1:
                    raise TemplateError(
                        f"轨道 {track_name} 片段 {index} 是文字模板（需要 {required} 段文本）："
                        f"清单每个片段只支持 1 段文案，先在剪映里把它改成普通文本再套模板"
                    )
            resolved_texts.append((str(track_name), list(values)))
    except TemplateError:
        raise
    except Exception as exc:
        raise TemplateError(f"{type(exc).__name__}: {exc}") from exc

    return {
        "by_name": loaded_by_name,
        "slots": resolved_slots,
        "texts": resolved_texts,
        "covered": _covered_slots(content, set(by_name), by_slot, texts),
    }


def apply_row(
    root: Path, plan: dict[str, Any], row: dict[str, Any], *, base_dir: Path
) -> dict[str, Any]:
    """把清单里的一行套用到目标草稿上（目标草稿必须已经存在）。

    先做只读预检（素材存在、轨道可解析、文案条数对得上、按名字替换不会截短片段），
    再在影子明文文件上一次性完成替换；任何一步失败都不会留下改了一半的草稿。
    """
    root = resolve_root(root)
    name = row.get("name")
    if not name:
        raise RowFailed("(未命名)", "清单行缺少 name")
    name = str(name)
    project = _project(root, name)
    content, _ = read_draft(root, name)

    defaults = plan.get("defaults") or {}
    replaced = {"by_name": 0, "by_slot": 0, "texts": 0}
    warnings: list[str] = []

    try:
        prepared = _prepare_row(content, row, base_dir, defaults)
        loaded_by_name = prepared["by_name"]
        resolved_slots = prepared["slots"]
        resolved_texts = prepared["texts"]

        def operation(script) -> None:
            from pyJianYingDraft.template_mode import ExtendMode, ShrinkMode
            from pyJianYingDraft.track import TrackType

            shrink = _enum_member(ShrinkMode, defaults.get("shrink", "cut_tail"), "shrink")
            extend = _enum_member(
                ExtendMode, defaults.get("extend", "cut_material_tail"), "extend"
            )
            for material_name, material in loaded_by_name:
                script.replace_material_by_name(material_name, material)
                replaced["by_name"] += 1
            for item in resolved_slots:
                track = script.get_imported_track(
                    TrackType[item["track_type"]], name=item["track"]
                )
                before = int(track.segments[item["segment"]].duration)
                script.replace_material_by_seg(
                    track,
                    item["segment"],
                    item["material"],
                    item["timerange"],
                    handle_shrink=shrink,
                    handle_extend=extend,
                )
                after = int(track.segments[item["segment"]].duration)
                replaced["by_slot"] += 1
                if after != before:
                    warnings.append(
                        f"track '{item['track']}' 片段 {item['segment']} 时长由 "
                        f"{before / SEC:.3f}s 调整为 {after / SEC:.3f}s"
                    )
            for track_name, values in resolved_texts:
                track = script.get_imported_track(TrackType.text, name=track_name)
                for index, value in enumerate(values):
                    script.replace_text(track, index, value)
                    replaced["texts"] += 1

        _edit_with_shadow(project, operation)
    except RowFailed:
        raise
    except TemplateError as exc:
        raise RowFailed(name, str(exc)) from exc
    except Exception as exc:  # 库抛的轨道/素材歧义、越界等，一律收敛成"该行失败"
        raise RowFailed(name, f"{type(exc).__name__}: {exc}") from exc

    updated, _ = read_draft(root, name)
    return {
        "row": name,
        "draft": str(project),
        "replaced": replaced,
        "warnings": warnings,
        "unfilled_slots": _unfilled_slots(updated, prepared["covered"]),
        "unreferenced_materials": _unreferenced_materials(updated),
    }


def import_track(
    root: Path,
    source_draft: str,
    target_draft: str,
    track: str,
    *,
    offset: float = 0.0,
    new_name: str | None = None,
) -> dict[str, Any]:
    """把源草稿里的一条轨道整条搬进目标草稿（素材条目随轨道一起过去）。

    底层保留素材 id，且明确不支持向同一草稿重复导入同一条轨道：目标已存在同名轨道，
    或已有同 id 素材时直接报错，不做静默改名或覆盖。
    """
    root = resolve_root(root)
    source_project = _project(root, source_draft)
    target_project = _project(root, target_draft)
    target_content, _ = read_draft(root, target_draft)

    imported_name = new_name or track
    if any(item.get("name") == imported_name for item in target_content.get("tracks") or []):
        raise TemplateError(f"目标草稿里已经有名为 {imported_name} 的轨道")

    source_content, _ = read_draft(root, source_draft)
    _prune_dangling_text_refs(source_content)
    source_shadow = _write_shadow(source_project, source_content)
    try:
        source_script = _load_template_script(source_shadow)
        candidates = [
            item for item in source_script.list_imported_tracks() if item.name == track
        ]
        if not candidates:
            raise TemplateError(f"源草稿 {source_draft} 里没有名为 {track} 的轨道")
        if len(candidates) > 1:
            raise TemplateError(f"源草稿里名为 {track} 的轨道有 {len(candidates)} 条")
        chosen = candidates[0]
        clash = _material_ids(target_content) & _segment_reference_ids(
            chosen.raw_data.get("segments")
        )
        if clash:
            raise TemplateError(
                f"目标草稿里已存在同 id 的素材（{len(clash)} 个），同一条轨道不能重复导入"
            )

        def operation(script) -> None:
            script.import_track(
                source_script,
                chosen,
                offset=int(round(float(offset) * SEC)),
                new_name=new_name,
            )

        _edit_with_shadow(target_project, operation)
    finally:
        source_shadow.unlink(missing_ok=True)

    return {
        "source": source_draft,
        "target": target_draft,
        "track": imported_name,
        "imported": True,
    }


def load_plan(path: Path) -> dict[str, Any]:
    """读清单 JSON 并检查必填字段。"""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateError(f"清单读不出来：{path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise TemplateError("清单必须是一个 JSON 对象")
    if not data.get("template"):
        raise TemplateError("清单缺少 template")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise TemplateError("清单缺少非空的 rows")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise TemplateError(f"清单第 {index} 行必须是对象，不是 {type(row).__name__}")
    return data


def fill(
    root: Path,
    plan: dict[str, Any],
    *,
    base_dir: Path,
    dry_run: bool = False,
    rows: set[int] | None = None,
) -> dict[str, Any]:
    """按清单批量出草稿：逐行复制模板 → 替换 → 汇总报告。

    预检（dry-run 也只做这一步）覆盖模板存在、目标名不撞已有草稿也不互相撞名、
    素材存在、轨道与文案可解析。任何一行失败只影响该行，整批继续；
    失败行的原因写进报告的 ``error``，由调用方决定退出码。
    """
    root = resolve_root(root)
    template = str(plan.get("template") or "")
    if not template:
        raise TemplateError("清单缺少 template")
    raw_rows = plan.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise TemplateError("清单缺少非空的 rows")
    if not (root / template).is_dir():
        raise TemplateError(f"模板草稿不存在：{root / template}")

    if rows is not None:
        unknown = sorted(index for index in rows if not 1 <= index <= len(raw_rows))
        if unknown:
            raise TemplateError(
                f"--rows 里的行号超出范围：{unknown}（清单共 {len(raw_rows)} 行）"
            )

    try:
        _assert_template_writable(root, template)
        template_content, _ = read_draft(root, template)
    except TemplateError:
        raise
    except Exception as exc:  # 编解码层读不出来（缺 DLL、路径不对）也算整批级别的错误
        raise TemplateError(
            f"模板草稿读不出来：{template}（{type(exc).__name__}: {exc}）"
        ) from exc

    defaults = plan.get("defaults") or {}
    require_all = bool(plan.get("require_all_slots"))
    selected = [
        (index, row)
        for index, row in enumerate(raw_rows, start=1)
        if rows is None or index in rows
    ]

    results: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    ok_count = 0
    for index, row in selected:
        entry: dict[str, Any] = {
            "row": (
                str(row.get("name")) if isinstance(row, dict) and row.get("name")
                else f"第 {index} 行"
            ),
            "index": index,
            "draft": None,
            "draft_ok": False,
            "error": None,
            "replaced": {"by_name": 0, "by_slot": 0, "texts": 0},
            "warnings": [],
            "unfilled_slots": [],
            "unreferenced_materials": [],
        }
        try:
            if not isinstance(row, dict):
                raise TemplateError(f"清单第 {index} 行必须是对象")
            name = _safe_name(row.get("name") or f"{template}-{index}")
            entry["row"] = name
            entry["draft"] = str(root / name)
            if name in seen:
                raise TemplateError(f"与第 {seen[name]} 行重名：{name}")
            seen[name] = index
            if (root / name).exists():
                raise TemplateError(f"目标草稿已存在：{root / name}")
            prepared = _prepare_row(template_content, row, base_dir, defaults)
            unfilled = _unfilled_slots(template_content, prepared["covered"])
            if require_all and unfilled:
                raise TemplateError(
                    f"还有槽位没填：{unfilled[0]}（共 {len(unfilled)} 个）"
                )
            if not dry_run:
                duplicate(root, template, name)
                detail = apply_row(root, plan, {**row, "name": name}, base_dir=base_dir)
                entry.update(detail)
            entry["unfilled_slots"] = unfilled
            entry["draft_ok"] = True
            ok_count += 1
        except Exception as exc:
            entry["error"] = (
                str(exc) if isinstance(exc, TemplateError) else f"{type(exc).__name__}: {exc}"
            )
        results.append(entry)

    return {
        "template": template,
        "dry_run": bool(dry_run),
        "rows": results,
        "ok_count": ok_count,
        "failed_count": len(results) - ok_count,
    }
