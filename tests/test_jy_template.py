"""模板批量出片的单元测试。

不依赖剪映、不碰真实媒体：模板草稿用 pyJianYingDraft 在临时目录里现造，
素材对象用 ``object.__new__`` 造假（与 pyJianYingDraft 自带测试同法）。
运行：仓库根执行 ``python -m unittest discover -s tests -t . -v``
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jy_template  # noqa: E402

import pyJianYingDraft as draft  # noqa: E402
from jianying_tool.native_codec import DraftDecodeError  # noqa: E402
from jianying_tool.metadata import write_draft_meta_info  # noqa: E402
from pyJianYingDraft.local_materials import CropSettings, VideoMaterial  # noqa: E402


def fake_video_material(
    *,
    material_id: str = "video-material-id",
    material_name: str = "占位.mp4",
    path: str = r"E:\素材\占位.mp4",
    duration: int = 5_000_000,
    width: int = 1920,
    height: int = 1080,
    material_type: str = "video",
) -> VideoMaterial:
    """造一个不读磁盘的 VideoMaterial 对象。"""
    material = object.__new__(VideoMaterial)
    material.material_id = material_id
    material.local_material_id = ""
    material.material_name = material_name
    material.path = path
    material.duration = duration
    material.width = width
    material.height = height
    material.crop_settings = CropSettings()
    material.material_type = material_type
    return material


def make_template_draft(
    root: Path,
    name: str,
    *,
    material_name: str = "占位.mp4",
    material_path: str = r"E:\素材\占位.mp4",
    texts: tuple[str, ...] = ("第1集",),
    video_track: str = "main_video",
    text_track: str = "title",
    duration_us: int = 2_000_000,
) -> Path:
    """在 root 下造一个明文模板草稿（1 个视频片段 + 可选文本片段），返回草稿目录。"""
    script = draft.DraftFolder(str(root)).create_draft(name, 1080, 1920, fps=30)
    script.append_tracks([draft.TrackSpec(draft.TrackType.video, video_track)])
    material = fake_video_material(
        material_name=material_name, path=material_path, duration=duration_us
    )
    script.add_segment(draft.VideoSegment(material, draft.trange("0s", "2s")), video_track)
    if texts:
        script.append_tracks([draft.TrackSpec(draft.TrackType.text, text_track)])
        for index, text in enumerate(texts):
            script.add_segment(
                draft.TextSegment(text, draft.trange(f"{index * 2}s", "2s")), text_track
            )
    script.save()
    draft_path = root / name
    write_draft_meta_info(draft_path, root=root)
    return draft_path


def relative_files(folder: Path) -> list[str]:
    """目录下的相对文件清单，用来断言"复制没有多出/少掉文件"。"""
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file())


def make_ambiguous_text_tracks_draft(root: Path, name: str = "模板") -> Path:
    """造一个含两条同名文本轨的草稿（库不允许建同名轨道，改盘上名字来构造）。"""
    script = draft.DraftFolder(str(root)).create_draft(name, 1080, 1920, fps=30)
    script.append_tracks([draft.TrackSpec(draft.TrackType.video, "main_video")])
    script.add_segment(
        draft.VideoSegment(fake_video_material(), draft.trange("0s", "2s")), "main_video"
    )
    refs = script.append_tracks([
        draft.TrackSpec(draft.TrackType.text, "text"),
        draft.TrackSpec(draft.TrackType.text, "subtitle"),
        draft.TrackSpec(draft.TrackType.effect, "fx"),
    ])
    script.add_segment(draft.TextSegment("甲", draft.trange("0s", "2s")), refs[0])
    script.add_segment(draft.TextSegment("乙", draft.trange("0s", "2s")), refs[1])
    script.save()
    content_path = root / name / "draft_content.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    for track in content["tracks"]:
        if track.get("name") == "subtitle":
            track["name"] = "text"
    content_path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    write_draft_meta_info(root / name, root=root)
    return root / name


class DuplicateTests(unittest.TestCase):
    def test_duplicate_gives_the_copy_its_own_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            target = jy_template.duplicate(root, "模板", "第1集")
            src = json.loads((root / "模板" / "draft_meta_info.json").read_text(encoding="utf-8"))
            dst = json.loads((target / "draft_meta_info.json").read_text(encoding="utf-8"))
            self.assertNotEqual(src["draft_id"], dst["draft_id"])
            self.assertEqual(dst["draft_name"], "第1集")
            self.assertEqual(Path(dst["draft_fold_path"]).resolve(), target.resolve())

    def test_duplicate_excludes_the_backup_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = make_template_draft(root, "模板")
            backup = template / ".jianying-tool-backups"
            backup.mkdir()
            (backup / "draft_content.json.20260101").write_text("{}", encoding="utf-8")
            target = jy_template.duplicate(root, "模板", "第1集")
            self.assertFalse((target / ".jianying-tool-backups").exists())

    def test_duplicate_refuses_an_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            existing = make_template_draft(root, "第1集", material_name="别的.mp4")
            before = (existing / "draft_content.json").read_bytes()
            with self.assertRaises(FileExistsError):
                jy_template.duplicate(root, "模板", "第1集")
            self.assertEqual((existing / "draft_content.json").read_bytes(), before)

    def test_duplicate_keeps_outside_media_paths_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = r"E:\素材\第1集\x.mp4"
            template = make_template_draft(root, "模板", material_path=outside)
            target = jy_template.duplicate(root, "模板", "第1集")
            content = json.loads((target / "draft_content.json").read_text(encoding="utf-8"))
            paths = {item.get("path") for item in content.get("materials", {}).get("videos", [])}
            self.assertIn(outside, paths)
            self.assertEqual(relative_files(target), relative_files(template))

    def test_divergent_active_timeline_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = make_template_draft(root, "模板")
            timeline = template / "Timelines" / "timeline-1"
            timeline.mkdir(parents=True)
            (timeline / "draft_content.json").write_text('{"id": "other"}', encoding="utf-8")
            (template / "timeline_layout.json").write_text(
                json.dumps({"activeTimeline": "timeline-1"}), encoding="utf-8"
            )
            with self.assertRaises(DraftDecodeError):
                jy_template.write_draft(root, "模板", {"id": "root"})

    def test_duplicate_rejects_a_name_that_escapes_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 草稿根目录放在临时目录里层：万一守卫失效，逃逸目标也落在临时目录内被清掉
            root = Path(tmp) / "草稿"
            root.mkdir()
            make_template_draft(root, "模板")
            with self.assertRaises(jy_template.TemplateError):
                jy_template.duplicate(root, "模板", r"..\逃逸")
            self.assertFalse((root.parent / "逃逸").exists())


class SlotTests(unittest.TestCase):
    def test_lists_media_and_text_slots_in_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            slots = jy_template.list_slots(root, "模板")
            self.assertEqual(slots["draft"], "模板")
            tracks = {track["name"]: track for track in slots["tracks"]}
            video = tracks["main_video"]["segments"][0]
            self.assertEqual(tracks["main_video"]["type"], "video")
            self.assertEqual(video["kind"], "material")
            self.assertEqual(video["material_name"], "占位.mp4")
            self.assertEqual(video["duration"], 2.0)
            self.assertEqual(video["source_duration"], 2.0)
            self.assertEqual(video["index"], 0)
            text = tracks["title"]["segments"][0]
            self.assertEqual(text["kind"], "text")
            self.assertEqual(text["text"], "第1集")
            self.assertEqual(text["duration"], 2.0)

    def test_marks_unmodifiable_tracks_and_ambiguous_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_ambiguous_text_tracks_draft(root)
            tracks = jy_template.list_slots(root, "模板")["tracks"]
            text_tracks = [track for track in tracks if track["type"] == "text"]
            self.assertEqual(len(text_tracks), 2)
            for track in text_tracks:
                self.assertTrue(track["replaceable"])
                self.assertTrue(track["ambiguous_name"])
            effects = [track for track in tracks if track["type"] == "effect"]
            self.assertEqual(len(effects), 1)
            self.assertFalse(effects[0]["replaceable"])


class TempDraftCase(unittest.TestCase):
    """公共脚手架：在临时目录里造草稿，并用假素材替掉真实媒体探测。"""

    def setUp(self) -> None:
        self._durations: dict[str, int] = {}
        self._serial = 0
        patcher = mock.patch.object(jy_template, "_load_video_material", self._fake_material)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_material(self, path: Path) -> VideoMaterial:
        self._serial += 1
        name = Path(path).name
        return fake_video_material(
            material_id=f"new-material-{self._serial}",
            material_name=name,
            path=str(path),
            duration=self._durations.get(name, 5_000_000),
        )

    def media(self, folder: Path, name: str, duration_us: int = 5_000_000) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(b"fake-media")
        self._durations[name] = duration_us
        return path

    def content(self, root: Path, name: str) -> dict:
        return json.loads((root / name / "draft_content.json").read_text(encoding="utf-8"))

    def tree_snapshot(self, root: Path) -> dict[str, tuple[int, int]]:
        """目录树快照（相对路径 → 大小 + mtime），用来断言"什么都没写"。"""
        return {
            str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in root.rglob("*")
            if path.is_file()
        }


class ApplyRowTests(TempDraftCase):
    """单行替换：素材、文案、时长策略、失败路径与跨草稿搬轨道。"""

    def test_replace_by_name_updates_every_referencing_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = draft.DraftFolder(str(root)).create_draft("模板", 1080, 1920, fps=30)
            script.append_tracks([draft.TrackSpec(draft.TrackType.video, "main_video")])
            shared = fake_video_material(material_name="占位.mp4", duration=5_000_000)
            script.add_segment(draft.VideoSegment(shared, draft.trange("0s", "1s")), "main_video")
            script.add_segment(draft.VideoSegment(shared, draft.trange("1s", "1s")), "main_video")
            script.save()
            write_draft_meta_info(root / "模板", root=root)
            jy_template.duplicate(root, "模板", "第1集")

            new_video = self.media(root / "素材", "新.mp4")
            report = jy_template.apply_row(
                root,
                {"template": "模板"},
                {"name": "第1集", "materials": {"by_name": {"占位.mp4": str(new_video)}}},
                base_dir=root,
            )

            content = self.content(root, "第1集")
            videos = content["materials"]["videos"]
            by_id = {item["id"]: item for item in videos}
            self.assertFalse(any(item["path"] == r"E:\素材\占位.mp4" for item in videos))
            segments = content["tracks"][0]["segments"]
            self.assertEqual(len(segments), 2)
            for segment in segments:
                self.assertEqual(Path(by_id[segment["material_id"]]["path"]), new_video)
            self.assertEqual(report["replaced"]["by_name"], 1)
            leftovers = [p.name for p in (root / "第1集").iterdir() if p.name.startswith(".draft_content.")]
            self.assertEqual(leftovers, [])

    def test_replace_by_slot_reselects_the_source_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            jy_template.duplicate(root, "模板", "第1集")
            new_video = self.media(root / "素材", "新.mp4", 5_000_000)

            report = jy_template.apply_row(
                root,
                {"template": "模板"},
                {
                    "name": "第1集",
                    "materials": {
                        "by_slot": [
                            {
                                "track": "main_video",
                                "segment": 0,
                                "file": str(new_video),
                                "source_start": 1.0,
                                "duration": 1.5,
                            }
                        ]
                    },
                },
                base_dir=root,
            )

            segment = self.content(root, "第1集")["tracks"][0]["segments"][0]
            self.assertEqual(segment["source_timerange"]["start"], 1_000_000)
            self.assertEqual(segment["source_timerange"]["duration"], 1_500_000)
            self.assertEqual(segment["target_timerange"]["duration"], 1_500_000)
            self.assertEqual(report["replaced"]["by_slot"], 1)

    def test_shorter_material_truncates_the_segment_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            jy_template.duplicate(root, "模板", "第1集")
            short = self.media(root / "素材", "短.mp4", 1_000_000)

            report = jy_template.apply_row(
                root,
                {"template": "模板"},
                {
                    "name": "第1集",
                    "materials": {
                        "by_slot": [{"track": "main_video", "segment": 0, "file": str(short)}]
                    },
                },
                base_dir=root,
            )

            segment = self.content(root, "第1集")["tracks"][0]["segments"][0]
            self.assertEqual(segment["target_timerange"]["duration"], 1_000_000)
            self.assertTrue(
                any("main_video" in warning and "0" in warning for warning in report["warnings"]),
                report["warnings"],
            )

    def test_text_replacement_keeps_the_style_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            before = json.loads(
                self.content(root, "模板")["materials"]["texts"][0]["content"]
            )
            jy_template.duplicate(root, "模板", "第1集")
            new_text = "全新的第一集标题"

            report = jy_template.apply_row(
                root,
                {"template": "模板"},
                {"name": "第1集", "texts": {"title": [new_text]}},
                base_dir=root,
            )

            material = self.content(root, "第1集")["materials"]["texts"][0]
            body = json.loads(material["content"])
            self.assertEqual(body["text"], new_text)
            self.assertEqual(len(body["styles"]), len(before["styles"]))
            self.assertEqual(body["styles"][0]["range"], [0, len(new_text)])
            self.assertEqual(report["replaced"]["texts"], 1)

    def test_text_count_mismatch_fails_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板", texts=("第1集", "第2集"))
            jy_template.duplicate(root, "模板", "第1集")
            before = (root / "第1集" / "draft_content.json").read_bytes()

            with self.assertRaises(jy_template.RowFailed) as caught:
                jy_template.apply_row(
                    root,
                    {"template": "模板"},
                    {"name": "第1集", "texts": {"title": ["只给一条"]}},
                    base_dir=root,
                )

            self.assertIn("title", str(caught.exception))
            self.assertEqual((root / "第1集" / "draft_content.json").read_bytes(), before)

    def test_by_name_shorter_than_the_shortest_segment_fails_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            jy_template.duplicate(root, "模板", "第1集")
            before = (root / "第1集" / "draft_content.json").read_bytes()
            short = self.media(root / "素材", "短.mp4", 1_000_000)

            with self.assertRaises(jy_template.RowFailed) as caught:
                jy_template.apply_row(
                    root,
                    {"template": "模板"},
                    {"name": "第1集", "materials": {"by_name": {"占位.mp4": str(short)}}},
                    base_dir=root,
                )

            self.assertIn("by_slot", str(caught.exception))
            self.assertEqual((root / "第1集" / "draft_content.json").read_bytes(), before)

    def test_ambiguous_track_name_fails_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_ambiguous_text_tracks_draft(root)
            jy_template.duplicate(root, "模板", "第1集")

            with self.assertRaises(jy_template.RowFailed) as caught:
                jy_template.apply_row(
                    root,
                    {"template": "模板"},
                    {"name": "第1集", "texts": {"text": ["甲改了", "乙改了"]}},
                    base_dir=root,
                )

            self.assertIn("text", str(caught.exception))

    def test_import_track_copies_the_track_and_its_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            target_script = draft.DraftFolder(str(root)).create_draft("目标", 1080, 1920, fps=30)
            target_script.append_tracks([draft.TrackSpec(draft.TrackType.video, "main_video")])
            target_script.add_segment(
                draft.VideoSegment(fake_video_material(), draft.trange("0s", "1s")), "main_video"
            )
            target_script.save()
            write_draft_meta_info(root / "目标", root=root)

            report = jy_template.import_track(root, "模板", "目标", "title")

            self.assertTrue(report["imported"])
            content = self.content(root, "目标")
            imported = next(track for track in content["tracks"] if track["name"] == "title")
            self.assertEqual(len(imported["segments"]), 1)
            text_ids = {item["id"] for item in content["materials"]["texts"]}
            self.assertIn(imported["segments"][0]["material_id"], text_ids)

    def test_import_track_refuses_a_name_that_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            target_script = draft.DraftFolder(str(root)).create_draft("目标", 1080, 1920, fps=30)
            target_script.append_tracks([draft.TrackSpec(draft.TrackType.text, "title")])
            target_script.add_segment(draft.TextSegment("已有", draft.trange("0s", "1s")), "title")
            target_script.save()
            write_draft_meta_info(root / "目标", root=root)

            with self.assertRaises(jy_template.TemplateError):
                jy_template.import_track(root, "模板", "目标", "title")


class ApplyRowGuardTests(TempDraftCase):
    """复核发现的预检缺口：按名字替换的取用范围、文字模板、未被引用的素材。"""

    def test_by_name_guard_uses_the_source_range_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = draft.DraftFolder(str(root)).create_draft("模板", 1080, 1920, fps=30)
            script.append_tracks([draft.TrackSpec(draft.TrackType.video, "main_video")])
            material = fake_video_material(material_name="占位.mp4", duration=8_000_000)
            script.add_segment(
                draft.VideoSegment(
                    material, draft.trange("0s", "2s"), source_timerange=draft.trange("6s", "2s")
                ),
                "main_video",
            )
            script.save()
            write_draft_meta_info(root / "模板", root=root)
            jy_template.duplicate(root, "模板", "第1集")
            before = (root / "第1集" / "draft_content.json").read_bytes()

            too_short = self.media(root / "素材", "2.5s.mp4", 2_500_000)
            with self.assertRaises(jy_template.RowFailed) as caught:
                jy_template.apply_row(
                    root,
                    {"template": "模板"},
                    {"name": "第1集", "materials": {"by_name": {"占位.mp4": str(too_short)}}},
                    base_dir=root,
                )
            self.assertIn("取用范围", str(caught.exception))
            self.assertEqual((root / "第1集" / "draft_content.json").read_bytes(), before)

            long_enough = self.media(root / "素材", "8s.mp4", 8_000_000)
            report = jy_template.apply_row(
                root,
                {"template": "模板"},
                {"name": "第1集", "materials": {"by_name": {"占位.mp4": str(long_enough)}}},
                base_dir=root,
            )
            self.assertEqual(report["replaced"]["by_name"], 1)

    def test_report_lists_materials_left_without_any_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            jy_template.duplicate(root, "模板", "第1集")
            new_video = self.media(root / "素材", "新.mp4")

            report = jy_template.apply_row(
                root,
                {"template": "模板"},
                {
                    "name": "第1集",
                    "materials": {
                        "by_slot": [{"track": "main_video", "segment": 0, "file": str(new_video)}]
                    },
                },
                base_dir=root,
            )

            self.assertIn("占位.mp4", " ".join(report["unreferenced_materials"]))

    def test_text_template_segment_needing_more_text_fails_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = make_template_draft(root, "模板")
            content_path = template / "draft_content.json"
            content = json.loads(content_path.read_text(encoding="utf-8"))
            track = next(item for item in content["tracks"] if item["type"] == "text")
            track["segments"][0]["material_id"] = "text-template-1"
            for suffix in ("a", "b"):
                content["materials"]["texts"].append(
                    {
                        "id": f"sub-text-{suffix}",
                        "content": json.dumps({"text": "旧", "styles": []}, ensure_ascii=False),
                    }
                )
            content["materials"]["text_templates"] = [
                {
                    "id": "text-template-1",
                    "name": "花字模板",
                    "text_info_resources": [
                        {"text_material_id": "sub-text-a"},
                        {"text_material_id": "sub-text-b"},
                    ],
                }
            ]
            content_path.write_text(
                json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            write_draft_meta_info(template, root=root)
            jy_template.duplicate(root, "模板", "第1集")

            with self.assertRaises(jy_template.RowFailed) as caught:
                jy_template.apply_row(
                    root,
                    {"template": "模板"},
                    {"name": "第1集", "texts": {"title": ["一条文案"]}},
                    base_dir=root,
                )

            self.assertIn("文字模板", str(caught.exception))


class FillTests(TempDraftCase):
    """整批编排：失败不中断、dry-run 不落盘、行过滤、未填槽位与撞名。"""

    def video_row(self, name: str, file: Path) -> dict:
        return {
            "name": name,
            "materials": {
                "by_slot": [{"track": "main_video", "segment": 0, "file": str(file)}]
            },
        }

    def plan(self, rows: list[dict], **extra: object) -> dict:
        return {"template": "模板", "rows": rows, **extra}

    def test_failed_row_does_not_stop_the_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            good = self.media(root / "素材", "好.mp4")

            report = jy_template.fill(
                root,
                self.plan([
                    self.video_row("第1集", root / "素材" / "缺.mp4"),
                    self.video_row("第2集", good),
                ]),
                base_dir=root,
            )

            self.assertEqual((report["ok_count"], report["failed_count"]), (1, 1))
            self.assertTrue((root / "第2集").is_dir())
            self.assertFalse((root / "第1集").exists())
            self.assertFalse(report["rows"][0]["draft_ok"])
            self.assertIn("缺.mp4", report["rows"][0]["error"])

    def test_duplicate_row_names_fail_the_second_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            good = self.media(root / "素材", "好.mp4")

            report = jy_template.fill(
                root,
                self.plan([self.video_row("第1集", good), self.video_row("第1集", good)]),
                base_dir=root,
            )

            self.assertTrue(report["rows"][0]["draft_ok"])
            self.assertFalse(report["rows"][1]["draft_ok"])
            self.assertIn("重名", report["rows"][1]["error"])
            self.assertIn("1", report["rows"][1]["error"])

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            good = self.media(root / "素材", "好.mp4")
            before = self.tree_snapshot(root)

            report = jy_template.fill(
                root, self.plan([self.video_row("第1集", good)]), base_dir=root, dry_run=True
            )

            self.assertTrue(report["dry_run"])
            self.assertEqual(report["ok_count"], 1)
            self.assertEqual(self.tree_snapshot(root), before)
            self.assertFalse((root / "第1集").exists())

    def test_rows_filter_runs_only_the_selected_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            good = self.media(root / "素材", "好.mp4")
            rows = [
                {
                    "materials": {
                        "by_slot": [{"track": "main_video", "segment": 0, "file": str(good)}]
                    }
                }
                for _ in range(3)
            ]

            report = jy_template.fill(root, self.plan(rows), base_dir=root, rows={2})

            self.assertEqual(len(report["rows"]), 1)
            self.assertTrue((root / "模板-2").is_dir())
            self.assertFalse((root / "模板-1").exists())
            self.assertFalse((root / "模板-3").exists())

    def test_missing_template_fails_the_whole_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(jy_template.TemplateError):
                jy_template.fill(
                    root,
                    {"template": "不存在", "rows": [{"name": "第1集"}]},
                    base_dir=root,
                )
            self.assertEqual(list(root.iterdir()), [])

    def test_existing_target_draft_fails_that_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            existing = make_template_draft(root, "第1集", material_name="别人.mp4")
            before = (existing / "draft_content.json").read_bytes()
            good = self.media(root / "素材", "好.mp4")

            report = jy_template.fill(
                root, self.plan([self.video_row("第1集", good)]), base_dir=root
            )

            self.assertFalse(report["rows"][0]["draft_ok"])
            self.assertIn("已存在", report["rows"][0]["error"])
            self.assertEqual((existing / "draft_content.json").read_bytes(), before)

    def test_partial_row_lists_unfilled_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")

            report = jy_template.fill(
                root,
                self.plan([{"name": "第1集", "texts": {"title": ["改过的标题"]}}]),
                base_dir=root,
            )

            row = report["rows"][0]
            self.assertTrue(row["draft_ok"])
            self.assertEqual(row["unfilled_slots"], ["main_video[0]:material"])

    def test_require_all_slots_turns_warnings_into_a_failed_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")

            report = jy_template.fill(
                root,
                self.plan(
                    [{"name": "第1集", "texts": {"title": ["改过的标题"]}}],
                    require_all_slots=True,
                ),
                base_dir=root,
            )

            row = report["rows"][0]
            self.assertFalse(row["draft_ok"])
            self.assertIn("main_video[0]:material", row["error"])
            self.assertFalse((root / "第1集").exists())


class FillGuardTests(TempDraftCase):
    """复核发现的编排缺口：预检要挡住"复制之后才失败"的情况。"""

    def plan(self, rows: list[dict], **extra: object) -> dict:
        return {"template": "模板", "rows": rows, **extra}

    def test_by_slot_on_a_text_track_fails_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            new_video = self.media(root / "素材", "新.mp4")

            report = jy_template.fill(
                root,
                self.plan([
                    {
                        "name": "第1集",
                        "materials": {
                            "by_slot": [
                                {"track": "title", "segment": 0, "file": str(new_video)}
                            ]
                        },
                    }
                ]),
                base_dir=root,
            )

            self.assertFalse(report["rows"][0]["draft_ok"])
            self.assertIn("title", report["rows"][0]["error"])
            self.assertFalse((root / "第1集").exists())

    def test_segment_index_out_of_range_fails_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            new_video = self.media(root / "素材", "新.mp4")

            report = jy_template.fill(
                root,
                self.plan([
                    {
                        "name": "第1集",
                        "materials": {
                            "by_slot": [
                                {"track": "main_video", "segment": 5, "file": str(new_video)}
                            ]
                        },
                    }
                ]),
                base_dir=root,
            )

            self.assertFalse(report["rows"][0]["draft_ok"])
            self.assertIn("片段", report["rows"][0]["error"])
            self.assertFalse((root / "第1集").exists())

    def test_divergent_template_timeline_fails_the_whole_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = make_template_draft(root, "模板")
            timeline = template / "Timelines" / "timeline-1"
            timeline.mkdir(parents=True)
            (timeline / "draft_content.json").write_text('{"id": "other"}', encoding="utf-8")
            (template / "timeline_layout.json").write_text(
                json.dumps({"activeTimeline": "timeline-1"}), encoding="utf-8"
            )

            with self.assertRaises(jy_template.TemplateError):
                jy_template.fill(
                    root, self.plan([{"name": "第1集", "texts": {"title": ["甲"]}}]), base_dir=root
                )
            self.assertFalse((root / "第1集").exists())

    def test_row_name_with_path_separator_fails_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "草稿"
            root.mkdir()
            make_template_draft(root, "模板")

            report = jy_template.fill(
                root,
                self.plan([{"name": r"..\逃逸", "texts": {"title": ["甲"]}}]),
                base_dir=root,
            )

            self.assertFalse(report["rows"][0]["draft_ok"])
            self.assertFalse((root.parent / "逃逸").exists())

    def test_rows_out_of_range_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")

            with self.assertRaises(jy_template.TemplateError):
                jy_template.fill(
                    root,
                    self.plan([{"name": "第1集", "texts": {"title": ["甲"]}}]),
                    base_dir=root,
                    rows={4},
                )


class PlanTests(unittest.TestCase):
    """清单解析：行必须是对象，否则早失败而不是抛属性错误。"""

    def test_load_plan_rejects_a_row_that_is_not_an_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(
                json.dumps({"template": "模板", "rows": ["第1集"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(jy_template.TemplateError):
                jy_template.load_plan(path)


class CliTests(TempDraftCase):
    """命令行薄包装：退出码、stdout 上的报告、dry-run 不落盘。

    命令行用子进程跑，进程内 patch 的假素材不生效，所以这里只覆盖不需要媒体探测的
    文本替换；素材替换的真实探测路径交给端到端验收（用真实素材跑）。
    """

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "template_fill.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def write_plan(self, root: Path, plan: dict) -> Path:
        path = root / "plan.json"
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        return path

    def test_dry_run_reports_ok_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            plan = self.write_plan(
                root,
                {
                    "template": "模板",
                    "rows": [{"name": "第1集", "texts": {"title": ["新的标题"]}}],
                },
            )
            before = self.tree_snapshot(root)

            result = self.run_cli("--plan", str(plan), "--root", str(root), "--dry-run")

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["dry_run"])
            self.assertEqual(report["ok_count"], 1)
            self.assertEqual(self.tree_snapshot(root), before)

    def test_missing_plan_file_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "--plan", str(Path(tmp) / "缺.json"), "--root", tmp, "--dry-run"
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("清单", result.stderr)


class McpExtTests(TempDraftCase):
    """MCP 薄包装：注册了哪三个工具、返回什么、出错怎么抛。"""

    def test_build_server_registers_the_three_tools(self) -> None:
        from mcp_ext.server import build_server

        tools = asyncio.run(build_server().list_tools())
        self.assertEqual(
            {tool.name for tool in tools},
            {"list_template_slots", "duplicate_local_draft", "import_local_track"},
        )

    def test_duplicate_tool_returns_the_new_draft_path(self) -> None:
        from mcp_ext.server import duplicate_local_draft

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_template_draft(root, "模板")
            result = duplicate_local_draft("模板", "第1集", root=str(root))
            self.assertEqual(Path(result["draft"]).resolve(), (root / "第1集").resolve())
            self.assertTrue((root / "第1集").is_dir())

    def test_tools_reject_a_missing_source_draft(self) -> None:
        from mcp_ext.server import duplicate_local_draft

        with tempfile.TemporaryDirectory() as tmp:
            # 核心对"源草稿不存在"的契约就是 FileNotFoundError（见 duplicate 的约定）
            with self.assertRaises(FileNotFoundError):
                duplicate_local_draft("不存在", "第1集", root=tmp)


class EnvTests(unittest.TestCase):
    """剪映路径注入：插件模式下 paths 文件在用户级运行时目录里。"""

    def test_apply_jianying_env_falls_back_to_the_user_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            runtime = home / ".jianying-kit"
            runtime.mkdir()
            (runtime / "jianying-paths.json").write_text(
                json.dumps({"JIANYING_INSTALL_ROOT": r"D:\JianyingPro"}),
                encoding="utf-8",
            )
            clean = {
                key: value
                for key, value in os.environ.items()
                if key not in {"JIANYING_INSTALL_ROOT", "JIANYING_KIT_HOME", "USERPROFILE", "HOME"}
            }
            clean.update({"USERPROFILE": str(home), "HOME": str(home)})
            with mock.patch.dict(os.environ, clean, clear=True):
                applied = jy_template.apply_jianying_env(start=home / "空的")
                self.assertEqual(applied.get("JIANYING_INSTALL_ROOT"), r"D:\JianyingPro")
                self.assertEqual(os.environ["JIANYING_INSTALL_ROOT"], r"D:\JianyingPro")


if __name__ == "__main__":
    unittest.main()
