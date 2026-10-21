"""Regression test for issue #2.

`import pkg.util` bound a namespace import to `util` while call sites still
emitted `pkg.util.helper(...)`, so the generated file referred to a name that
was never imported. These tests fail on the code before the fix.

https://github.com/parth-garg01/Codeweaver/issues/2
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from codeweaver.frontends.python import parse_source
from codeweaver.graph import build_dependency_graph, build_symbol_index
from codeweaver.graph.dependency import EdgeKind
from codeweaver.ir.declarations import IRImport
from codeweaver.pipeline import MigrationOptions, run_migration
from codeweaver.validation.results import CheckOutcome

HAS_TSC = shutil.which("tsc") is not None

UTIL = "def helper(n: int) -> int:\n    return n + 1\n"
PLAIN = "import pkg.util\n\n\ndef go(n: int) -> int:\n    return pkg.util.helper(n)\n"
ALIASED = "import pkg.util as u\n\n\ndef go(n: int) -> int:\n    return u.helper(n)\n"
DEEP = "import pkg.deep.inner\n\n\ndef go(n: int) -> int:\n    return pkg.deep.inner.helper(n)\n"


class LocalBindingTests(unittest.TestCase):
    def test_a_dotted_import_flattens_to_one_binding(self) -> None:
        self.assertEqual(IRImport("pkg.util").local_binding, "pkg_util")

    def test_an_alias_wins(self) -> None:
        self.assertEqual(IRImport("pkg.util", module_alias="u").local_binding, "u")

    def test_a_single_component_import_keeps_its_name(self) -> None:
        self.assertEqual(IRImport("os").local_binding, "os")

    def test_two_modules_with_the_same_leaf_do_not_collide(self) -> None:
        self.assertNotEqual(
            IRImport("pkg.util").local_binding, IRImport("other.util").local_binding
        )


class ProjectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "p"
        self.out = Path(self._tmp.name) / "out"
        (self.root / "pkg" / "deep").mkdir(parents=True)
        (self.root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "pkg" / "deep" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "pkg" / "util.py").write_text(UTIL, encoding="utf-8")
        (self.root / "pkg" / "deep" / "inner.py").write_text(UTIL, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative: str, source: str) -> None:
        (self.root / relative).write_text(source, encoding="utf-8")

    def migrate(self, *, validate: bool = False):
        return run_migration(self.root, self.out, MigrationOptions(run_validation=validate))

    def generated(self, relative: str) -> str:
        return (self.out / relative).read_text(encoding="utf-8")


class GeneratedReferenceTests(ProjectTestCase):
    def test_a_dotted_reference_uses_the_import_binding(self) -> None:
        self.write("pkg/app.py", PLAIN)
        self.migrate()
        contents = self.generated("pkg/app.ts")
        self.assertIn('import * as pkg_util from "./util";', contents)
        self.assertIn("return pkg_util.helper(n);", contents)

    def test_no_reference_to_the_unimported_package_name_remains(self) -> None:
        self.write("pkg/app.py", PLAIN)
        self.migrate()
        self.assertNotIn("pkg.util.helper", self.generated("pkg/app.ts"))

    def test_an_aliased_import_keeps_working(self) -> None:
        self.write("pkg/aliased.py", ALIASED)
        self.migrate()
        contents = self.generated("pkg/aliased.ts")
        self.assertIn('import * as u from "./util";', contents)
        self.assertIn("return u.helper(n);", contents)

    def test_a_deeply_dotted_import_is_rewritten(self) -> None:
        self.write("pkg/deepuse.py", DEEP)
        self.migrate()
        contents = self.generated("pkg/deepuse.ts")
        self.assertIn("import * as pkg_deep_inner from", contents)
        self.assertIn("return pkg_deep_inner.helper(n);", contents)

    def test_the_longest_matching_prefix_wins(self) -> None:
        self.write(
            "pkg/both.py",
            "import pkg.util\nimport pkg.deep.inner\n\n\n"
            "def go(n: int) -> int:\n"
            "    return pkg.deep.inner.helper(pkg.util.helper(n))\n",
        )
        self.migrate()
        contents = self.generated("pkg/both.ts")
        self.assertIn("pkg_deep_inner.helper(pkg_util.helper(n))", contents)

    def test_an_unrelated_attribute_chain_is_untouched(self) -> None:
        self.write(
            "pkg/plain.py",
            "def go(obj) -> int:\n    return obj.inner.value\n",
        )
        self.migrate()
        self.assertIn("return obj.inner.value;", self.generated("pkg/plain.ts"))

    def test_a_from_import_is_unaffected(self) -> None:
        self.write(
            "pkg/fromimp.py",
            "from pkg.util import helper\n\n\ndef go(n: int) -> int:\n    return helper(n)\n",
        )
        self.migrate()
        contents = self.generated("pkg/fromimp.ts")
        self.assertIn('import { helper } from "./util";', contents)
        self.assertIn("return helper(n);", contents)


class GraphAgreementTests(ProjectTestCase):
    def test_the_graph_resolves_the_call_through_the_same_binding(self) -> None:
        self.write("pkg/app.py", PLAIN)
        modules = []
        for relative in ("pkg/__init__.py", "pkg/util.py", "pkg/app.py"):
            text = (self.root / relative).read_text(encoding="utf-8")
            result = parse_source(text, relative)
            assert result.module is not None
            modules.append(result.module)
        graph = build_dependency_graph(modules, symbols=build_symbol_index(modules))
        imports = graph.successors("pkg.app", (EdgeKind.IMPORT,))
        self.assertIn("pkg.util", imports)


class ToolchainTests(ProjectTestCase):
    @unittest.skipUnless(HAS_TSC, "tsc is not installed")
    def test_the_generated_project_type_checks(self) -> None:
        self.write("pkg/app.py", PLAIN)
        self.write("pkg/aliased.py", ALIASED)
        self.write("pkg/deepuse.py", DEEP)
        outcome = self.migrate(validate=True)
        check = outcome.report.validation.checks[0]
        self.assertEqual(check.outcome, CheckOutcome.PASSED, msg=check.output)


if __name__ == "__main__":
    unittest.main()
