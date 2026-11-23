"""Shared helpers for tests of the native (C, C++, Java) backends."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.frontends.python import parse_source
from codeweaver.graph.project_index import ProjectIndex
from codeweaver.language_registry import registry
from codeweaver.transforms import default_pipeline, project_pipeline
from codeweaver.transforms.inference import inference_order

FIXTURES = Path(__file__).parent / "fixtures"
NATIVE_PROJECT = FIXTURES / "native_project"


def translate(sources: dict[str, str], target: str) -> tuple[dict[str, str], DiagnosticBag]:
    """Run the in-memory pipeline for *target*; return generated files by path."""
    import codeweaver.adapters  # noqa: F401 - populates the registry

    backend = registry.backends[target]
    modules = []
    for path, text in sources.items():
        parsed = parse_source(text, path)
        assert parsed.module is not None, [item.format() for item in parsed.diagnostics]
        modules.append(default_pipeline(target).run(parsed.module).module)
    index = ProjectIndex.build(modules)
    finished = {
        module.name: project_pipeline(target, index).run(module).module
        for module in inference_order(modules)
    }
    modules = [finished[module.name] for module in modules]
    index = ProjectIndex.build(modules)
    layout = backend.create_layout(modules, index)
    bag = DiagnosticBag()
    files: dict[str, str] = {}
    for module in modules:
        for generated in backend.generate_files(module, layout, diagnostics=bag):
            files[generated.path] = generated.contents
    files.update(backend.project_files("test", layout))
    return files, bag


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, contents in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")


def tool(name: str) -> str | None:
    return shutil.which(name)


def run(argv: list[str], cwd: Path, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def python_output(project: Path, module: str) -> str:
    import sys

    completed = run([sys.executable, "-m", module], project)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout
