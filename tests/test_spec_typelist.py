"""OpenAPI 3.1 type lists: a field typed ["string", "null"] used to
pass vacuously — _TYPE_CHECKS.get(list) would have crashed, and
the list path never ran, so no type was ever checked. The value
must now match at least one listed type."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.spec_conformance import _check


def problems(value, schema):
    out = []
    _check(value, schema, "field", out)
    return out


class TestTypeLists(unittest.TestCase):
    def test_matching_member_passes(self):
        self.assertEqual(problems("x", {"type": ["string", "null"]}), [])
        self.assertEqual(problems(None, {"type": ["string", "null"]}), [])

    def test_no_member_match_fails(self):
        probs = problems(42, {"type": ["string", "null"]})
        self.assertEqual(len(probs), 1)
        self.assertIn("one of", probs[0])

    def test_null_type_alone(self):
        self.assertEqual(problems(None, {"type": "null"}), [])
        self.assertEqual(len(problems("x", {"type": "null"})), 1)

    def test_list_typed_object_still_checks_properties(self):
        schema = {"type": ["object", "null"],
                  "properties": {"a": {"type": "string"}}}
        self.assertEqual(problems({"a": "ok"}, schema), [])
        probs = problems({"a": 1}, schema)
        self.assertTrue(any("a" in p for p in probs))


if __name__ == "__main__":
    unittest.main()
