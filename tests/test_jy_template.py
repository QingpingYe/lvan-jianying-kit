"""模板批量出片的单元测试。

不依赖剪映、不碰真实媒体：模板草稿用 pyJianYingDraft 在临时目录里现造，
素材对象用 ``object.__new__`` 造假（与 pyJianYingDraft 自带测试同法）。
运行：仓库根执行 ``python -m unittest discover -s tests -t . -v``
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

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


if __name__ == "__main__":
    unittest.main()
