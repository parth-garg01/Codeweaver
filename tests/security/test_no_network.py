"""An audit: the core workflows must not touch the network.

"Local-first" is a claim the product makes on every screen, so it gets a test
rather than a promise. Sockets are replaced with a factory that raises, and the
whole pipeline is then run against a real fixture: scan, parse, IR validation,
graph, plan, transforms, code generation, report rendering and persistence.

The one permitted exception is the loopback interface, because the UI server
binds to it. Any attempt to reach a non-loopback address fails the audit, as
does any DNS lookup.
"""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CLEAN = FIXTURES / "sample_project"


class NetworkAttempt(AssertionError):
    """Raised when audited code tries to reach the network."""


_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", ""})


class _AuditedSocket(socket.socket):
    """A socket that refuses to connect anywhere but the loopback interface."""

    def connect(self, address):  # type: ignore[override]
        _check(address)
        return super().connect(address)

    def connect_ex(self, address):  # type: ignore[override]
        _check(address)
        return super().connect_ex(address)

    def sendto(self, *args, **kwargs):  # type: ignore[override]
        if len(args) > 1:
            _check(args[-1])
        return super().sendto(*args, **kwargs)


def _check(address) -> None:
    host = address[0] if isinstance(address, tuple) else address
    if str(host) not in _LOOPBACK:
        raise NetworkAttempt(f"attempted a network connection to {host!r}")


class NoNetworkAudit(unittest.TestCase):
    """Run the pipeline with the network cut off."""

    def setUp(self) -> None:
        self._real_socket = socket.socket
        self._real_getaddrinfo = socket.getaddrinfo
        self._real_create_connection = socket.create_connection

        def refuse_lookup(host, *args, **kwargs):
            if str(host) not in _LOOPBACK:
                raise NetworkAttempt(f"attempted a DNS lookup for {host!r}")
            return self._real_getaddrinfo(host, *args, **kwargs)

        def refuse_connection(address, *args, **kwargs):
            _check(address)
            return self._real_create_connection(address, *args, **kwargs)

        socket.socket = _AuditedSocket  # type: ignore[misc]
        socket.getaddrinfo = refuse_lookup  # type: ignore[assignment]
        socket.create_connection = refuse_connection  # type: ignore[assignment]

    def tearDown(self) -> None:
        socket.socket = self._real_socket  # type: ignore[misc]
        socket.getaddrinfo = self._real_getaddrinfo  # type: ignore[assignment]
        socket.create_connection = self._real_create_connection  # type: ignore[assignment]

    def test_the_audit_itself_detects_a_connection_attempt(self) -> None:
        with self.assertRaises(NetworkAttempt):
            socket.create_connection(("example.com", 80), timeout=1)

    def test_the_audit_detects_a_dns_lookup(self) -> None:
        with self.assertRaises(NetworkAttempt):
            socket.getaddrinfo("example.com", 80)

    def test_scan_makes_no_network_call(self) -> None:
        from codeweaver.project_intake.scanner import scan_project

        self.assertEqual(len(scan_project(CLEAN).files), 5)

    def test_analyse_and_plan_make_no_network_call(self) -> None:
        from codeweaver.pipeline import analyse_project, plan_migration

        analysis = analyse_project(CLEAN)
        plan = plan_migration(analysis)
        self.assertEqual(analysis.counts.parsed, 5)
        self.assertTrue(plan.module_order)

    def test_full_migration_without_validation_makes_no_network_call(self) -> None:
        from codeweaver.pipeline import MigrationOptions, run_migration

        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_migration(
                CLEAN, Path(tmp) / "out", MigrationOptions(run_validation=False)
            )
            self.assertTrue(outcome.written_files)

    def test_validation_makes_no_network_call(self) -> None:
        from codeweaver.pipeline import MigrationOptions, run_migration

        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_migration(CLEAN, Path(tmp) / "out", MigrationOptions())
            self.assertTrue(outcome.report.validation.checks)

    def test_report_rendering_makes_no_network_call(self) -> None:
        from codeweaver.pipeline import MigrationOptions, run_migration
        from codeweaver.reporting import render_json, render_markdown, render_text

        with tempfile.TemporaryDirectory() as tmp:
            report = run_migration(
                CLEAN, Path(tmp) / "out", MigrationOptions(run_validation=False)
            ).report
            for rendered in (render_text(report), render_markdown(report), render_json(report)):
                self.assertTrue(rendered)

    def test_persistence_makes_no_network_call(self) -> None:
        from codeweaver.persistence import Checkpoint, CheckpointStore

        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(Path(tmp) / ".codeweaver")
            store.save(Checkpoint(project_root=tmp, stage="analyze"))
            self.assertIsNotNone(store.load())

    def test_the_cli_makes_no_network_call(self) -> None:
        import io
        from contextlib import redirect_stdout

        from codeweaver.cli import main

        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["languages"]), 0)
            self.assertEqual(main(["scan", str(CLEAN)]), 0)
            self.assertEqual(main(["plan", str(CLEAN)]), 0)


class NoAiDependencyAudit(unittest.TestCase):
    """The runtime must not depend on a model provider or an HTTP client."""

    FORBIDDEN = (
        "anthropic",
        "openai",
        "langchain",
        "llama",
        "transformers",
        "torch",
        "tensorflow",
        "sentence_transformers",
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
    )

    def test_declared_runtime_dependencies_are_empty(self) -> None:
        import tomllib

        root = Path(__file__).resolve().parents[2]
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["dependencies"], [])

    def test_no_runtime_module_imports_a_model_provider_or_http_client(self) -> None:
        import ast

        root = Path(__file__).resolve().parents[2] / "src"
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    top = name.split(".")[0]
                    if top in self.FORBIDDEN:
                        offenders.append(f"{path.relative_to(root)}: {name}")
        self.assertEqual(offenders, [])

    def test_only_the_ui_server_imports_a_network_module(self) -> None:
        import ast

        root = Path(__file__).resolve().parents[2] / "src"
        network_modules = {"socket", "http", "urllib", "ssl", "ftplib", "smtplib"}
        importers: set[str] = set()
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any(name.split(".")[0] in network_modules for name in names):
                    importers.add(str(path.relative_to(root)))
        self.assertEqual(importers, {"codeweaver/ui/server.py"})


if __name__ == "__main__":
    unittest.main()
