"""Java code generation: the Python semantics each construct must keep."""

from __future__ import annotations

import unittest

from tests.native_support import translate


def java(source: str, path: str = "m.py") -> dict[str, str]:
    files, _ = translate({path: source}, "java")
    return files


def module_class(source: str) -> str:
    return java(source)["src/main/java/M.java"]


class ArithmeticTests(unittest.TestCase):
    def test_floor_division_and_modulo_round_like_python(self) -> None:
        out = module_class("def f(a: int, b: int) -> int:\n    return a // b + a % b\n")
        self.assertIn("Py.add(Math.floorDiv(a, b), Math.floorMod(a, b))", out)

    def test_true_division_always_produces_a_double(self) -> None:
        self.assertIn(
            "Py.div(a, b)", module_class("def f(a: int, b: int) -> float:\n    return a / b\n")
        )

    def test_integer_literals_are_longs(self) -> None:
        self.assertIn("return 3L;", module_class("def f() -> int:\n    return 3\n"))

    def test_int_literal_where_a_float_is_expected(self) -> None:
        self.assertIn("double x = 3.0;", module_class("def f() -> None:\n    x: float = 3\n"))


class EqualityTests(unittest.TestCase):
    def test_strings_compare_by_value(self) -> None:
        out = module_class("def f(a: str, b: str) -> bool:\n    return a == b\n")
        self.assertIn("Objects.equals(a, b)", out)

    def test_primitives_compare_with_operators(self) -> None:
        self.assertIn("a == b", module_class("def f(a: int, b: int) -> bool:\n    return a == b\n"))

    def test_none_checks_compare_with_null(self) -> None:
        out = module_class("def f(a: int | None) -> bool:\n    return a is None\n")
        self.assertIn("a == null", out)


class LayoutTests(unittest.TestCase):
    SOURCE = (
        "class Animal:\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "    def speak(self) -> str:\n"
        "        return self.name\n"
        "class Dog(Animal):\n"
        "    def speak(self) -> str:\n"
        "        return 'woof'\n"
    )

    def test_each_class_gets_its_own_file(self) -> None:
        files = java(self.SOURCE, "zoo/animals.py")
        self.assertIn("src/main/java/zoo/Animal.java", files)
        self.assertIn("src/main/java/zoo/Dog.java", files)
        self.assertIn("src/main/java/zoo/Animals.java", files)
        self.assertIn("package zoo;", files["src/main/java/zoo/Dog.java"])

    def test_constructors_are_forwarded_to_subclasses(self) -> None:
        dog = java(self.SOURCE)["src/main/java/Dog.java"]
        self.assertIn("public Dog(String name) {", dog)
        self.assertIn("super(name);", dog)

    def test_project_files_include_runtime_and_pom(self) -> None:
        files = java("X: int = 1\n")
        self.assertIn("src/main/java/cwrt/Py.java", files)
        self.assertIn("<maven.compiler.release>17</maven.compiler.release>", files["pom.xml"])


class ControlFlowTests(unittest.TestCase):
    def test_try_else_runs_only_when_the_body_completed(self) -> None:
        out = module_class(
            "def f(a: int) -> int:\n"
            "    try:\n"
            "        b = 10 // a\n"
            "    except ZeroDivisionError:\n"
            "        return -1\n"
            "    else:\n"
            "        return b\n"
        )
        self.assertRegex(out, r"boolean (cwCompleted\d+) = false;")
        self.assertIn("catch (ArithmeticException", out)

    def test_dead_code_after_return_is_omitted(self) -> None:
        out = module_class("def f() -> int:\n    return 1\n    print('never')\n")
        self.assertNotIn("Py.print", out)

    def test_a_method_that_can_fall_off_the_end_throws(self) -> None:
        out = module_class("def f(a: int) -> int:\n    if a:\n        return 1\n")
        self.assertIn("function ended without returning a value", out)

    def test_lambdas_capture_copies_of_reassigned_locals(self) -> None:
        out = module_class(
            "def f(xs: list[int]) -> list[int]:\n"
            "    k = 1\n"
            "    k = 2\n"
            "    return [x * k for x in xs]\n"
        )
        self.assertRegex(out, r"final var (cwCap\d+) = k;")

    def test_subsumed_handlers_are_dropped(self) -> None:
        out = module_class(
            "def f() -> None:\n"
            "    try:\n"
            "        print(1)\n"
            "    except Exception:\n"
            "        print(2)\n"
            "    except ValueError:\n"
            "        print(3)\n"
        )
        self.assertNotIn("IllegalArgumentException", out)


class HonestyTests(unittest.TestCase):
    def test_undefined_names_become_markers(self) -> None:
        files, bag = translate({"m.py": "def f() -> int:\n    return helper()\n"}, "java")
        self.assertIn("CODEWEAVER-UNSUPPORTED", files["src/main/java/M.java"])
        self.assertTrue(any(item.code == "CW0600" for item in bag))


if __name__ == "__main__":
    unittest.main()
