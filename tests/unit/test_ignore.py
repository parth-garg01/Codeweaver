from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeweaver.project_intake.ignore import IgnoreRule, IgnoreRules, load_ignore_rules


def rules_from(*patterns: str) -> IgnoreRules:
    rules = IgnoreRules()
    rules.add_patterns(list(patterns), "test")
    return rules


class IgnoreRuleParsingTests(unittest.TestCase):
    def test_blank_lines_and_comments_are_dropped(self) -> None:
        self.assertIsNone(IgnoreRule.parse("", "t"))
        self.assertIsNone(IgnoreRule.parse("   ", "t"))
        self.assertIsNone(IgnoreRule.parse("# a comment", "t"))
        self.assertIsNone(IgnoreRule.parse("  # indented comment", "t"))

    def test_trailing_slash_marks_directory_only(self) -> None:
        rule = IgnoreRule.parse("build/", "t")
        assert rule is not None
        self.assertTrue(rule.directory_only)
        self.assertEqual(rule.pattern, "build")

    def test_leading_bang_marks_negation(self) -> None:
        rule = IgnoreRule.parse("!keep.py", "t")
        assert rule is not None
        self.assertTrue(rule.negated)
        self.assertEqual(rule.pattern, "keep.py")

    def test_slash_marks_anchored_pattern(self) -> None:
        rule = IgnoreRule.parse("src/generated", "t")
        assert rule is not None
        self.assertTrue(rule.anchored)

    def test_bare_bang_is_not_a_rule(self) -> None:
        self.assertIsNone(IgnoreRule.parse("!", "t"))


class IgnoreMatchingTests(unittest.TestCase):
    def test_basename_pattern_matches_at_any_depth(self) -> None:
        rules = rules_from("__pycache__/")
        self.assertTrue(rules.is_ignored("a/b/__pycache__", is_dir=True))
        self.assertTrue(rules.is_ignored("a/b/__pycache__/c.pyc"))

    def test_directory_only_pattern_does_not_match_file(self) -> None:
        rules = rules_from("build/")
        self.assertFalse(rules.is_ignored("build", is_dir=False))
        self.assertTrue(rules.is_ignored("build", is_dir=True))

    def test_anchored_pattern_only_matches_from_root(self) -> None:
        rules = rules_from("src/generated")
        self.assertTrue(rules.is_ignored("src/generated/a.py"))
        self.assertFalse(rules.is_ignored("lib/src/generated/a.py"))

    def test_glob_extension_pattern(self) -> None:
        rules = rules_from("*.log")
        self.assertTrue(rules.is_ignored("a/b/run.log"))
        self.assertFalse(rules.is_ignored("a/b/run.txt"))

    def test_character_class_pattern(self) -> None:
        rules = rules_from("*.py[cod]")
        self.assertTrue(rules.is_ignored("m.pyc"))
        self.assertTrue(rules.is_ignored("m.pyo"))
        self.assertFalse(rules.is_ignored("m.py"))

    def test_double_star_matches_any_depth(self) -> None:
        rules = rules_from("docs/**/draft.md")
        self.assertTrue(rules.is_ignored("docs/draft.md"))
        self.assertTrue(rules.is_ignored("docs/a/b/draft.md"))
        self.assertFalse(rules.is_ignored("other/a/draft.md"))

    def test_later_negation_wins(self) -> None:
        rules = rules_from("*.py", "!keep.py")
        self.assertTrue(rules.is_ignored("drop.py"))
        self.assertFalse(rules.is_ignored("keep.py"))

    def test_later_ignore_overrides_earlier_negation(self) -> None:
        rules = rules_from("!keep.py", "*.py")
        self.assertTrue(rules.is_ignored("keep.py"))

    def test_decide_reports_the_deciding_rule_source(self) -> None:
        rules = rules_from("*.log")
        decision = rules.decide("a.log", is_dir=False)
        assert decision is not None
        self.assertEqual(decision.source, "test")

    def test_unmatched_path_has_no_decision(self) -> None:
        self.assertIsNone(rules_from("*.log").decide("a.py", is_dir=False))


class LoadIgnoreRulesTests(unittest.TestCase):
    def test_layers_defaults_gitignore_and_codeweaverignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("*.generated.py\n", encoding="utf-8")
            (root / ".codeweaverignore").write_text("!keep.generated.py\n", encoding="utf-8")
            rules = load_ignore_rules(root)
            self.assertTrue(rules.is_ignored("node_modules", is_dir=True))
            self.assertTrue(rules.is_ignored("a.generated.py"))
            self.assertFalse(rules.is_ignored("keep.generated.py"))

    def test_defaults_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rules = load_ignore_rules(Path(tmp), use_defaults=False)
            self.assertEqual(len(rules), 0)

    def test_missing_ignore_file_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rules = IgnoreRules()
            self.assertFalse(rules.add_file(Path(tmp) / "nope", relative_to=Path(tmp)))


if __name__ == "__main__":
    unittest.main()
