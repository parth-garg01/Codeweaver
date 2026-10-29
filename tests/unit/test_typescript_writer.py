from __future__ import annotations

import unittest

from codeweaver.backends.common.writer import CodeWriter
from codeweaver.backends.typescript.naming import (
    escape_string,
    is_valid_identifier,
    safe_identifier,
    safe_property,
)
from codeweaver.diagnostics import SourceSpan


class CodeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = CodeWriter()

    def test_writes_lines_in_order(self) -> None:
        self.writer.line("a")
        self.writer.line("b")
        self.assertEqual(self.writer.render(), "a\nb\n")

    def test_indentation_uses_two_spaces(self) -> None:
        self.writer.line("outer")
        self.writer.indent()
        self.writer.line("inner")
        self.writer.dedent()
        self.assertEqual(self.writer.render(), "outer\n  inner\n")

    def test_empty_lines_carry_no_whitespace(self) -> None:
        self.writer.indent()
        self.writer.line()
        self.assertEqual(self.writer.lines[0], "")

    def test_dedent_below_margin_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.writer.dedent()

    def test_block_opens_and_closes_braces(self) -> None:
        with self.writer.block("function f()") as writer:
            writer.line("return 1;")
        self.assertEqual(self.writer.render(), "function f() {\n  return 1;\n}\n")

    def test_nested_blocks(self) -> None:
        with self.writer.block("class C") as writer, writer.block("m()") as inner:
            inner.line("return;")
        self.assertEqual(self.writer.render(), "class C {\n  m() {\n    return;\n  }\n}\n")

    def test_consecutive_blanks_collapse(self) -> None:
        self.writer.line("a")
        self.writer.blank()
        self.writer.blank()
        self.writer.line("b")
        self.assertEqual(self.writer.render(), "a\n\nb\n")

    def test_leading_blank_is_not_emitted_twice(self) -> None:
        self.writer.blank()
        self.assertEqual(self.writer.render(), "")

    def test_render_ends_with_exactly_one_newline(self) -> None:
        self.writer.line("a")
        self.writer.blank()
        self.assertEqual(self.writer.render(), "a\n")

    def test_empty_writer_renders_nothing(self) -> None:
        self.assertEqual(CodeWriter().render(), "")

    def test_multiline_text_is_indented_per_line(self) -> None:
        self.writer.indent()
        self.writer.lines_of("one\ntwo")
        self.assertEqual(self.writer.lines, ["  one", "  two"])

    def test_mapping_records_output_and_source_lines(self) -> None:
        self.writer.line("const a = 1;", SourceSpan("m.py", 7, 0))
        entry = self.writer.mappings[0]
        self.assertEqual(entry.output_line, 1)
        self.assertEqual(entry.source_line, 7)
        self.assertEqual(entry.source_file, "m.py")

    def test_unlocated_spans_are_not_mapped(self) -> None:
        self.writer.line("x", SourceSpan("m.py"))
        self.assertEqual(self.writer.mappings, [])

    def test_mapping_dict_is_json_friendly(self) -> None:
        import json

        self.writer.line("x", SourceSpan("m.py", 1, 0))
        json.dumps(self.writer.mapping_dict())

    def test_output_is_identical_across_runs(self) -> None:
        def build() -> str:
            writer = CodeWriter()
            with writer.block("function f()") as inner:
                inner.line("return 1;")
            return writer.render()

        self.assertEqual(build(), build())


class NamingTests(unittest.TestCase):
    def test_plain_names_are_preserved(self) -> None:
        self.assertEqual(safe_identifier("total_count"), "total_count")
        self.assertEqual(safe_identifier("Account"), "Account")

    def test_reserved_words_gain_an_underscore(self) -> None:
        self.assertEqual(safe_identifier("class"), "class_")
        self.assertEqual(safe_identifier("function"), "function_")

    def test_invalid_characters_are_replaced(self) -> None:
        self.assertEqual(safe_identifier("a-b"), "a_b")

    def test_leading_digit_is_prefixed(self) -> None:
        self.assertEqual(safe_identifier("1st"), "_1st")

    def test_empty_name_becomes_an_underscore(self) -> None:
        self.assertEqual(safe_identifier(""), "_")

    def test_dollar_and_underscore_are_valid(self) -> None:
        self.assertTrue(is_valid_identifier("$x"))
        self.assertTrue(is_valid_identifier("_x"))

    def test_reserved_words_are_not_valid_identifiers(self) -> None:
        self.assertFalse(is_valid_identifier("const"))

    def test_properties_keep_reserved_words_unquoted(self) -> None:
        self.assertEqual(safe_property("class"), "class")

    def test_invalid_property_names_are_quoted(self) -> None:
        self.assertEqual(safe_property("a-b"), '"a-b"')

    def test_naming_is_deterministic(self) -> None:
        self.assertEqual(safe_identifier("a-b"), safe_identifier("a-b"))


class StringEscapingTests(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(escape_string("hi"), '"hi"')

    def test_quotes_and_backslashes(self) -> None:
        self.assertEqual(escape_string('a"b\\c'), '"a\\"b\\\\c"')

    def test_newlines_and_tabs(self) -> None:
        self.assertEqual(escape_string("a\nb\tc"), '"a\\nb\\tc"')

    def test_non_ascii_is_kept_literal(self) -> None:
        self.assertEqual(escape_string("café"), '"café"')

    def test_control_characters_are_escaped(self) -> None:
        self.assertEqual(escape_string("\x00"), '"\\u0000"')


if __name__ == "__main__":
    unittest.main()
