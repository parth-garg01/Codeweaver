from __future__ import annotations

import unittest

from codeweaver.backends.typescript.types import render_type
from codeweaver.ir.types import (
    IRType,
    TypeKind,
    t_any,
    t_bool,
    t_callable,
    t_dict,
    t_float,
    t_int,
    t_list,
    t_none,
    t_optional,
    t_set,
    t_str,
    t_tuple,
    t_union,
    t_unknown,
)


class PrimitiveTypeTests(unittest.TestCase):
    def test_numbers_collapse_onto_number(self) -> None:
        self.assertEqual(render_type(t_int()), "number")
        self.assertEqual(render_type(t_float()), "number")

    def test_string_and_boolean(self) -> None:
        self.assertEqual(render_type(t_str()), "string")
        self.assertEqual(render_type(t_bool()), "boolean")

    def test_bytes_becomes_a_typed_array(self) -> None:
        self.assertEqual(render_type(IRType(TypeKind.PRIMITIVE, "bytes")), "Uint8Array")

    def test_none_becomes_null(self) -> None:
        self.assertEqual(render_type(t_none()), "null")

    def test_any_stays_any_but_unknown_becomes_unknown(self) -> None:
        self.assertEqual(render_type(t_any()), "any")
        self.assertEqual(render_type(t_unknown()), "unknown")

    def test_absent_type_is_unknown(self) -> None:
        self.assertEqual(render_type(None), "unknown")

    def test_unrecognised_primitive_is_unknown(self) -> None:
        self.assertEqual(render_type(IRType(TypeKind.PRIMITIVE, "complex")), "unknown")


class CollectionTypeTests(unittest.TestCase):
    def test_list_becomes_an_array(self) -> None:
        self.assertEqual(render_type(t_list(t_int())), "number[]")

    def test_bare_list_is_an_unknown_array(self) -> None:
        self.assertEqual(render_type(t_list()), "unknown[]")

    def test_nested_list(self) -> None:
        self.assertEqual(render_type(t_list(t_list(t_str()))), "string[][]")

    def test_array_of_union_is_parenthesised(self) -> None:
        self.assertEqual(render_type(t_list(t_union(t_int(), t_str()))), "(number | string)[]")

    def test_set_becomes_a_set(self) -> None:
        self.assertEqual(render_type(t_set(t_str())), "Set<string>")

    def test_tuple_becomes_a_tuple_type(self) -> None:
        self.assertEqual(render_type(t_tuple(t_int(), t_str())), "[number, string]")

    def test_bare_tuple_is_an_unknown_array(self) -> None:
        self.assertEqual(render_type(IRType(TypeKind.COLLECTION, "tuple")), "unknown[]")

    def test_string_keyed_dict_becomes_a_record(self) -> None:
        self.assertEqual(render_type(t_dict(t_str(), t_int())), "Record<string, number>")

    def test_number_keyed_dict_becomes_a_record(self) -> None:
        self.assertEqual(render_type(t_dict(t_int(), t_str())), "Record<number, string>")

    def test_object_keyed_dict_becomes_a_map(self) -> None:
        key = IRType(TypeKind.OBJECT, "Account")
        self.assertEqual(render_type(t_dict(key, t_int())), "Map<Account, number>")

    def test_unknown_keyed_dict_becomes_a_map(self) -> None:
        self.assertEqual(render_type(t_dict()), "Map<unknown, unknown>")


class UnionTypeTests(unittest.TestCase):
    def test_optional_becomes_a_null_union(self) -> None:
        self.assertEqual(render_type(t_optional(t_int())), "number | null")

    def test_union(self) -> None:
        self.assertEqual(render_type(t_union(t_int(), t_str())), "number | string")

    def test_optional_of_a_callable_is_parenthesised(self) -> None:
        rendered = render_type(t_optional(t_callable((t_int(),), t_str())))
        self.assertEqual(rendered, "((arg0: number) => string) | null")


class CallableTypeTests(unittest.TestCase):
    def test_callable_with_parameters(self) -> None:
        rendered = render_type(t_callable((t_int(), t_str()), t_bool()))
        self.assertEqual(rendered, "(arg0: number, arg1: string) => boolean")

    def test_callable_with_no_parameters(self) -> None:
        self.assertEqual(render_type(t_callable((), t_int())), "() => number")

    def test_bare_callable_accepts_anything(self) -> None:
        rendered = render_type(IRType(TypeKind.CALLABLE, "callable"))
        self.assertEqual(rendered, "(...args: unknown[]) => unknown")


class ObjectTypeTests(unittest.TestCase):
    def test_object_type_keeps_its_name(self) -> None:
        self.assertEqual(render_type(IRType(TypeKind.OBJECT, "Account")), "Account")

    def test_reserved_object_name_is_made_safe(self) -> None:
        self.assertEqual(render_type(IRType(TypeKind.OBJECT, "enum")), "enum_")

    def test_generic_object_type(self) -> None:
        inner = (IRType(TypeKind.OBJECT, "Account"),)
        rendered = render_type(IRType(TypeKind.OBJECT, "Repository", inner))
        self.assertEqual(rendered, "Repository<Account>")

    def test_unnamed_object_type_is_unknown(self) -> None:
        self.assertEqual(render_type(IRType(TypeKind.OBJECT, "")), "unknown")


if __name__ == "__main__":
    unittest.main()


class ReturnTypeTests(unittest.TestCase):
    def test_none_return_becomes_void(self) -> None:
        from codeweaver.backends.typescript.types import render_return_type

        self.assertEqual(render_return_type(t_none()), "void")

    def test_other_return_types_are_unchanged(self) -> None:
        from codeweaver.backends.typescript.types import render_return_type

        self.assertEqual(render_return_type(t_int()), "number")
        self.assertEqual(render_return_type(None), "unknown")

    def test_optional_return_is_still_a_null_union(self) -> None:
        from codeweaver.backends.typescript.types import render_return_type

        self.assertEqual(render_return_type(t_optional(t_int())), "number | null")

    def test_value_position_still_renders_none_as_null(self) -> None:
        self.assertEqual(render_type(t_none()), "null")
