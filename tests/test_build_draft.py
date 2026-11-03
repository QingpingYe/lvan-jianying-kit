"""build_draft.py 的单元测试（不依赖剪映与真实草稿）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import build_draft  # noqa: E402


class VenvPythonTests(unittest.TestCase):
    def test_explicit_python_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "python.exe"
            exe.write_bytes(b"")
            self.assertEqual(build_draft.venv_python(str(exe)), exe)

    def test_runtime_json_python_is_used_in_plugin_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runtime_home = tmp / "runtime"
            runtime_home.mkdir()
            exe = tmp / "venv" / "Scripts" / "python.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            (runtime_home / "runtime.json").write_text(
                json.dumps({"python": str(exe), "plugin_mode": True}), encoding="utf-8"
            )
            plugin_root = tmp / "plugin"
            plugin_root.mkdir()
            self.assertEqual(
                build_draft.venv_python(
                    plugin_root=plugin_root,
                    env={"JIANYING_KIT_HOME": str(runtime_home)},
                ),
                exe,
            )

    def test_plugin_venv_is_the_last_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            plugin_root = tmp / "plugin"
            exe = plugin_root / ".venv" / "Scripts" / "python.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            self.assertEqual(
                build_draft.venv_python(
                    plugin_root=plugin_root,
                    home=tmp / "home",
                    env={},
                ),
                exe,
            )

    def test_missing_runtime_returns_none_instead_of_a_bad_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self.assertIsNone(
                build_draft.venv_python(
                    plugin_root=tmp / "plugin",
                    home=tmp / "home",
                    env={},
                )
            )

    def test_main_reports_a_clear_error_when_python_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            shotlist = Path(tmp) / "shotlist.json"
            shotlist.write_text(
                json.dumps(
                    {
                        "draft_name": "第3集-粗剪",
                        "canvas": {"width": 1080, "height": 1920, "fps": 30},
                        "shots": [{"file": "missing.mp4", "duration": 1.0}],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(build_draft, "venv_python", return_value=None):
                with self.assertRaisesRegex(SystemExit, "找不到可用的 Python 环境"):
                    build_draft.main(["--shotlist", str(shotlist), "--dry-run"])


class RuntimeEnvTests(unittest.TestCase):
    def test_runtime_json_nested_jianying_paths_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".jianying-kit"
            home.mkdir()
            (home / "runtime.json").write_text(
                json.dumps(
                    {
                        "python": "C:\\venv\\python.exe",
                        "jianying_paths": {
                            "JIANYING_INSTALL_ROOT": "D:\\JianyingPro",
                            "JY_INSTALL_DIR": "D:\\JianyingPro\\11.6.6",
                            "JIANYING_DRAFT_ROOT": "D:\\JianyingPro Drafts",
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = build_draft.load_jianying_env(env={"JIANYING_KIT_HOME": str(home)})
            self.assertEqual(loaded["JIANYING_INSTALL_ROOT"], "D:\\JianyingPro")
            self.assertEqual(loaded["JIANYING_DRAFT_ROOT"], "D:\\JianyingPro Drafts")

    def test_flat_jianying_paths_file_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "jianying-paths.json").write_text(
                json.dumps({"JIANYING_DRAFT_ROOT": "D:\\Flat"}), encoding="utf-8"
            )
            (home / "runtime.json").write_text(
                json.dumps({"jianying_paths": {"JIANYING_DRAFT_ROOT": "D:\\Nested"}}),
                encoding="utf-8",
            )
            loaded = build_draft.load_jianying_env(env={"JIANYING_KIT_HOME": str(home)})
            self.assertEqual(loaded["JIANYING_DRAFT_ROOT"], "D:\\Flat")


class MainBuildTests(unittest.TestCase):
    class RecordingBuilder:
        calls: list[tuple] = []

        def __init__(self, python, root, dry_run):
            self.python = python
            self.root = root
            self.dry_run = dry_run

        def run(self, *args, raw=False):
            self.calls.append((args, raw))
            if args and args[0] == "probe":
                return {"draft_root": self.root or ""}
            return {}

    def _shotlist(self, tmp: Path, first: dict) -> Path:
        media = tmp / "a.mp4"
        media.write_bytes(b"x")
        path = tmp / "shotlist.json"
        path.write_text(
            json.dumps(
                {
                    "draft_name": "第3集-粗剪",
                    "canvas": {"width": 1080, "height": 1920, "ratio": "9:16", "fps": 24},
                    "shots": [{"file": "a.mp4", **first}],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_create_applies_the_first_shot_source_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shotlist = self._shotlist(tmp, {"source_start": 0.3, "duration": 2.0})
            calls: list[tuple] = []

            class FakeBuilder(self.RecordingBuilder):
                def run(self, *args, raw=False):
                    calls.append((args, raw))
                    if args and args[0] == "probe":
                        return {"draft_root": str(tmp / "drafts")}
                    return {}

            with mock.patch.object(build_draft, "Builder", FakeBuilder):
                rc = build_draft.main(
                    ["--shotlist", str(shotlist), "--root", str(tmp / "drafts"),
                     "--python", sys.executable]
                )
            self.assertEqual(rc, 0)
            commands = [args for args, _ in calls]
            self.assertEqual(commands[1][0], "create")
            edit = commands[2]
            self.assertEqual(edit[0], "edit-segment")
            self.assertIn("--source-start", edit)
            self.assertEqual(edit[edit.index("--source-start") + 1], "0.3")
            self.assertEqual(edit[edit.index("--segment") + 1], "0")

    def test_create_skips_the_edit_when_source_start_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shotlist = self._shotlist(tmp, {"source_start": 0, "duration": 2.0})
            calls: list[tuple] = []

            class FakeBuilder(self.RecordingBuilder):
                def run(self, *args, raw=False):
                    calls.append((args, raw))
                    if args and args[0] == "probe":
                        return {"draft_root": str(tmp / "drafts")}
                    return {}

            with mock.patch.object(build_draft, "Builder", FakeBuilder):
                build_draft.main(
                    ["--shotlist", str(shotlist), "--root", str(tmp / "drafts"),
                     "--python", sys.executable]
                )
            commands = [args for args, _ in calls]
            self.assertNotIn("edit-segment", [command[0] for command in commands])

    def test_existing_non_empty_draft_is_refused_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shotlist = self._shotlist(tmp, {"source_start": 0.0, "duration": 2.0})
            drafts = tmp / "drafts"
            (drafts / "第3集-粗剪").mkdir(parents=True)

            class FakeBuilder(self.RecordingBuilder):
                def run(self, *args, raw=False):
                    if args and args[0] == "probe":
                        return {"draft_root": str(drafts)}
                    return {}

            with mock.patch.object(build_draft, "Builder", FakeBuilder), \
                    mock.patch.object(build_draft, "existing_video_segment_count", return_value=3):
                with self.assertRaisesRegex(SystemExit, "已有 3 个视频片段"):
                    build_draft.main(
                        ["--shotlist", str(shotlist), "--root", str(drafts), "--existing",
                         "--python", sys.executable]
                    )


if __name__ == "__main__":
    unittest.main()
