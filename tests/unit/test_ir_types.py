from __future__ import annotations

import unittest

from codeweaver.ir.types import (
    IRType,
    TypeKind,
    t_any,
    t_bool,
    t_callable,
    t_dict,
    t_int,
    t_list,
    t_none,
    t_optional,
    t_str,
    t_tuple,
    t_union,
    t_unknown,
)
from codeweaver.ir.version import IR_SCHEMA_VERSION, SCHEMA_HISTORY, is_compatible


class TypeConstructorTests(unittest.TestCase):
    def test_primitives(self) -> None:
        self.assertEqual(str(t_int()), "int")
        self.assertEqual(str(t_str()), "str")
        self.assertEqual(t_bool().kind, TypeKind.PRIMITIVE)

    def test_none_and_any(self) -> None:
        self.assertEqual(t_none().kind, TypeKind.NONE)
        self.assertEqual(t_any().kind, TypeKind.ANY)

    def test_unknown_is_not_known(self) -> None:
        self.assertFalse(t_unknown().is_known)
        self.assertTrue(t_int().is_known)

    def test_unknown_keeps_source_spelling(self) -> None:
        self.assertEqual(t_unknown("Sequence[T]").source_text, "Sequence[T]")

    def test_collections_render_with_arguments(self) -> None:
        self.assertEqual(str(t_list(t_int())), "list[int]")
        self.assertEqual(str(t_dict(t_str(), t_int())), "dict[str, int]")
        self.assertEqual(str(t_tuple(t_int(), t_str())), "tuple[int, str]")

    def test_bare_collection_defaults_to_unknown_item(self) -> None:
        self.assertEqual(str(t_list()), "list[unknown]")

    def test_callable_renders_arrow(self) -> None:
        self.assertEqual(str(t_callable((t_int(), t_str()), t_bool())), "(int, str) -> bool")

    def test_optional_renders_union_with_none(self) -> None:
        optional = t_optional(t_int())
        self.assertTrue(optional.nullable)
        self.assertEqual(str(optional), "int | None")

    def test_optional_is_idempotent(self) -> None:
        once = t_optional(t_int())
        self.assertIs(t_optional(once), once)

    def test_union_deduplicates_and_collapses(self) -> None:
        self.assertEqual(str(t_union(t_int(), t_str())), "int | str")
        self.assertEqual(t_union(t_int(), t_int()), t_int())

    def test_object_type_renders_its_name(self) -> None:
        self.assertEqual(str(IRType(TypeKind.OBJECT, "Account")), "Account")


class TypeSerialisationTests(unittest.TestCase):
    def test_round_trips_nested_types(self) -> None:
        original = t_dict(t_str(), t_list(t_optional(t_int())))
        self.assertEqual(IRType.from_dict(original.to_dict()), original)

    def test_omits_empty_fields(self) -> None:
        payload = t_any().to_dict()
        self.assertNotIn("args", payload)
        self.assertEqual(payload["kind"], "type")

    def test_nullable_flag_survives(self) -> None:
        self.assertTrue(IRType.from_dict(t_optional(t_str()).to_dict()).nullable)


class SchemaVersionTests(unittest.TestCase):
    def test_current_version_is_documented(self) -> None:
        self.assertIn(IR_SCHEMA_VERSION, SCHEMA_HISTORY)

    def test_same_version_is_compatible(self) -> None:
        self.assertTrue(is_compatible(IR_SCHEMA_VERSION))

    def test_newer_minor_version_is_rejected(self) -> None:
        self.assertFalse(is_compatible("1.9.0"))

    def test_different_major_version_is_rejected(self) -> None:
        self.assertFalse(is_compatible("2.0.0"))

    def test_malformed_version_is_rejected(self) -> None:
        self.assertFalse(is_compatible("not-a-version"))
        self.assertFalse(is_compatible("1.0"))


if __name__ == "__main__":
    unittest.main()
