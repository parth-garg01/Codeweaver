from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from codeweaver.validation.toolchain import detect_toolchain, detect_toolchains


class ToolchainDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bin_dir = Path(self._tmp.name) / "bin"
        self.bin_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_tool(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_missing_tool_is_reported_not_raised(self) -> None:
        tool = detect_toolchain("definitely-not-a-real-tool", search_path=str(self.bin_dir))
        self.assertFalse(tool.available)
        self.assertEqual(tool.error, "not found on PATH")
        self.assertEqual(tool.path, "")

    def test_found_tool_reports_its_version(self) -> None:
        self.make_tool("faketsc", "print('Version 5.8.2')\n")
        tool = detect_toolchain("faketsc", search_path=str(self.bin_dir))
        self.assertTrue(tool.available)
        self.assertEqual(tool.version, "Version 5.8.2")
        self.assertTrue(tool.path.endswith("faketsc"))

    def test_only_the_first_output_line_is_kept(self) -> None:
        self.make_tool("noisy", "print('1.0.0')\nprint('extra noise')\n")
        self.assertEqual(detect_toolchain("noisy", search_path=str(self.bin_dir)).version, "1.0.0")

    def test_version_from_stderr_is_accepted(self) -> None:
        self.make_tool("onstderr", "import sys; sys.stderr.write('2.0.0\\n')\n")
        self.assertEqual(
            detect_toolchain("onstderr", search_path=str(self.bin_dir)).version, "2.0.0"
        )

    def test_failing_probe_is_reported_as_unavailable(self) -> None:
        self.make_tool("broken", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)\n")
        tool = detect_toolchain("broken", search_path=str(self.bin_dir))
        self.assertFalse(tool.available)
        self.assertIn("exited 3", tool.error)
        self.assertIn("boom", tool.error)

    def test_project_local_bin_is_preferred(self) -> None:
        project = Path(self._tmp.name) / "project"
        local = project / "node_modules" / ".bin"
        local.mkdir(parents=True)
        local_tool = local / "tsc"
        local_tool.write_text(f"#!{sys.executable}\nprint('local 1.0')\n", encoding="utf-8")
        local_tool.chmod(0o755)
        self.make_tool("tsc", "print('global 9.9')\n")
        index = detect_toolchains(("tsc",), project_root=project, search_path=str(self.bin_dir))
        self.assertEqual(index.get("tsc").version, "local 1.0")

    def test_non_executable_local_candidate_is_ignored(self) -> None:
        project = Path(self._tmp.name) / "project2"
        local = project / "node_modules" / ".bin"
        local.mkdir(parents=True)
        (local / "tsc").write_text("not executable", encoding="utf-8")
        (local / "tsc").chmod(0o644)
        self.make_tool("tsc", "print('global 9.9')\n")
        index = detect_toolchains(("tsc",), project_root=project, search_path=str(self.bin_dir))
        self.assertEqual(index.get("tsc").version, "global 9.9")


class ToolchainIndexTests(unittest.TestCase):
    def test_index_reports_missing_tools(self) -> None:
        index = detect_toolchains(("definitely-not-real",), search_path=os.devnull)
        self.assertFalse(index.available("definitely-not-real"))
        self.assertEqual(index.missing(("definitely-not-real",)), ["definitely-not-real"])
        self.assertEqual(index.versions(), {})

    def test_unknown_tool_lookup_returns_none(self) -> None:
        self.assertIsNone(detect_toolchains(()).get("tsc"))

    def test_to_dict_is_sorted_and_json_friendly(self) -> None:
        import json

        index = detect_toolchains(("b-tool", "a-tool"), search_path=os.devnull)
        payload = index.to_dict()
        self.assertEqual([item["name"] for item in payload["tools"]], ["a-tool", "b-tool"])
        json.dumps(payload)


if __name__ == "__main__":
    unittest.main()
