"""C++ code generation: the Python semantics each construct must keep."""

from __future__ import annotations

import unittest

from tests.native_support import translate


def cpp(source: str, path: str = "m.py") -> str:
    files, _ = translate({path: source}, "cpp")
    stem = path[:-3]
    return files[f"src/{stem}.hpp"] + files[f"src/{stem}.cpp"]


class ArithmeticTests(unittest.TestCase):
    def test_python_division_operators_use_the_runtime(self) -> None:
        out = cpp("def f(a: int, b: int) -> float:\n    return a / b + a // b + a % b\n")
        self.assertIn("cw::truediv(a, b)", out)
        self.assertIn("cw::floordiv(a, b)", out)
        self.assertIn("cw::mod(a, b)", out)

    def test_large_integer_literals_are_64_bit(self) -> None:
        out = cpp("X: int = 9000000000\n")
        self.assertIn("INT64_C(9000000000)", out)

    def test_ints_and_floats_have_fixed_width_types(self) -> None:
        out = cpp("def f(a: int, b: float) -> bool:\n    return a > b\n")
        self.assertIn("bool f(std::int64_t a, double b)", out)


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
        "        self.age = age\n"
        "    def speak(self) -> str:\n"
        "        return 'child'\n"
    )

    def test_only_overridden_methods_are_virtual(self) -> None:
        out = cpp(self.SOURCE)
        self.assertIn("virtual std::string speak()", out)
        self.assertIn("std::string speak() override", out)
        self.assertNotIn("virtual std::string label", out)

    def test_leaf_classes_are_final(self) -> None:
        self.assertIn("class Child final : public Base", cpp(self.SOURCE))

    def test_super_init_becomes_the_initialiser_list(self) -> None:
        self.assertIn("Child::Child(const std::string& name, std::int64_t age) : Base(name)", cpp(self.SOURCE))

    def test_instances_are_shared_pointers(self) -> None:
        out = cpp(self.SOURCE + "def make() -> Base:\n    return Child('a', 1)\n")
        self.assertIn("std::shared_ptr<Base> make()", out)
        self.assertIn("std::make_shared<Child>(\"a\"s, 1)", out)


class CallTests(unittest.TestCase):
    def test_keyword_arguments_are_matched_to_the_signature(self) -> None:
        out = cpp(
            "def area(width: int, height: int = 1, depth: int = 1) -> int:\n"
            "    return width * height * depth\n"
            "def use() -> int:\n"
            "    return area(2, depth=3)\n"
        )
        self.assertIn("return area(2, 1, 3);", out)

    def test_containers_that_are_only_read_are_passed_by_const_reference(self) -> None:
        out = cpp("def total(xs: list[int]) -> int:\n    return sum(xs)\n")
        self.assertIn("const std::vector<std::int64_t>& xs", out)

    def test_mutated_containers_are_passed_by_reference(self) -> None:
        out = cpp("def push(xs: list[int]) -> None:\n    xs.append(1)\n")
        self.assertIn("std::vector<std::int64_t>& xs", out)
        self.assertNotIn("const std::vector<std::int64_t>& xs", out)


class ControlFlowTests(unittest.TestCase):
    def test_bounds_checks_are_dropped_in_provably_safe_loops(self) -> None:
        out = cpp(
            "def total(xs: list[int]) -> int:\n"
            "    t = 0\n"
            "    for i in range(len(xs)):\n"
            "        t += xs[i]\n"
            "    return t\n"
        )
        self.assertIn("xs[static_cast<std::size_t>(i)]", out)

    def test_other_indexing_is_checked_and_accepts_negative_indices(self) -> None:
        self.assertIn("cw::at(xs, -1)", cpp("def last(xs: list[int]) -> int:\n    return xs[-1]\n"))

    def test_for_else_uses_a_break_flag(self) -> None:
        out = cpp(
            "def f(xs: list[int]) -> int:\n"
            "    for x in xs:\n"
            "        if x:\n"
            "            break\n"
            "    else:\n"
            "        return 0\n"
            "    return 1\n"
        )
        self.assertRegex(out, r"bool (cw_broke_\d+) = false;")
        self.assertRegex(out, r"if \(!cw_broke_\d+\)")

    def test_finally_is_a_scope_guard(self) -> None:
        out = cpp("def f() -> None:\n    try:\n        print(1)\n    finally:\n        print(2)\n")
        self.assertIn("cw::Finally", out)

    def test_loop_variable_read_after_the_loop_outlives_it(self) -> None:
        out = cpp(
            "def f(xs: list[int]) -> int:\n"
            "    for i in range(len(xs)):\n"
            "        if xs[i] == 0:\n"
            "            break\n"
            "    return i\n"
        )
        self.assertIn("std::int64_t i{};", out)

    def test_destructuring_swap_keeps_python_semantics(self) -> None:
        out = cpp("def f(a: int, b: int) -> int:\n    a, b = b, a\n    return a - b\n")
        self.assertRegex(out, r"auto cw_t1 = b;|std::int64_t cw_t1 = b;")


class HonestyTests(unittest.TestCase):
    def test_unsupported_constructs_are_marked_in_the_output(self) -> None:
        files, bag = translate({"m.py": "def f() -> None:\n    with open('x') as h:\n        pass\n"}, "cpp")
        self.assertIn("CODEWEAVER-UNSUPPORTED", files["src/m.cpp"])

    def test_project_files_include_runtime_and_cmake(self) -> None:
        files, _ = translate({"m.py": "X: int = 1\n"}, "cpp")
        self.assertIn("src/cw_runtime/runtime.hpp", files)
        self.assertIn("set(CMAKE_CXX_STANDARD 20)", files["CMakeLists.txt"])


if __name__ == "__main__":
    unittest.main()
