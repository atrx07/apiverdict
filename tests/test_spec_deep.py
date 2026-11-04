"""Deeper spec-conformance: $ref, nested objects, arrays, path params.

These are the v2-continuation improvements over the shallow v2
checker (top-level required + primitive types only).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.spec_conformance import run_spec_conformance_check
from apiverdict.spec_util import fill_path_params, resolve_ref
from fixture_util import FixtureServer


def _item_spec(detail_count_type="integer", detail_code_enum=None):
    detail = {"type": "object",
              "properties": {"code": {"type": "string"},
                             "count": {"type": detail_count_type}}}
    if detail_code_enum is not None:
        detail["properties"]["code"]["enum"] = detail_code_enum
    schema = {
        "type": "object",
        "required": ["id", "name", "detail"],
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "detail": {"$ref": "#/components/schemas/Detail"},
        },
    }
    operation = {
        "parameters": [
            {"name": "itemId", "in": "path", "required": True,
             "schema": {"type": "integer", "example": 3}}],
        "responses": {"200": {"content": {"application/json": {"schema": schema}}}},
    }
    return {
        "openapi": "3.0.3",
        "components": {"schemas": {"Detail": detail}},
        "paths": {"/api/item/{itemId}": {"get": operation}},
    }


class TestSpecUtil(unittest.TestCase):
    def test_ref_resolves_locally(self):
        spec = {"components": {"schemas": {"X": {"type": "object",
                                                 "properties": {"a": {"type": "string"}}}}}}
        out = resolve_ref({"$ref": "#/components/schemas/X"}, spec)
        self.assertEqual(out["properties"]["a"]["type"], "string")

    def test_ref_cycle_does_not_loop(self):
        spec = {"components": {"schemas": {"X": {"$ref": "#/components/schemas/X"}}}}
        out = resolve_ref({"$ref": "#/components/schemas/X"}, spec)
        self.assertIsInstance(out, dict)

    def test_remote_ref_not_fetched(self):
        out = resolve_ref({"$ref": "https://example.invalid/schema.json"}, {})
        self.assertEqual(out["$ref"], "https://example.invalid/schema.json")

    def test_fill_path_prefers_example(self):
        self.assertEqual(
            fill_path_params("/users/{userId}",
                             [{"name": "userId", "example": 42}]),
            "/users/42")

    def test_fill_path_defaults_by_type(self):
        self.assertEqual(
            fill_path_params("/users/{userId}",
                             [{"name": "userId", "schema": {"type": "integer"}}]),
            "/users/1")


class TestDeepSpecConformance(unittest.TestCase):
    def test_ref_and_nested_pass(self):
        with FixtureServer("fixed") as base:
            v = run_spec_conformance_check(_item_spec(), base)
        self.assertEqual(v.status, "PASS")
        self.assertIn("/api/item/3", v.evidence["filled_paths"])

    def test_nested_wrong_type_fails(self):
        with FixtureServer("fixed") as base:
            v = run_spec_conformance_check(_item_spec(detail_count_type="string"), base)
        self.assertEqual(v.status, "FAIL")
        self.assertIn("detail.count", v.summary)

    def test_enum_violation_fails(self):
        with FixtureServer("fixed") as base:
            v = run_spec_conformance_check(_item_spec(detail_code_enum=["NOPE"]), base)
        self.assertEqual(v.status, "FAIL")
        self.assertIn("detail.code", v.summary)

    def test_array_schema_checked(self):
        spec = {"openapi": "3.0.3", "paths": {"/api/items": {"get": {
            "responses": {"200": {"content": {"application/json": {"schema": {
                "type": "array",
                "items": {"type": "object",
                          "required": ["id"],
                          "properties": {"id": {"type": "integer"}}}}}}}}}}}}
        with FixtureServer("fixed") as base:
            # /api/items without paging params returns a dict, not a
            # list — so this must FAIL on shape, proving array schemas
            # are actually judged, not skipped.
            v = run_spec_conformance_check(spec, base)
        self.assertEqual(v.status, "FAIL")


class TestAdditionalProperties(unittest.TestCase):
    def test_extra_values_checked_against_additional_schema(self):
        from apiverdict.spec_conformance import _check
        problems = []
        _check({"available": 3, "pending": "not-a-count"},
               {"type": "object",
                "additionalProperties": {"type": "integer"}},
               "inventory", problems)
        self.assertEqual(len(problems), 1)
        self.assertIn("pending", problems[0])

    def test_declared_property_not_double_checked_as_additional(self):
        from apiverdict.spec_conformance import _check
        problems = []
        _check({"name": "x"},
               {"type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": {"type": "integer"}},
               "obj", problems)
        self.assertEqual(problems, [])

    def test_additional_false_flags_undeclared_key(self):
        from apiverdict.spec_conformance import _check
        problems = []
        _check({"name": "x", "surprise": 1},
               {"type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": False},
               "obj", problems)
        self.assertEqual(len(problems), 1)
        self.assertIn("surprise", problems[0])


if __name__ == "__main__":
    unittest.main()
