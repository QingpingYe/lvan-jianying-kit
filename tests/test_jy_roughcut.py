"""智能粗剪共享核心的测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import jy_roughcut  # noqa: E402


def make_scan(*files, root="E:\\素材\\第3集"):
    clips = [
        {
            "file": f,
            "duration": 2.0,
            "width": 1080,
            "height": 1920,
            "fps": 30.0,
            "has_audio": False,
            "warning": None,
        }
        for f in files
    ]
    return {
        "version": 1,
        "root": root,
        "generated": "2026-11-02T00:00:00",
        "clips": clips,
        "skipped": [{"file": "封面.png", "reason": "非视频"}],
    }


def make_grouping(shots=None, unresolved=None, excluded=None, root="E:\\素材\\第3集"):
    return {
        "version": 1,
        "root": root,
        "scheme": "test",
        "scheme_confidence": "high",
        "evidence": "测试用",
        "shots": shots or [],
        "unresolved": unresolved or [],
        "excluded": excluded or [],
    }


class ScanTests(unittest.TestCase):
    def test_scan_writes_metadata_and_skips_non_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mp4").write_bytes(b"x")
            (root / "notes.txt").write_text("x", encoding="utf-8")
            meta = {"duration": 3.2, "width": 1080, "height": 1920, "fps": 30.0, "has_audio": False}
            data = jy_roughcut.scan(root, probe=lambda p: dict(meta))
            self.assertEqual([c["file"] for c in data["clips"]], ["a.mp4"])
            self.assertEqual(data["skipped"][0]["file"], "notes.txt")
            self.assertTrue((root / "roughcut" / "scan.json").is_file())

    def test_probe_failure_keeps_the_clip_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.mp4").write_bytes(b"x")

            def probe(path):
                raise ValueError("boom")

            data = jy_roughcut.scan(root, probe=probe)
            clip = data["clips"][0]
            self.assertIsNone(clip["duration"])
            self.assertTrue(clip["warning"])


class GroupingTests(unittest.TestCase):
    def test_missing_coverage_is_an_error(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = make_grouping(
            shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}]
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("b.mp4" in e for e in result["errors"]))

    def test_duplicate_file_coverage_is_an_error(self):
        scan = make_scan("a.mp4")
        grouping = make_grouping(
            shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
            unresolved=[{"file": "a.mp4", "reason": "重复"}],
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("a.mp4" in e for e in result["errors"]))

    def test_unknown_file_reference_is_an_error(self):
        scan = make_scan("a.mp4")
        grouping = make_grouping(
            shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4", "c.mp4"]}]}]
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("c.mp4" in e for e in result["errors"]))

    def test_segmented_single_file_warns(self):
        scan = make_scan("a.mp4")
        grouping = make_grouping(
            shots=[{"shot": "01", "takes": [{"take": "01", "kind": "segmented", "files": ["a.mp4"]}]}]
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])

    def test_valid_grouping_passes(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = make_grouping(
            shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
            unresolved=[{"file": "b.mp4", "reason": "无法判断镜号"}],
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
