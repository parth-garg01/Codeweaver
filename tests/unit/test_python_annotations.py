from __future__ import annotations

import ast
import unittest

from codeweaver.frontends.python.annotations import annotation_to_type, infer_literal_type
from codeweaver.ir.types import TypeKind


def parse_annotation(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


def rendered(text: str) -> str:
    return str(annotation_to_type(parse_annotation(text)))


class PrimitiveAnnotationTests(unittest.TestCase):
    def test_primitives(self) -> None:
        self.assertEqual(rendered("int"), "int")
        self.assertEqual(rendered("float"), "float")
        self.assertEqual(rendered("str"), "str")
        self.assertEqual(rendered("bool"), "bool")
        self.assertEqual(rendered("bytes"), "bytes")

    def test_none_annotation(self) -> None:
        self.assertEqual(annotation_to_type(parse_annotation("None")).kind, TypeKind.NONE)

    def test_any_and_object(self) -> None:
        self.assertEqual(annotation_to_type(parse_annotation("Any")).kind, TypeKind.ANY)
        self.assertEqual(annotation_to_type(parse_annotation("object")).kind, TypeKind.ANY)

    def test_missing_annotation_is_unknown(self) -> None:
        self.assertEqual(annotation_to_type(None).kind, TypeKind.UNKNOWN)

    def test_user_type_becomes_an_object_type(self) -> None:
        found = annotation_to_type(parse_annotation("Account"))
        self.assertEqual(found.kind, TypeKind.OBJECT)
        self.assertEqual(found.name, "Account")


class CollectionAnnotationTests(unittest.TestCase):
    def test_builtin_generics(self) -> None:
        self.assertEqual(rendered("list[int]"), "list[int]")
        self.assertEqual(rendered("dict[str, int]"), "dict[str, int]")
        self.assertEqual(rendered("set[str]"), "set[str]")
        self.assertEqual(rendered("tuple[int, str]"), "tuple[int, str]")

    def test_typing_aliases_collapse_onto_builtins(self) -> None:
        self.assertEqual(rendered("List[int]"), "list[int]")
        self.assertEqual(rendered("Dict[str, int]"), "dict[str, int]")
        self.assertEqual(rendered("Sequence[int]"), "list[int]")
        self.assertEqual(rendered("Iterable[int]"), "list[int]")
        self.assertEqual(rendered("Mapping[str, int]"), "dict[str, int]")

    def test_dotted_typing_names_are_handled(self) -> None:
        self.assertEqual(rendered("typing.List[int]"), "list[int]")

    def test_bare_collections_have_unknown_elements(self) -> None:
        self.assertEqual(rendered("list"), "list[unknown]")
        self.assertEqual(rendered("dict"), "dict[unknown, unknown]")

    def test_nested_collections(self) -> None:
        self.assertEqual(rendered("dict[str, list[int]]"), "dict[str, list[int]]")

    def test_unknown_generic_keeps_its_name_and_arguments(self) -> None:
        found = annotation_to_type(parse_annotation("Repository[Account]"))
        self.assertEqual(found.kind, TypeKind.OBJECT)
        self.assertEqual(found.name, "Repository")
        self.assertEqual(found.args[0].name, "Account")


class UnionAnnotationTests(unittest.TestCase):
    def test_pep604_union(self) -> None:
        self.assertEqual(rendered("int | str"), "int | str")

    def test_typing_union(self) -> None:
        self.assertEqual(rendered("Union[int, str]"), "int | str")

    def test_union_with_none_becomes_optional(self) -> None:
        found = annotation_to_type(parse_annotation("int | None"))
        self.assertEqual(found.kind, TypeKind.OPTIONAL)
        self.assertTrue(found.nullable)
        self.assertEqual(str(found), "int | None")

    def test_optional_alias(self) -> None:
        found = annotation_to_type(parse_annotation("Optional[str]"))
        self.assertEqual(found.kind, TypeKind.OPTIONAL)

    def test_three_way_union_with_none(self) -> None:
        self.assertEqual(rendered("int | str | None"), "int | str | None")

    def test_nested_unions_are_flattened(self) -> None:
        self.assertEqual(rendered("Union[int, Union[str, bool]]"), "int | str | bool")


class CallableAnnotationTests(unittest.TestCase):
    def test_callable_with_parameters(self) -> None:
        self.assertEqual(rendered("Callable[[int, str], bool]"), "(int, str) -> bool")

    def test_callable_with_ellipsis(self) -> None:
        self.assertEqual(rendered("Callable[..., int]"), "() -> int")

    def test_bare_callable(self) -> None:
        self.assertEqual(annotation_to_type(parse_annotation("Callable")).kind, TypeKind.CALLABLE)

    def test_awaitable_exposes_its_result(self) -> None:
        self.assertEqual(rendered("Awaitable[int]"), "() -> int")


class ForwardReferenceTests(unittest.TestCase):
    def test_string_annotation_is_reparsed(self) -> None:
        found = annotation_to_type(parse_annotation('"Account"'))
        self.assertEqual(found.name, "Account")

    def test_string_generic_annotation_is_reparsed(self) -> None:
        self.assertEqual(rendered('"list[int]"'), "list[int]")

    def test_unparseable_string_keeps_its_text(self) -> None:
        found = annotation_to_type(parse_annotation('"not valid ["'))
        self.assertEqual(found.kind, TypeKind.UNKNOWN)
        self.assertEqual(found.source_text, "not valid [")


class UnsupportedAnnotationTests(unittest.TestCase):
    def test_unrecognised_annotation_keeps_its_source_text(self) -> None:
        found = annotation_to_type(parse_annotation("x[1 + 2]"))
        self.assertEqual(found.kind, TypeKind.OBJECT)
        self.assertEqual(found.name, "x")

    def test_literal_annotation_is_unknown_but_recorded(self) -> None:
        found = annotation_to_type(parse_annotation("3"))
        self.assertEqual(found.kind, TypeKind.UNKNOWN)
        self.assertEqual(found.source_text, "3")


class LiteralInferenceTests(unittest.TestCase):
    def test_bool_is_not_confused_with_int(self) -> None:
        self.assertEqual(str(infer_literal_type(True)), "bool")
        self.assertEqual(str(infer_literal_type(1)), "int")

    def test_other_literals(self) -> None:
        self.assertEqual(str(infer_literal_type(1.5)), "float")
        self.assertEqual(str(infer_literal_type("a")), "str")
        self.assertEqual(str(infer_literal_type(b"a")), "bytes")
        self.assertEqual(infer_literal_type(None).kind, TypeKind.NONE)

    def test_unknown_value_records_its_python_type(self) -> None:
        found = infer_literal_type(complex(1, 2))
        self.assertEqual(found.kind, TypeKind.UNKNOWN)
        self.assertEqual(found.source_text, "complex")


if __name__ == "__main__":
    unittest.main()
