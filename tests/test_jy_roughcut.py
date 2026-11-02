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


def make_plan(shots, *, canvas=None, episode="第3集", source_root="E:\\素材\\第3集", extra=None):
    plan = {
        "version": 1,
        "episode": episode,
        "source_root": source_root,
        "canvas": canvas or {"width": 1080, "height": 1920, "ratio": "9:16", "fps": 30},
        "shots": shots,
        "warnings": [],
    }
    if extra:
        plan.update(extra)
    return plan


def make_shot(shot, files, *, durations=None, confidence=0.9, review=None,
              transition=None, alternates=None, reason="测试理由"):
    durations = durations or [2.0] * len(files)
    return {
        "shot": shot,
        "script_ref": "",
        "segments": [
            {"file": f, "source_start": 0.0, "duration": d}
            for f, d in zip(files, durations)
        ],
        "reason": reason,
        "confidence": confidence,
        "alternates": alternates or [],
        "transition_out": transition or {"type": "hard_cut"},
        "effects": [],
        "review": review or [],
    }


def grouping_for(shot_files, unresolved=None):
    shots = [
        {"shot": sid, "takes": [{"take": "01", "kind": "single", "files": [f]}]}
        for sid, f in shot_files.items()
    ]
    return make_grouping(shots=shots, unresolved=unresolved)


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


class PlanTests(unittest.TestCase):
    def test_interval_beyond_stream_duration_is_an_error(self):
        scan = make_scan("a.mp4")  # 视频流 2.0s
        grouping = grouping_for({"01": "a.mp4"})
        shot = make_shot("01", ["a.mp4"], durations=[1.0])
        shot["segments"][0]["source_start"] = 1.5
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("超出" in e for e in result["errors"]))

    def test_missing_shot_is_an_error(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = grouping_for({"01": "a.mp4", "02": "b.mp4"})
        result = jy_roughcut.validate_plan(make_plan([make_shot("01", ["a.mp4"])]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("02" in e for e in result["errors"]))

    def test_low_confidence_requires_review_entry(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        shot = make_shot("01", ["a.mp4"], confidence=0.5)
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertFalse(result["ok"])
        shot["review"] = ["[低置信] 两条 take 差异小"]
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertTrue(result["ok"])

    def test_unresolved_file_in_plan_is_an_error(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = grouping_for({"01": "a.mp4"}, unresolved=[{"file": "b.mp4", "reason": "无法判断"}])
        result = jy_roughcut.validate_plan(make_plan([make_shot("01", ["b.mp4"])]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("b.mp4" in e for e in result["errors"]))

    def test_dissolve_duration_out_of_range_warns(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        shot = make_shot("01", ["a.mp4"], transition={"type": "dissolve", "duration": 1.5})
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])

    def test_unknown_transition_type_only_warns(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        shot = make_shot("01", ["a.mp4"], transition={"type": "flash"})
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertTrue(result["ok"])
        self.assertTrue(any("只标注" in w for w in result["warnings"]))

    def test_valid_plan_passes(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        result = jy_roughcut.validate_plan(make_plan([make_shot("01", ["a.mp4"])]), scan, grouping)
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])


class ShotlistTests(unittest.TestCase):
    def test_target_start_accumulates(self):
        plan = make_plan(
            [
                make_shot("01", ["a.mp4"], durations=[2.0]),
                make_shot("02", ["b.mp4"], durations=[3.5]),
                make_shot("03", ["c.mp4"], durations=[1.0]),
            ]
        )
        shotlist = jy_roughcut.plan_to_shotlist(plan)
        self.assertEqual([s["target_start"] for s in shotlist["shots"]], [0.0, 2.0, 5.5])
        self.assertEqual([s["duration"] for s in shotlist["shots"]], [2.0, 3.5, 1.0])

    def test_default_draft_name_uses_episode(self):
        plan = make_plan([make_shot("01", ["a.mp4"])], episode="第3集")
        shotlist = jy_roughcut.plan_to_shotlist(plan)
        self.assertEqual(shotlist["draft_name"], "第3集-粗剪")

    def test_explicit_draft_name_wins(self):
        plan = make_plan([make_shot("01", ["a.mp4"])])
        shotlist = jy_roughcut.plan_to_shotlist(plan, draft_name="自定义名")
        self.assertEqual(shotlist["draft_name"], "自定义名")

    def test_passthrough_blocks_survive(self):
        plan = make_plan(
            [make_shot("01", ["a.mp4"])],
            extra={
                "subtitles": {"srt": "ep03.srt"},
                "bgm": {"file": "bgm.m4a", "volume": 0.35},
                "voiceover": {"script": "lines.txt"},
            },
        )
        shotlist = jy_roughcut.plan_to_shotlist(plan)
        self.assertEqual(shotlist["subtitles"], {"srt": "ep03.srt"})
        self.assertEqual(shotlist["bgm"], {"file": "bgm.m4a", "volume": 0.35})
        self.assertEqual(shotlist["voiceover"], {"script": "lines.txt"})


if __name__ == "__main__":
    unittest.main()
