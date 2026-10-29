from __future__ import annotations

import re
import unittest

from codeweaver.backends.common import (
    CodeWriter,
    IdentifierPolicy,
    escape_double_quoted,
)


class IdentifierPolicyTests(unittest.TestCase):
    def policy(self) -> IdentifierPolicy:
        return IdentifierPolicy(reserved=frozenset({"class", "func"}))

    def test_an_ordinary_name_is_left_alone(self) -> None:
        self.assertEqual(self.policy().identifier("total_count"), "total_count")

    def test_a_reserved_word_is_suffixed(self) -> None:
        self.assertEqual(self.policy().identifier("class"), "class_")

    def test_an_invalid_character_is_replaced(self) -> None:
        self.assertEqual(self.policy().identifier("a-b"), "a_b")

    def test_a_leading_digit_is_prefixed(self) -> None:
        self.assertEqual(self.policy().identifier("1st"), "_1st")

    def test_an_empty_name_becomes_the_prefix(self) -> None:
        self.assertEqual(self.policy().identifier(""), "_")

    def test_validity_accounts_for_reserved_words(self) -> None:
        policy = self.policy()
        self.assertTrue(policy.is_valid("total"))
        self.assertFalse(policy.is_valid("class"))
        self.assertFalse(policy.is_valid("a-b"))

    def test_a_reserved_word_is_fine_as_a_member(self) -> None:
        self.assertEqual(self.policy().member("class"), "class")

    def test_an_invalid_member_name_is_made_valid(self) -> None:
        self.assertEqual(self.policy().member("a-b"), "a_b")
        self.assertFalse(self.policy().is_plain_member("a-b"))

    def test_extra_characters_can_be_permitted(self) -> None:
        policy = IdentifierPolicy(
            pattern=re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$"), extra_chars="$"
        )
        self.assertEqual(policy.identifier("$x"), "$x")
        self.assertTrue(policy.is_valid("$x"))

    def test_naming_is_deterministic(self) -> None:
        policy = self.policy()
        self.assertEqual(policy.identifier("a-b"), policy.identifier("a-b"))

    def test_export_spelling_defaults_to_unchanged(self) -> None:
        self.assertEqual(self.policy().exported("thing"), "thing")


class StringEscapingTests(unittest.TestCase):
    def test_plain_text(self) -> None:
        self.assertEqual(escape_double_quoted("hi"), '"hi"')

    def test_quotes_and_backslashes(self) -> None:
        self.assertEqual(escape_double_quoted('a"b\\c'), '"a\\"b\\\\c"')

    def test_newlines_and_tabs(self) -> None:
        self.assertEqual(escape_double_quoted("a\nb\tc"), '"a\\nb\\tc"')

    def test_control_characters(self) -> None:
        self.assertEqual(escape_double_quoted("\x00"), '"\\u0000"')

    def test_non_ascii_is_kept_literal(self) -> None:
        self.assertEqual(escape_double_quoted("café"), '"café"')


class SharedWriterTests(unittest.TestCase):
    def test_the_writer_is_importable_from_the_shared_package(self) -> None:
        writer = CodeWriter()
        with writer.block("func main()") as inner:
            inner.line("return;")
        self.assertEqual(writer.render(), "func main() {\n  return;\n}\n")

    def test_the_indent_text_is_configurable_per_language(self) -> None:
        writer = CodeWriter(indent_text="\t")
        writer.indent()
        writer.line("x")
        self.assertEqual(writer.render(), "\tx\n")


if __name__ == "__main__":
    unittest.main()
