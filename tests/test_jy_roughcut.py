"""智能粗剪共享核心的测试。"""

from __future__ import annotations

import asyncio
import json
import subprocess
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

    def test_root_mismatch_with_scan_is_an_error(self):
        scan = make_scan("a.mp4", root="E:\\A")
        grouping = make_grouping(
            shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
            root="E:\\B",
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("root" in e for e in result["errors"]))

    def test_duplicate_shot_identifiers_are_an_error(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = make_grouping(
            shots=[
                {"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]},
                {"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["b.mp4"]}]},
            ]
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("重复" in e for e in result["errors"]))

    def test_duplicate_take_identifiers_are_an_error(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = make_grouping(
            shots=[
                {
                    "shot": "01",
                    "takes": [
                        {"take": "01", "kind": "single", "files": ["a.mp4"]},
                        {"take": "01", "kind": "single", "files": ["b.mp4"]},
                    ],
                }
            ]
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("take" in e and "重复" in e for e in result["errors"]))

    def test_grouping_requires_scheme_and_evidence(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        for key in ("scheme", "scheme_confidence", "evidence"):
            grouping.pop(key)
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        for key in ("scheme", "scheme_confidence", "evidence"):
            self.assertTrue(any(key in e for e in result["errors"]))

    def test_unresolved_requires_reason(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = make_grouping(
            shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
            unresolved=[{"file": "b.mp4"}],
        )
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("reason" in e for e in result["errors"]))

    def test_grouping_structure_errors_do_not_crash(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        grouping["shots"] = "oops"
        result = jy_roughcut.validate_grouping(grouping, scan)
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])


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

    def test_confidence_below_threshold_is_rejected_even_with_review(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        shot = make_shot("01", ["a.mp4"], confidence=0.5, review=["[低置信] 两条 take 差异小"])
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("0.6" in e for e in result["errors"]))

    def test_backup_grade_confidence_requires_review(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        shot = make_shot("01", ["a.mp4"], confidence=0.7)
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("review" in e for e in result["errors"]))
        shot["review"] = ["[低置信] 两条 take 差异小"]
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertTrue(result["ok"])

    def test_unresolved_file_in_plan_is_an_error(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = grouping_for({"01": "a.mp4"}, unresolved=[{"file": "b.mp4", "reason": "无法判断"}])
        result = jy_roughcut.validate_plan(make_plan([make_shot("01", ["b.mp4"])]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("b.mp4" in e for e in result["errors"]))

    def test_dissolve_duration_out_of_range_is_an_error(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        for bad in (1.5, 0.2, "0.5", True, float("nan")):
            shot = make_shot("01", ["a.mp4"], transition={"type": "dissolve", "duration": bad})
            result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
            self.assertFalse(result["ok"], f"duration={bad!r} 应报错")

    def test_dissolve_duration_in_range_passes(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        shot = make_shot("01", ["a.mp4"], transition={"type": "dissolve", "duration": 0.5})
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])

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

    def test_source_root_mismatch_is_an_error(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        plan = make_plan([make_shot("01", ["a.mp4"])], source_root="E:\\其它")
        result = jy_roughcut.validate_plan(plan, scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("source_root" in e for e in result["errors"]))

    def test_unselected_take_must_be_recorded_in_alternates(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = make_grouping(
            shots=[
                {
                    "shot": "01",
                    "takes": [
                        {"take": "01", "kind": "single", "files": ["a.mp4"]},
                        {"take": "02", "kind": "single", "files": ["b.mp4"]},
                    ],
                }
            ]
        )
        plan = make_plan([make_shot("01", ["a.mp4"])])
        result = jy_roughcut.validate_plan(plan, scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("b.mp4" in e and "alternates" in e for e in result["errors"]))
        shot = make_shot("01", ["a.mp4"], alternates=[{"file": "b.mp4", "note": "中段崩坏"}])
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertTrue(result["ok"])

    def test_alternate_requires_note(self):
        scan = make_scan("a.mp4", "b.mp4")
        grouping = make_grouping(
            shots=[
                {
                    "shot": "01",
                    "takes": [
                        {"take": "01", "kind": "single", "files": ["a.mp4"]},
                        {"take": "02", "kind": "single", "files": ["b.mp4"]},
                    ],
                }
            ]
        )
        shot = make_shot("01", ["a.mp4"], alternates=[{"file": "b.mp4"}])
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("note" in e for e in result["errors"]))

    def test_required_plan_blocks_must_be_present(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        for key in ("alternates", "transition_out", "review"):
            shot = make_shot("01", ["a.mp4"])
            shot.pop(key)
            result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
            self.assertFalse(result["ok"], f"缺少 {key} 应报错")

    def test_empty_plan_is_an_error(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        result = jy_roughcut.validate_plan(make_plan([]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("没有任何镜头" in e for e in result["errors"]))

    def test_all_unresolved_and_empty_plan_is_an_error(self):
        scan = make_scan("a.mp4")
        grouping = make_grouping(shots=[], unresolved=[{"file": "a.mp4", "reason": "纯序号"}])
        self.assertTrue(jy_roughcut.validate_grouping(grouping, scan)["ok"])
        result = jy_roughcut.validate_plan(make_plan([]), scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("没有任何镜头" in e for e in result["errors"]))

    def test_plan_structure_errors_do_not_crash(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        plan = make_plan([make_shot("01", ["a.mp4"])])
        plan["shots"] = "oops"
        result = jy_roughcut.validate_plan(plan, scan, grouping)
        self.assertFalse(result["ok"])
        shot = make_shot("01", ["a.mp4"])
        shot["transition_out"] = "hard-cut"
        result = jy_roughcut.validate_plan(make_plan([shot]), scan, grouping)
        self.assertFalse(result["ok"])
        plan = make_plan([make_shot("01", ["a.mp4"])])
        plan["canvas"] = "9:16"
        result = jy_roughcut.validate_plan(plan, scan, grouping)
        self.assertFalse(result["ok"])
        plan = make_plan([make_shot("01", ["a.mp4"])])
        plan["canvas"] = {"width": -1, "height": 1920, "ratio": "9:16", "fps": 30}
        result = jy_roughcut.validate_plan(plan, scan, grouping)
        self.assertFalse(result["ok"])

    def test_file_existence_check_reports_missing_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scan = make_scan("a.mp4", root=str(root))
            grouping = make_grouping(
                shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
                root=str(root),
            )
            plan = make_plan([make_shot("01", ["a.mp4"])], source_root=str(root))
            result = jy_roughcut.validate_plan(plan, scan, grouping, check_files=True)
            self.assertFalse(result["ok"])
            self.assertTrue(any("a.mp4" in e for e in result["errors"]))
            (root / "a.mp4").write_bytes(b"x")
            result = jy_roughcut.validate_plan(plan, scan, grouping, check_files=True)
            self.assertTrue(result["ok"], result)

    def test_invalid_source_audio_is_an_error(self):
        scan = make_scan("a.mp4")
        grouping = grouping_for({"01": "a.mp4"})
        plan = make_plan([make_shot("01", ["a.mp4"])], extra={"source_audio": "drop"})
        result = jy_roughcut.validate_plan(plan, scan, grouping)
        self.assertFalse(result["ok"])
        self.assertTrue(any("source_audio" in e for e in result["errors"]))


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


class ReportTests(unittest.TestCase):
    def test_report_lists_unresolved_and_low_confidence(self):
        grouping = grouping_for(
            {"07": "a.mp4"},
            unresolved=[{"file": "unresolved.mp4", "reason": "无法判断"}],
        )
        shot = make_shot("07", ["a.mp4"], confidence=0.5, review=["[低置信] 两条 take 差异小"])
        text = jy_roughcut.report(make_plan([shot]), grouping)
        self.assertIn("unresolved.mp4", text)
        self.assertIn("07", text)
        self.assertIn("待确认", text)

    def test_report_includes_timeline_total(self):
        plan = make_plan(
            [
                make_shot("01", ["a.mp4"], durations=[2.0]),
                make_shot("02", ["b.mp4"], durations=[3.0]),
            ]
        )
        text = jy_roughcut.report(plan)
        self.assertIn("5.00", text)

    def test_report_lists_alternates_with_notes(self):
        grouping = make_grouping(
            shots=[
                {
                    "shot": "01",
                    "takes": [
                        {"take": "01", "kind": "single", "files": ["a.mp4"]},
                        {"take": "02", "kind": "single", "files": ["b.mp4"]},
                    ],
                }
            ]
        )
        shot = make_shot("01", ["a.mp4"], alternates=[{"file": "b.mp4", "note": "中段崩坏"}])
        text = jy_roughcut.report(make_plan([shot]), grouping)
        self.assertIn("备选", text)
        self.assertIn("b.mp4", text)
        self.assertIn("中段崩坏", text)

    def test_report_declares_unverified_script_without_script_ref(self):
        shot = make_shot("01", ["a.mp4"])
        text = jy_roughcut.report(make_plan([shot]))
        self.assertIn("未对剧本验证", text)
        shot["script_ref"] = "第1镜：开场"
        text = jy_roughcut.report(make_plan([shot]))
        self.assertNotIn("未对剧本验证", text)

    def test_report_prints_plan_warnings(self):
        plan = make_plan([make_shot("01", ["a.mp4"])])
        plan["warnings"] = ["闪黑转场只标注不执行"]
        text = jy_roughcut.report(plan)
        self.assertIn("闪黑转场只标注不执行", text)


class FramesTests(unittest.TestCase):
    def test_frame_times_are_inside_the_clip(self):
        self.assertEqual(jy_roughcut.frame_times(10.0, 3), [1.0, 5.0, 9.0])
        self.assertEqual(jy_roughcut.frame_times(8.0, 1), [4.0])

    def test_sheets_one_per_shot_with_injected_grab(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4", "b.mp4", "c.mp4")
            grouping = make_grouping(
                shots=[
                    {"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]},
                    {
                        "shot": "02",
                        "takes": [{"take": "01", "kind": "segmented", "files": ["b.mp4", "c.mp4"]}],
                    },
                ]
            )

            def grab(path, t):
                return Image.new("RGB", (120, 68), (30, 30, 30))

            result = jy_roughcut.frames(grouping, scan, out, count=2, grab=grab)
            self.assertEqual(result["shots"], 2)
            self.assertEqual(len(result["sheets"]), 2)
            self.assertTrue(all("shot-" in Path(p).name for p in result["sheets"]))
            with Image.open(result["sheets"][0]) as img:
                self.assertGreater(img.width, 0)

    def test_contact_sheet_keeps_at_most_twelve_cells(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4", "b.mp4", "c.mp4")
            grouping = make_grouping(
                shots=[
                    {
                        "shot": "05",
                        "takes": [
                            {"take": "01", "kind": "single", "files": ["a.mp4"]},
                            {"take": "02", "kind": "single", "files": ["b.mp4"]},
                            {"take": "03", "kind": "single", "files": ["c.mp4"]},
                        ],
                    }
                ]
            )

            def grab(path, t):
                return Image.new("RGB", (120, 68), (30, 30, 30))

            result = jy_roughcut.frames(grouping, scan, out, count=5, grab=grab)
            self.assertEqual(result["cells"], [12])
            self.assertEqual(len(result["sheets"]), 1)

    def test_contact_sheet_splits_when_files_exceed_capacity(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            names = [f"{i:02d}.mp4" for i in range(1, 14)]
            scan = make_scan(*names)
            grouping = make_grouping(
                shots=[
                    {
                        "shot": "01",
                        "takes": [
                            {"take": f"{i:02d}", "kind": "single", "files": [name]}
                            for i, name in enumerate(names, start=1)
                        ],
                    }
                ]
            )

            def grab(path, t):
                return Image.new("RGB", (120, 68), (30, 30, 30))

            result = jy_roughcut.frames(grouping, scan, out, count=5, grab=grab)
            self.assertEqual(len(result["sheets"]), 2)
            self.assertEqual(result["cells"], [12, 1])
            self.assertTrue(Path(result["sheets"][0]).name.startswith("shot-01-1"))


class BuildTests(unittest.TestCase):
    def test_build_refuses_incomplete_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4", "b.mp4")
            grouping = grouping_for({"01": "a.mp4"})  # b.mp4 未被覆盖
            plan = make_plan([make_shot("01", ["a.mp4"])])
            with self.assertRaises(jy_roughcut.RoughcutError):
                jy_roughcut.build(
                    plan, scan, grouping, out,
                    check_files=False,
                    run_builder=lambda args: {},
                    run_transition=lambda args: {},
                )

    def test_build_resolves_paths_against_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "第3集"
            source.mkdir()
            (source / "a.mp4").write_bytes(b"x")
            (source / "ep03.srt").write_text("1\n", encoding="utf-8")
            out = source / "roughcut"
            scan = make_scan("a.mp4", root=str(source))
            grouping = make_grouping(
                shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
                root=str(source),
            )
            plan = make_plan(
                [make_shot("01", ["a.mp4"])],
                source_root=str(source),
                extra={"subtitles": {"srt": "ep03.srt"}},
            )
            jy_roughcut.build(
                plan, scan, grouping, out,
                run_builder=lambda args: {},
                run_transition=lambda args: {},
            )
            shotlist = json.loads((out / "shotlist.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(shotlist["shots"][0]["file"]), (source / "a.mp4").resolve())
            self.assertEqual(Path(shotlist["subtitles"]["srt"]), (source / "ep03.srt").resolve())

    def test_build_dry_run_writes_nothing_and_calls_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4")
            grouping = grouping_for({"01": "a.mp4"})
            plan = make_plan([make_shot("01", ["a.mp4"])])
            calls = []
            result = jy_roughcut.build(
                plan, scan, grouping, out,
                dry_run=True,
                check_files=False,
                run_builder=lambda args: calls.append(("builder", list(args))) or {},
                run_transition=lambda args: calls.append(("transition", list(args))) or {},
            )
            self.assertEqual(calls, [])
            self.assertFalse(out.exists())
            self.assertEqual(result["transitions_planned"], 0)

    def test_build_applies_dissolve_on_the_previous_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4", "b.mp4")
            grouping = grouping_for({"01": "a.mp4", "02": "b.mp4"})
            plan = make_plan(
                [
                    make_shot("01", ["a.mp4"], transition={"type": "dissolve", "duration": 0.5}),
                    make_shot("02", ["b.mp4"]),
                ]
            )
            calls = []
            result = jy_roughcut.build(
                plan, scan, grouping, out,
                check_files=False,
                run_builder=lambda args: calls.append(("builder", list(args))) or {"draft": "第3集-粗剪"},
                run_transition=lambda args: calls.append(("transition", list(args))) or {},
            )
            kinds = [c[0] for c in calls]
            self.assertIn("builder", kinds)
            builder_args = next(args for kind, args in calls if kind == "builder")
            self.assertIn("--shotlist", builder_args)
            trans = next(args for kind, args in calls if kind == "transition")
            self.assertEqual(trans[:2], ["transition", "--draft"])
            index = trans.index("--segment")
            self.assertEqual(trans[index + 1], "0")
            self.assertIn("叠化", trans)
            self.assertEqual(result["transitions_applied"], 1)
            self.assertTrue((out / "shotlist.json").is_file())

    def test_build_mute_adds_edit_segment_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4")
            grouping = grouping_for({"01": "a.mp4"})
            plan = make_plan([make_shot("01", ["a.mp4"])], extra={"source_audio": "mute"})
            calls = []
            result = jy_roughcut.build(
                plan, scan, grouping, out,
                check_files=False,
                run_builder=lambda args: {},
                run_transition=lambda args: calls.append(list(args)) or {},
            )
            self.assertEqual(result["muted_segments"], 1)
            edit = next(args for args in calls if args and args[0] == "edit-segment")
            self.assertIn("--mute", edit)

    def test_build_refuses_missing_media_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4")
            grouping = grouping_for({"01": "a.mp4"})
            plan = make_plan([make_shot("01", ["a.mp4"])])
            with self.assertRaises(jy_roughcut.RoughcutError):
                jy_roughcut.build(
                    plan, scan, grouping, out,
                    run_builder=lambda args: {},
                    run_transition=lambda args: {},
                )
            self.assertFalse(out.exists())

    def test_build_refuses_existing_draft_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4")
            grouping = grouping_for({"01": "a.mp4"})
            plan = make_plan([make_shot("01", ["a.mp4"])])
            with self.assertRaisesRegex(jy_roughcut.RoughcutError, "草稿已存在"):
                jy_roughcut.build(
                    plan, scan, grouping, out,
                    check_files=False,
                    draft_exists=lambda name: True,
                    run_builder=lambda args: {},
                    run_transition=lambda args: {},
                )
            self.assertFalse(out.exists())

    def test_build_existing_flag_requires_existing_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            scan = make_scan("a.mp4")
            grouping = grouping_for({"01": "a.mp4"})
            plan = make_plan([make_shot("01", ["a.mp4"])])
            with self.assertRaises(jy_roughcut.RoughcutError):
                jy_roughcut.build(
                    plan, scan, grouping, out,
                    existing=True,
                    check_files=False,
                    draft_exists=lambda name: False,
                    run_builder=lambda args: {},
                    run_transition=lambda args: {},
                )
            self.assertFalse(out.exists())

    def test_cli_validate_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "第3集"
            source.mkdir()
            (source / "a.mp4").write_bytes(b"x")
            out = source / "roughcut"
            out.mkdir(parents=True)
            scan = make_scan("a.mp4", root=str(source))
            grouping = make_grouping(
                shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
                root=str(source),
            )
            good = make_plan([make_shot("01", ["a.mp4"])], source_root=str(source))
            bad = make_plan([make_shot("01", ["a.mp4"], durations=[5.0])], source_root=str(source))
            (out / "scan.json").write_text(json.dumps(scan), encoding="utf-8")
            (out / "grouping.json").write_text(json.dumps(grouping), encoding="utf-8")
            (out / "roughcut-good.json").write_text(json.dumps(good), encoding="utf-8")
            (out / "roughcut-bad.json").write_text(json.dumps(bad), encoding="utf-8")
            cli = str(ROOT / "tools" / "roughcut.py")

            def run(plan_path):
                return subprocess.run(
                    [sys.executable, cli, "validate", "--grouping", str(out / "grouping.json"),
                     "--plan", plan_path, "--out", str(out)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )

            self.assertEqual(run(str(out / "roughcut-good.json")).returncode, 0)
            self.assertEqual(run(str(out / "roughcut-bad.json")).returncode, 1)
            self.assertEqual(run(str(out / "missing.json")).returncode, 2)

    def test_cli_structure_error_returns_validation_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            scan = make_scan("a.mp4")
            grouping = grouping_for({"01": "a.mp4"})
            plan = make_plan([make_shot("01", ["a.mp4"])])
            plan["shots"] = "oops"
            plan.pop("source_root")
            (out / "scan.json").write_text(json.dumps(scan), encoding="utf-8")
            (out / "grouping.json").write_text(json.dumps(grouping), encoding="utf-8")
            plan_path = out / "roughcut.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "roughcut.py"),
                    "validate",
                    "--grouping",
                    str(out / "grouping.json"),
                    "--plan",
                    str(plan_path),
                ],
                capture_output=True,
            )
            self.assertEqual(
                proc.returncode,
                1,
                proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace"),
            )


class CliTests(unittest.TestCase):
    def test_cli_help_is_utf8_on_windows_console(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "roughcut.py"), "--help"],
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("智能粗剪", proc.stdout.decode("utf-8"))

    def test_cli_validate_reads_bom_json_under_chinese_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "第3集"
            source.mkdir()
            (source / "03-05-01.mp4").write_bytes(b"x")
            out = source / "roughcut"
            out.mkdir()
            scan = make_scan("03-05-01.mp4", root=str(source))
            grouping = make_grouping(
                shots=[{"shot": "05", "takes": [{"take": "01", "kind": "single", "files": ["03-05-01.mp4"]}]}],
                root=str(source),
            )
            plan = make_plan([make_shot("05", ["03-05-01.mp4"])], source_root=str(source))
            for name, data in (("scan.json", scan), ("grouping.json", grouping), ("roughcut.json", plan)):
                (out / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "roughcut.py"),
                    "validate",
                    "--grouping",
                    str(out / "grouping.json"),
                    "--plan",
                    str(out / "roughcut.json"),
                ],
                capture_output=True,
            )
            stdout = proc.stdout.decode("utf-8", "replace")
            self.assertEqual(proc.returncode, 0, stdout + proc.stderr.decode("utf-8", "replace"))
            self.assertIn('"ok": true', stdout)

    def test_cli_report_loads_grouping_next_to_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            scan = make_scan("a.mp4", "b.mp4")
            grouping = make_grouping(
                shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
                unresolved=[{"file": "b.mp4", "reason": "无法判断镜号"}],
            )
            plan = make_plan([make_shot("01", ["a.mp4"])])
            plan["warnings"] = ["叠化建议未执行"]
            (out / "scan.json").write_text(json.dumps(scan), encoding="utf-8")
            (out / "grouping.json").write_text(json.dumps(grouping, ensure_ascii=False), encoding="utf-8")
            plan_path = out / "roughcut.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "roughcut.py"),
                    "report",
                    "--plan",
                    str(plan_path),
                    "--out",
                    str(out),
                ],
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0)
            text = (out / "roughcut.md").read_text(encoding="utf-8")
            self.assertIn("b.mp4", text)
            self.assertIn("叠化建议未执行", text)
            self.assertIn("未对剧本验证", text)


class McpTests(unittest.TestCase):
    def test_build_server_registers_six_tools(self):
        sys.path.insert(0, str(ROOT))
        import mcp_ext.server as ext

        tools = asyncio.run(ext.build_server().list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {
                "list_template_slots",
                "duplicate_local_draft",
                "import_local_track",
                "scan_roughcut_folder",
                "validate_roughcut_plan",
                "build_roughcut_draft",
            },
        )

    def test_validate_tool_reports_errors_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roughcut"
            out.mkdir(parents=True)
            (out / "scan.json").write_text(json.dumps(make_scan("a.mp4")), encoding="utf-8")
            (out / "grouping.json").write_text(json.dumps(grouping_for({"01": "a.mp4"})), encoding="utf-8")
            plan_path = out / "roughcut.json"
            plan_path.write_text(
                json.dumps(make_plan([make_shot("01", ["a.mp4"], durations=[5.0])])),
                encoding="utf-8",
            )
            sys.path.insert(0, str(ROOT))
            import mcp_ext.server as ext

            result = ext.validate_roughcut_plan(str(plan_path))
            self.assertFalse(result["ok"])
            self.assertTrue(any("超出" in e for e in result["errors"]))

    def test_validate_tool_reads_bom_files_with_real_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "第3集"
            source.mkdir()
            (source / "a.mp4").write_bytes(b"x")
            scan = make_scan("a.mp4", root=str(source))
            grouping = make_grouping(
                shots=[{"shot": "01", "takes": [{"take": "01", "kind": "single", "files": ["a.mp4"]}]}],
                root=str(source),
            )
            plan = make_plan([make_shot("01", ["a.mp4"])], source_root=str(source))
            (source / "scan.json").write_text(json.dumps(scan, ensure_ascii=False), encoding="utf-8-sig")
            (source / "grouping.json").write_text(
                json.dumps(grouping, ensure_ascii=False), encoding="utf-8-sig"
            )
            plan_path = source / "roughcut.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8-sig")
            sys.path.insert(0, str(ROOT))
            import mcp_ext.server as ext

            result = ext.validate_roughcut_plan(str(plan_path))
            self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
