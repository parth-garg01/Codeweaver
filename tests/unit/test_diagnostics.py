from __future__ import annotations

import unittest

from codeweaver.diagnostics import Diagnostic, DiagnosticBag, Severity, SourceSpan


class SourceSpanTests(unittest.TestCase):
    def test_str_uses_one_based_columns(self) -> None:
        span = SourceSpan("a/b.py", 12, 4, 12, 9)
        self.assertEqual(str(span), "a/b.py:12:5")

    def test_str_falls_back_to_file_when_unlocated(self) -> None:
        self.assertEqual(str(SourceSpan("a/b.py")), "a/b.py")

    def test_round_trips_through_dict(self) -> None:
        span = SourceSpan("a/b.py", 1, 2, 3, 4)
        self.assertEqual(SourceSpan.from_dict(span.to_dict()), span)


class DiagnosticTests(unittest.TestCase):
    def test_format_includes_location_code_and_hint(self) -> None:
        diagnostic = Diagnostic(
            code="CW1001",
            message="unsupported construct",
            severity=Severity.WARNING,
            span=SourceSpan("m.py", 3, 0),
            hint="rewrite as a plain loop",
        )
        text = diagnostic.format()
        self.assertIn("m.py:3:1", text)
        self.assertIn("warning[CW1001]", text)
        self.assertIn("hint: rewrite as a plain loop", text)

    def test_round_trips_through_dict(self) -> None:
        diagnostic = Diagnostic("CW1", "boom", Severity.INFO, SourceSpan("m.py", 1, 0), "stage")
        self.assertEqual(Diagnostic.from_dict(diagnostic.to_dict()), diagnostic)


class DiagnosticBagTests(unittest.TestCase):
    def test_deduplicates_identical_diagnostics(self) -> None:
        bag = DiagnosticBag()
        bag.error("CW1", "same")
        bag.error("CW1", "same")
        self.assertEqual(len(bag), 1)

    def test_keeps_same_code_at_different_locations(self) -> None:
        bag = DiagnosticBag()
        bag.error("CW1", "same", span=SourceSpan("a.py", 1, 0))
        bag.error("CW1", "same", span=SourceSpan("a.py", 2, 0))
        self.assertEqual(len(bag), 2)

    def test_partitions_by_severity(self) -> None:
        bag = DiagnosticBag()
        bag.error("CW1", "e")
        bag.warning("CW2", "w")
        bag.info("CW3", "i")
        self.assertTrue(bag.has_errors)
        self.assertEqual([d.code for d in bag.errors], ["CW1"])
        self.assertEqual([d.code for d in bag.warnings], ["CW2"])
        self.assertEqual([d.code for d in bag.of(Severity.INFO)], ["CW3"])

    def test_empty_bag_has_no_errors(self) -> None:
        self.assertFalse(DiagnosticBag().has_errors)

    def test_severity_rank_orders_error_above_warning(self) -> None:
        self.assertGreater(Severity.ERROR.rank, Severity.WARNING.rank)
        self.assertGreater(Severity.WARNING.rank, Severity.INFO.rank)


if __name__ == "__main__":
    unittest.main()
