"""C code generation: the Python semantics each construct must keep."""

from __future__ import annotations

import unittest

from tests.native_support import translate


def c(source: str, path: str = "m.py") -> tuple[str, list[str]]:
    files, bag = translate({path: source}, "c")
    stem = path[:-3]
    return files[f"src/{stem}.h"] + files[f"src/{stem}.c"], [item.format() for item in bag]


class ArithmeticTests(unittest.TestCase):
    def test_python_division_operators_use_the_runtime(self) -> None:
        out, _ = c("def f(a: int, b: int) -> float:\n    return a / b + a // b + a % b\n")
        self.assertIn("cw_truediv(a, b)", out)
        self.assertIn("cw_floordiv_i64(a, b)", out)
        self.assertIn("cw_mod_i64(a, b)", out)

    def test_fixed_width_types(self) -> None:
        out, _ = c("def f(a: int, b: float, s: str) -> bool:\n    return a > b\n")
        self.assertIn("bool f(int64_t a, double b, cw_str s)", out)

    def test_large_literals_are_64_bit(self) -> None:
        out, _ = c("X: int = 9000000000\n")
        self.assertIn("INT64_C(9000000000)", out)


class ClassTests(unittest.TestCase):
    SOURCE = (
        "class Base:\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "    def speak(self) -> str:\n"
        "        return 'base'\n"
        "    def label(self) -> str:\n"
        "        return self.name\n"
        "class Child(Base):\n"
        "    def __init__(self, name: str, age: int) -> None:\n"
        "        super().__init__(name)\n"
        "        self.name = name + '!'\n"
        "        self.age = age\n"
        "    def speak(self) -> str:\n"
        "        return 'child'\n"
    )

    def test_subclass_embeds_its_base(self) -> None:
        out, _ = c(self.SOURCE)
        self.assertIn("struct Child {\n  Base base;", out)

    def test_inherited_field_lives_in_the_base_struct(self) -> None:
        out, _ = c(self.SOURCE)
        self.assertIn('self->base.name = cw_str_concat(name, "!");', out)
        self.assertNotIn("cw_str name;\n  int64_t age;", out)

    def test_only_overridden_methods_are_dispatched(self) -> None:
        out, _ = c(self.SOURCE)
        self.assertIn("cw_str (*vf_speak)(Base* self);", out)
        self.assertNotIn("vf_label", out)
        self.assertIn("self->base.vf_speak = Child_speak_vf;", out)

    def test_super_init_calls_the_base_directly(self) -> None:
        out, _ = c(self.SOURCE)
        self.assertIn("Base_init(((Base*)self), name);", out)


class ControlFlowTests(unittest.TestCase):
    def test_for_else_uses_a_break_flag(self) -> None:
        out, _ = c(
            "def f(xs: list[int]) -> int:\n"
            "    for x in xs:\n"
            "        if x > 2:\n"
            "            break\n"
            "    else:\n"
            "        return -1\n"
            "    return 0\n"
        )
        self.assertIn("bool cw_broke", out)
        self.assertIn("if (!cw_broke", out)

    def test_list_comprehension_is_a_loop_before_the_statement(self) -> None:
        out, _ = c(
            "def f(n: int) -> list[int]:\n    return [i * i for i in range(n) if i % 2 == 0]\n"
        )
        self.assertIn("cw_list_i64* cw_list", out)
        self.assertIn("if (cw_mod_i64(i, 2) == 0) cw_list_i64_append(", out)
        self.assertNotIn("cw_range(", out)

    def test_raise_reports_the_exception_and_exits(self) -> None:
        out, _ = c(
            "def f(x: int) -> int:\n    if x < 0:\n        raise ValueError('negative')\n    return x\n"
        )
        self.assertIn('cw_raise("ValueError", "negative");', out)


class HonestyTests(unittest.TestCase):
    def test_try_except_is_a_gap_and_the_body_is_stubbed(self) -> None:
        out, diagnostics = c(
            "def f(x: int) -> int:\n    try:\n        return 10 // x\n    except ZeroDivisionError:\n        return 0\n"
        )
        self.assertIn("CODEWEAVER-UNSUPPORTED", out)
        self.assertIn("cw_unsupported(", out)
        self.assertIn("#if 0", out)
        self.assertTrue(any("CW0600" in item for item in diagnostics))

    def test_dict_values_are_reported(self) -> None:
        out, _ = c("def f() -> int:\n    d = {'a': 1}\n    return len(d)\n")
        self.assertIn("CODEWEAVER-UNSUPPORTED", out)


class LayoutTests(unittest.TestCase):
    def test_colliding_names_get_module_prefixes(self) -> None:
        files, _ = translate(
            {
                "a.py": "def run() -> int:\n    return 1\n",
                "b.py": "def run() -> int:\n    return 2\n",
            },
            "c",
        )
        self.assertIn("int64_t a_run(void);", files["src/a.h"])
        self.assertIn("int64_t b_run(void);", files["src/b.h"])

    def test_names_reserved_by_c_are_renamed(self) -> None:
        files, _ = translate({"m.py": "def index(xs: list[int]) -> int:\n    return xs[0]\n"}, "c")
        self.assertNotIn("int64_t index(", files["src/m.h"])

    def test_project_files(self) -> None:
        files, _ = translate({"m.py": "X: int = 1\n"}, "c")
        self.assertIn("project(test LANGUAGES C)", files["CMakeLists.txt"])
        self.assertIn("CW_DEFINE_LIST", files["src/cw_runtime/cw.h"])


if __name__ == "__main__":
    unittest.main()
