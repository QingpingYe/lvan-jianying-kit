#!/usr/bin/env python
"""分镜表 -> 剪映草稿构建器。

基于 jianying-tool 的 CLI（调用本机剪映自带的编解码能力），把一份 JSON 分镜表
组装成一个可以直接在剪映专业版里打开、继续精修的草稿。

用法：
    python build_draft.py --shotlist ep01.json
    python build_draft.py --shotlist ep01.json --dry-run

分镜表结构见 shotlist.example.json。相对路径按分镜表所在目录解析。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _runtime_json_candidates(
    *,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    plugin_root: Path | None = None,
) -> list[Path]:
    """runtime.json 的查找顺序：JIANYING_KIT_HOME → 用户目录 → 插件目录。"""
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    here = Path(__file__).resolve().parent
    root = Path(plugin_root) if plugin_root is not None else here.parent
    candidates: list[Path] = []
    kit_home = env.get("JIANYING_KIT_HOME")
    if kit_home:
        candidates.append(Path(kit_home) / "runtime.json")
    try:
        candidates.append(home / ".jianying-kit" / "runtime.json")
    except RuntimeError:
        pass
    candidates += [root / "runtime.json", here / "runtime.json"]
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def venv_python(
    explicit: str | Path | None = None,
    *,
    plugin_root: Path | None = None,
    home: Path | None = None,
    env: dict[str, str] | None = None,
) -> Path | None:
    """解析运行 jianying-tool 的 Python。

    顺序：显式 --python → runtime.json 里的运行时（插件模式的正确位置）→
    插件根/.venv。找不到时返回 None，由调用方给出可执行的修复提示，绝不返回
    一个看起来像路径但不存在的 exe。
    """
    if explicit:
        return Path(explicit)
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    for path in _runtime_json_candidates(env=env, home=home, plugin_root=plugin_root):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        python = data.get("python") if isinstance(data, dict) else None
        if isinstance(python, str) and python.strip() and Path(python).is_file():
            return Path(python)
    here = Path(__file__).resolve().parent
    bases = [Path(plugin_root)] if plugin_root is not None else [here.parent]
    bases.append(here)
    for base in bases:
        for parts in (("Scripts", "python.exe"), ("bin", "python3"), ("bin", "python")):
            candidate = base.joinpath(".venv", *parts)
            if candidate.is_file():
                return candidate
    return None


def load_jianying_env(
    *,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    plugin_root: Path | None = None,
) -> dict[str, str]:
    """读取安装器写入的 jianying-paths.json。

    剪映装在非默认位置（或草稿目录被改过）时，安装器会把实际路径记录在这里，
    这里注入给 jianying-tool 子进程，避免它去找默认路径。

    兼容两种布局：老安装器写的独立 ``jianying-paths.json``，以及插件模式
    ``runtime.json`` 里的 ``jianying_paths`` 块。
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    here = Path(__file__).resolve().parent
    root = Path(plugin_root) if plugin_root is not None else here.parent
    flat_candidates: list[Path] = []
    kit_home = env.get("JIANYING_KIT_HOME")
    if kit_home:
        flat_candidates.append(Path(kit_home) / "jianying-paths.json")
    flat_candidates.append(home / ".jianying-kit" / "jianying-paths.json")
    flat_candidates += [root / "jianying-paths.json", here / "jianying-paths.json"]
    for path in flat_candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {k: str(v) for k, v in data.items() if isinstance(v, str) and v}
    for path in _runtime_json_candidates(env=env, home=home, plugin_root=plugin_root):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        nested = data.get("jianying_paths") if isinstance(data, dict) else None
        if isinstance(nested, dict):
            return {k: str(v) for k, v in nested.items() if isinstance(v, str) and v}
    return {}


# 让本进程内的解码逻辑（读加密草稿）也能拿到这些路径
for _key, _value in load_jianying_env().items():
    os.environ.setdefault(_key, _value)


class Builder:
    def __init__(self, python: Path, root: str | None, dry_run: bool) -> None:
        self.python = python
        self.root = root
        self.dry_run = dry_run

    def run(self, *args: str, raw: bool = False) -> dict:
        cmd = [str(self.python), "-m", "jianying_tool.cli", *args]
        if self.root and not raw:
            cmd += ["--root", self.root]
        printable = " ".join(f'"{a}"' if " " in a else a for a in cmd[3:])
        if self.dry_run:
            print(f"[dry-run] [cmd] {printable}")
            return {}
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        for key, value in load_jianying_env().items():
            env.setdefault(key, value)
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env,
        )
        if proc.returncode != 0:
            raise SystemExit(
                f"命令失败（exit {proc.returncode}）：[cmd] {printable}\n"
                f"[stdout]\n{proc.stdout}\n[stderr]\n{proc.stderr}"
            )
        print(f"[ok] [cmd] {printable}")
        out = (proc.stdout or "").strip()
        try:
            return json.loads(out) if out.startswith("{") else {}
        except json.JSONDecodeError:
            return {}


def resolve(base: Path, value: str) -> str:
    p = Path(value)
    return str(p if p.is_absolute() else (base / p).resolve())


def sync_card_meta(draft_dir: Path) -> None:
    """同步草稿卡片上的时长/素材大小（剪映只在自己保存时才刷新，这里主动补一次）。"""
    content_path = draft_dir / "draft_content.json"
    meta_path = draft_dir / "draft_meta_info.json"
    if not content_path.exists() or not meta_path.exists():
        return
    content = read_draft_content(content_path)
    if content is None:
        print("[skip] 草稿主文件不是明文，卡片元数据留给剪映自己刷新")
        return
    end_us = 0
    for track in content.get("tracks", []):
        for seg in track.get("segments", []):
            tr = seg.get("target_timerange") or {}
            end_us = max(end_us, int(tr.get("start", 0)) + int(tr.get("duration", 0)))
    media_dir = draft_dir / "media"
    media_size = sum(f.stat().st_size for f in media_dir.rglob("*") if f.is_file()) if media_dir.is_dir() else 0
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # 新版剪映会把 draft_meta_info.json 一起加密；卡片信息留给剪映打开时自己刷新
        print(f"[skip] 草稿元信息是密文，卡片时长/大小留给剪映刷新（当前工程时长 {end_us / 1e6:.2f}s）")
        return
    meta["tm_duration"] = end_us
    if media_size:
        meta["draft_timeline_materials_size_"] = media_size
    meta["tm_draft_modified"] = int(time.time() * 1_000_000)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[ok] 卡片元数据已同步：时长 {end_us / 1e6:.2f}s，素材 {media_size / 1048576:.1f}MB")


def read_draft_content(path: Path) -> dict | None:
    """读草稿内容：先按明文 JSON，失败则用本机剪映的编解码能力（加密草稿）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    try:
        from jianying_tool.native_codec import decode_file
        content, _ = decode_file(path)
        return content
    except Exception:
        return None


def project_duration_seconds(draft_dir: Path | None) -> float | None:
    """读草稿当前的真实工程时长（秒）。读不到就返回 None。"""
    if draft_dir is None:
        return None
    path = draft_dir / "draft_content.json"
    if not path.is_file():
        return None
    content = read_draft_content(path)
    if content is None:
        return None
    end_us = 0
    for track in content.get("tracks", []):
        for seg in track.get("segments", []):
            tr = seg.get("target_timerange") or {}
            end_us = max(end_us, int(tr.get("start", 0)) + int(tr.get("duration", 0)))
    return end_us / 1e6 if end_us else None


def existing_video_segment_count(draft_dir: Path, track: str = "main_video") -> int | None:
    """读已有草稿指定视频轨的片段数；读不到时返回 None（交给后续流程报错）。"""
    try:
        import jy_template
    except ImportError:
        return None
    try:
        content, _ = jy_template.read_draft(draft_dir.parent, draft_dir.name)
    except Exception:  # noqa: BLE001 - 明文/密文都可能读失败，读失败不在这里下结论
        return None
    for item in content.get("tracks", []):
        if item.get("name") == track or (track == "main_video" and item.get("type") == "video"):
            return len(item.get("segments") or [])
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="按分镜表生成剪映草稿")
    ap.add_argument("--shotlist", required=True, help="分镜表 JSON 路径")
    ap.add_argument("--root", help="剪映草稿目录，默认由工具自动探测")
    ap.add_argument("--name", help="覆盖分镜表里的 draft_name（例如同一模板批量出 ep01/ep02）")
    ap.add_argument("--python", help="运行 jianying-tool 的 python.exe，默认用同级 .venv")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令")
    ap.add_argument("--existing", action="store_true",
                    help="目标草稿已存在（例如先在剪映里建好空草稿），跳过创建、直接填入内容")
    ap.add_argument("--force", action="store_true",
                    help="跳过剪映占用/非空草稿保护；确认风险后才使用")
    args = ap.parse_args(argv)

    python_exe = venv_python(args.python)
    if python_exe is None:
        raise SystemExit(
            "找不到可用的 Python 环境：请先运行 install.ps1 完成运行时安装"
            "（路径会写入 %USERPROFILE%\\.jianying-kit\\runtime.json），"
            "或用 --python 指定安装了 jianying-tool 的 python.exe。"
        )

    shotlist_path = Path(args.shotlist).resolve()
    base = shotlist_path.parent
    spec = json.loads(shotlist_path.read_text(encoding="utf-8"))

    name = args.name or spec["draft_name"]
    canvas = spec.get("canvas", {})
    width = int(canvas.get("width", 1080))
    height = int(canvas.get("height", 1920))
    fps = float(canvas.get("fps", 30))
    shots = spec["shots"]
    if not shots:
        raise SystemExit("分镜表里没有任何镜头")
    video_track = spec.get("video_track", "main_video")

    b = Builder(python_exe, args.root, args.dry_run)

    info = {} if args.dry_run else b.run("probe", "--json", raw=True)
    draft_root = Path(args.root) if args.root else (
        Path(info["draft_root"]) if info.get("draft_root") else None
    )
    if draft_root and not args.dry_run:
        target = draft_root / name
        if args.existing and not target.exists():
            raise SystemExit(f"--existing 模式要求草稿已存在，但没找到：{target}")
        if not args.existing and target.exists():
            raise SystemExit(
                f"草稿已存在：{target}\n换个 draft_name，或加 --existing 往这个草稿里填内容"
            )
        if args.existing:
            count = existing_video_segment_count(target, video_track)
            if count and count > 0 and not args.force:
                raise SystemExit(
                    f"目标草稿非空：--existing 草稿的 {video_track} 轨道已有 {count} 个视频片段。\n"
                    "本模式只保证在空草稿上正确追加；请换新草稿名，"
                    "确认要叠加内容时再加 --force。"
                )
            try:
                import jy_template

                jy_template.assert_draft_writable(
                    draft_root, name, force=args.force
                )
            except ImportError:
                pass
            except Exception as exc:  # noqa: BLE001 - 统一转成面向用户的错误
                raise SystemExit(f"写前检查未通过：{exc}") from exc

    missing = [s["file"] for s in shots if not Path(resolve(base, s["file"])).exists()]
    for extra in filter(None, [spec.get("bgm", {}).get("file"), spec.get("subtitles", {}).get("srt")]):
        if not Path(resolve(base, extra)).exists():
            missing.append(extra)
    if missing:
        raise SystemExit("素材缺失：\n  " + "\n  ".join(missing))

    draft_dir = (draft_root / name) if draft_root else None

    if args.existing:
        cursor, pending = 0.0, list(shots)
    else:
        first, rest = shots[0], shots[1:]
        cmd = [
            "create",
            "--name", name,
            "--video", resolve(base, first["file"]),
        ]
        if first.get("duration") is not None:
            cmd += ["--duration", str(first["duration"])]
        cmd += ["--width", str(width), "--height", str(height)]
        b.run(*cmd)
        if first.get("source_start") is not None and float(first["source_start"]) != 0:
            b.run(
                "edit-segment",
                "--draft", name,
                "--track", first.get("track", video_track),
                "--segment", "0",
                "--source-start", str(first["source_start"]),
            )
        cursor, pending = float(first.get("duration") or 0), rest

    for shot in pending:
        cmd = [
            "append-video",
            "--draft", name,
            "--video", resolve(base, shot["file"]),
            "--track", shot.get("track", video_track),
        ]
        # 不显式指定 target_start 时，让工具接在当前工程末尾 —— 比按累加值摆放更稳，
        # 因为剪映会把片段时长取整到自己的粒度，累加值差几微秒就会被判成重叠。
        if shot.get("target_start") is not None:
            cmd += ["--target-start", str(shot["target_start"])]
        if shot.get("source_start") is not None:
            cmd += ["--source-start", str(shot["source_start"])]
        if shot.get("duration") is not None:
            cmd += ["--source-duration", str(shot["duration"])]
        if shot.get("speed") is not None:
            cmd += ["--speed", str(shot["speed"])]
        b.run(*cmd)
        if shot.get("target_start") is not None and shot.get("duration") is not None:
            span = float(shot["duration"]) / float(shot.get("speed", 1) or 1)
            cursor = max(cursor, float(shot["target_start"]) + span)

    b.run("project-settings", "--draft", name,
          "--width", str(width), "--height", str(height),
          "--ratio", canvas.get("ratio", "9:16"), "--fps", str(fps))

    subtitles = spec.get("subtitles")
    if subtitles and subtitles.get("srt"):
        cmd = ["subtitle", "--draft", name, "--srt", resolve(base, subtitles["srt"])]
        if subtitles.get("offset"):
            cmd += ["--offset", str(subtitles["offset"])]
        b.run(*cmd)

    bgm = spec.get("bgm")
    if bgm and bgm.get("file"):
        if bgm.get("source_duration") is None:
            actual = project_duration_seconds(draft_dir) or cursor
            bgm = {**bgm, "source_duration": round(actual, 3)}
        cmd = [
            "append-audio",
            "--draft", name,
            "--audio", resolve(base, bgm["file"]),
            "--track", bgm.get("track", "bgm"),
        ]
        for key in ("target_start", "source_start", "source_duration",
                    "volume", "fade_in", "fade_out", "speed"):
            if bgm.get(key) is not None:
                cmd += [f"--{key.replace('_', '-')}", str(bgm[key])]
        b.run(*cmd)

    if spec.get("voiceover", {}).get("script"):
        vo = spec["voiceover"]
        cmd = [
            "voiceover",
            "--draft", name,
            "--script", resolve(base, vo["script"]),
            "--engine", vo.get("engine", "auto"),
        ]
        for key in ("voice", "rate", "pitch", "track", "volume",
                    "fade_in", "fade_out", "gap", "subtitle_track"):
            if vo.get(key) is not None:
                cmd += [f"--{key.replace('_', '-')}", str(vo[key])]
        if vo.get("subtitles"):
            cmd += ["--subtitles"]
        b.run(*cmd)

    draft_dir = (draft_root / name) if draft_root else None
    verification: dict = {}
    if draft_dir and draft_dir.exists() and not args.dry_run:
        sync_card_meta(draft_dir)
        expected = [
            {
                "source_start": float(shot.get("source_start", 0.0)),
                "duration": float(shot["duration"]),
                "target_start": shot.get("target_start"),
            }
            for shot in shots
            if shot.get("duration") is not None
        ]
        try:
            import jy_template

            verification = jy_template.verify_timeline(
                draft_dir.parent, name, expected, track=video_track, fps=fps
            )
        except Exception as exc:  # noqa: BLE001 - 核对失败要明确报错，不能静默通过
            verification = {"ok": False, "errors": [f"写后核对无法执行：{exc}"]}
        if not verification.get("ok"):
            detail = "\n  ".join(verification.get("errors") or ["草稿时间线与计划不一致"])
            raise SystemExit(
                "写后核对失败，草稿可能被剪映覆盖或写入不完整：\n  "
                + detail
                + "\n请完全退出剪映后重建；若确认草稿已被外部修改，请先另存新草稿名。"
            )
        try:
            import jy_template

            if jy_template.jianying_process_ids():
                print("[warn] 剪映正在运行：新草稿可能不会立刻出现在草稿列表里，必要时重启剪映刷新。")
        except Exception:  # noqa: BLE001 - 提示失败不影响交付
            pass

    result = b.run("doctor", str(draft_dir) if draft_dir else name, raw=True)
    print(json.dumps(
        {"draft": name, "doctor": result, "verification": verification},
        ensure_ascii=False,
        indent=2,
    ))
    if not args.dry_run:
        print(f"\n草稿已生成：{name}（在剪映草稿首页查看，可能需要重启剪映刷新列表）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
