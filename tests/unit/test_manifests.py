from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.project_intake.manifests import detect_manifests


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> None:
        (self.root / name).write_text(text, encoding="utf-8")

    def test_no_manifests_found_in_empty_directory(self) -> None:
        self.assertEqual(detect_manifests(self.root), [])

    def test_reads_pep621_pyproject(self) -> None:
        self.write(
            "pyproject.toml",
            '[project]\nname = "demo"\nversion = "1.2.3"\n'
            'dependencies = ["requests>=2.0", "rich"]\n',
        )
        manifest = detect_manifests(self.root)[0]
        self.assertEqual(manifest.ecosystem, "python")
        self.assertEqual(manifest.project_name, "demo")
        self.assertEqual(manifest.version, "1.2.3")
        self.assertEqual(sorted(manifest.dependencies), ["requests", "rich"])

    def test_reads_poetry_pyproject(self) -> None:
        self.write(
            "pyproject.toml",
            '[tool.poetry]\nname = "poet"\nversion = "0.1.0"\n'
            '[tool.poetry.dependencies]\npython = "^3.11"\nhttpx = "^0.27"\n',
        )
        manifest = detect_manifests(self.root)[0]
        self.assertEqual(manifest.project_name, "poet")
        self.assertEqual(manifest.dependencies, ["httpx"])

    def test_reads_requirements_txt_ignoring_comments_and_flags(self) -> None:
        self.write("requirements.txt", "# comment\n\n-r other.txt\nflask==3.0\nnumpy\n")
        manifest = detect_manifests(self.root)[0]
        self.assertEqual(manifest.dependencies, ["flask", "numpy"])

    def test_reads_package_json(self) -> None:
        self.write(
            "package.json",
            '{"name": "web", "version": "2.0.0", "dependencies": {"zod": "^3"}}',
        )
        manifest = detect_manifests(self.root)[0]
        self.assertEqual(manifest.ecosystem, "node")
        self.assertEqual(manifest.project_name, "web")
        self.assertEqual(manifest.dependencies, ["zod"])

    def test_reads_go_mod(self) -> None:
        self.write("go.mod", "module example.com/app\n\nrequire github.com/x/y v1.0.0\n")
        manifest = detect_manifests(self.root)[0]
        self.assertEqual(manifest.project_name, "example.com/app")
        self.assertEqual(manifest.dependencies, ["github.com/x/y"])

    def test_reads_cargo_toml(self) -> None:
        self.write(
            "Cargo.toml",
            '[package]\nname = "crate"\nversion = "0.1.0"\n[dependencies]\nserde = "1"\n',
        )
        manifest = detect_manifests(self.root)[0]
        self.assertEqual(manifest.project_name, "crate")
        self.assertEqual(manifest.dependencies, ["serde"])

    def test_broken_manifest_is_reported_not_guessed(self) -> None:
        self.write("package.json", "{ this is not json")
        bag = DiagnosticBag()
        manifest = detect_manifests(self.root, bag)[0]
        self.assertFalse(manifest.parsed)
        self.assertEqual([d.code for d in bag], ["CW0201"])

    def test_manifest_without_a_reader_is_still_detected(self) -> None:
        self.write("Gemfile", "source 'https://rubygems.org'\n")
        manifest = detect_manifests(self.root)[0]
        self.assertEqual(manifest.ecosystem, "ruby")
        self.assertEqual(manifest.dependencies, [])

    def test_detection_order_is_stable(self) -> None:
        self.write("package.json", "{}")
        self.write("go.mod", "module m\n")
        paths = [m.path for m in detect_manifests(self.root)]
        self.assertEqual(paths, sorted(paths))


if __name__ == "__main__":
    unittest.main()
