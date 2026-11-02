"""智能粗剪共享核心的测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import jy_roughcut  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
