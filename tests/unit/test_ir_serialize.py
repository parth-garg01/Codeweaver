from __future__ import annotations

import json
import unittest

from codeweaver.errors import IRSchemaError
from codeweaver.ir import (
    IR_SCHEMA_VERSION,
    IRBlock,
    IRFunction,
    IRLiteral,
    IRModule,
    IRName,
    IRParam,
    IRReturn,
    dumps,
    from_envelope,
    loads,
    to_envelope,
)
from codeweaver.ir.serialize import ENVELOPE_KIND
from codeweaver.ir.types import t_int


def sample_module() -> IRModule:
    return IRModule(
        name="pkg.math",
        path="pkg/math.py",
        source_language="python",
        functions=[
            IRFunction(
                "add",
                params=[IRParam("a", t_int()), IRParam("b", t_int())],
                return_type=t_int(),
                body=IRBlock([IRReturn(IRName("a"))]),
            )
        ],
    )


class EnvelopeTests(unittest.TestCase):
    def test_envelope_names_kind_version_and_producer(self) -> None:
        envelope = to_envelope(IRLiteral(1))
        self.assertEqual(envelope["envelope"], ENVELOPE_KIND)
        self.assertEqual(envelope["schemaVersion"], IR_SCHEMA_VERSION)
        self.assertTrue(envelope["producer"].startswith("codeweaver/"))

    def test_extra_metadata_is_carried(self) -> None:
        envelope = to_envelope(IRLiteral(1), sourceSha256="abc")
        self.assertEqual(envelope["sourceSha256"], "abc")

    def test_round_trips_a_module(self) -> None:
        module = sample_module()
        self.assertEqual(from_envelope(to_envelope(module)), module)

    def test_rejects_a_foreign_document(self) -> None:
        with self.assertRaises(IRSchemaError) as ctx:
            from_envelope({"envelope": "something.else", "root": {}})
        self.assertIn("not a CodeWeaver IR document", str(ctx.exception))

    def test_rejects_a_non_object_envelope(self) -> None:
        with self.assertRaises(IRSchemaError):
            from_envelope([])  # type: ignore[arg-type]

    def test_rejects_an_incompatible_schema_version(self) -> None:
        envelope = to_envelope(IRLiteral(1))
        envelope["schemaVersion"] = "9.0.0"
        with self.assertRaises(IRSchemaError) as ctx:
            from_envelope(envelope)
        self.assertIn("cannot be read by this build", str(ctx.exception))

    def test_rejects_a_missing_root(self) -> None:
        envelope = to_envelope(IRLiteral(1))
        del envelope["root"]
        with self.assertRaises(IRSchemaError) as ctx:
            from_envelope(envelope)
        self.assertIn("missing its 'root' node", str(ctx.exception))


class TextSerialisationTests(unittest.TestCase):
    def test_dumps_then_loads_is_identity(self) -> None:
        module = sample_module()
        self.assertEqual(loads(dumps(module)), module)

    def test_output_is_deterministic(self) -> None:
        module = sample_module()
        self.assertEqual(dumps(module), dumps(sample_module()))
        self.assertEqual(json.loads(dumps(module)), json.loads(dumps(module)))

    def test_keys_are_sorted(self) -> None:
        text = dumps(IRLiteral(1), indent=None)
        self.assertLess(text.index('"envelope"'), text.index('"producer"'))
        self.assertLess(text.index('"producer"'), text.index('"root"'))

    def test_non_ascii_identifiers_survive(self) -> None:
        module = IRModule(name="pkg.café", path="pkg/café.py")
        self.assertEqual(loads(dumps(module)).name, "pkg.café")

    def test_invalid_json_is_reported_clearly(self) -> None:
        with self.assertRaises(IRSchemaError) as ctx:
            loads("{not json")
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_unknown_node_kind_is_reported(self) -> None:
        text = json.dumps(
            {
                "envelope": ENVELOPE_KIND,
                "schemaVersion": IR_SCHEMA_VERSION,
                "root": {"kind": "no_such_node"},
            }
        )
        with self.assertRaises(IRSchemaError) as ctx:
            loads(text)
        self.assertIn("unknown IR node kind", str(ctx.exception))

    def test_unencodable_values_are_marked_not_dropped(self) -> None:
        payload = json.loads(dumps(IRLiteral(value=object())))
        self.assertIn("unencodable", payload["root"]["value"])


if __name__ == "__main__":
    unittest.main()
