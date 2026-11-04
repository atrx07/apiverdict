"""Spec-conformance scalar bounds: pattern, numeric bounds,
length/item/property counts — without full JSON Schema."""
import unittest

from apiverdict.spec_conformance import _check


def problems_for(value, schema):
    problems = []
    _check(value, schema, "root", problems)
    return problems


class TestSpecBounds(unittest.TestCase):
    def test_pattern_and_lengths(self):
        self.assertEqual(problems_for("abc-123", {"type": "string", "pattern": r"^[a-z]+-\d+$"}), [])
        bad = problems_for("ABC", {"type": "string", "pattern": r"^[a-z]+$"})
        self.assertTrue(any("pattern" in p for p in bad), bad)
        self.assertTrue(any("minLength" in p for p in
                            problems_for("", {"type": "string", "minLength": 1})))
        self.assertTrue(any("maxLength" in p for p in
                            problems_for("toolong", {"type": "string", "maxLength": 3})))

    def test_numeric_bounds_both_exclusive_forms(self):
        self.assertEqual(problems_for(5, {"type": "integer", "minimum": 0, "maximum": 10}), [])
        self.assertTrue(any("minimum" in p for p in
                            problems_for(-1, {"type": "number", "minimum": 0})))
        self.assertTrue(any("exclusiveMinimum" in p for p in
                            problems_for(0, {"type": "integer", "minimum": 0,
                                             "exclusiveMinimum": True})))
        self.assertTrue(any("exclusiveMinimum" in p for p in
                            problems_for(0, {"type": "integer",
                                             "exclusiveMinimum": 0})))
        self.assertTrue(any("maximum" in p for p in
                            problems_for(11, {"type": "integer", "maximum": 10})))

    def test_items_and_properties(self):
        self.assertTrue(any("minItems" in p for p in
                            problems_for([], {"type": "array", "minItems": 1})))
        self.assertTrue(any("maxItems" in p for p in
                            problems_for([1, 2, 3], {"type": "array", "maxItems": 2})))
        self.assertTrue(any("minProperties" in p for p in
                            problems_for({}, {"type": "object", "minProperties": 1})))

    def test_bad_pattern_judges_nothing(self):
        self.assertEqual(problems_for("anything", {"type": "string", "pattern": "["}), [])

    def test_bool_is_not_a_number_for_bounds(self):
        self.assertEqual(problems_for(True, {"type": "boolean", "minimum": 0}), [])


if __name__ == "__main__":
    unittest.main()


class TestDependentRequired(unittest.TestCase):
    SCHEMA = {"type": "object",
              "properties": {"credit_card": {"type": "string"},
                             "billing_zip": {"type": "string"},
                             "name": {"type": "string"}},
              "dependentRequired": {"credit_card": ["billing_zip"]}}

    def test_missing_dependent_fails(self):
        problems = problems_for({"credit_card": "4111"}, self.SCHEMA)
        self.assertEqual(len(problems), 1)
        self.assertIn("dependent required", problems[0])
        self.assertIn("billing_zip", problems[0])

    def test_dependent_present_passes(self):
        self.assertEqual(problems_for(
            {"credit_card": "4111", "billing_zip": "90210"},
            self.SCHEMA), [])

    def test_trigger_absent_passes(self):
        self.assertEqual(problems_for({"name": "x"}, self.SCHEMA), [])

    def test_nested_dependent_required(self):
        schema = {"type": "object",
                  "properties": {"payment": self.SCHEMA}}
        problems = problems_for({"payment": {"credit_card": "4111"}}, schema)
        self.assertEqual(len(problems), 1)
        self.assertIn("billing_zip", problems[0])



    def _run(self, body):
        import apiverdict.spec_conformance as mod
        spec = {"openapi": "3.0.0",
                "paths": {"/tags": {"get": {"responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True}}}}}}}}}}}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, body, {})
        try:
            return mod.run_spec_conformance_check(spec, "http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_duplicate_tags_fail(self):
        v = self._run({"tags": ["a", "b", "a"]})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("uniqueItems", v.summary)

    def test_unique_tags_pass(self):
        v = self._run({"tags": ["a", "b"]})
        self.assertEqual(v.status, "PASS", v.summary)


class TestConst(unittest.TestCase):
    def _run(self, body):
        import apiverdict.spec_conformance as mod
        spec = {"openapi": "3.0.0",
                "paths": {"/s": {"get": {"responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"kind": {
                            "type": "string", "const": "user"}}}}}}}}}}}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, body, {})
        try:
            return mod.run_spec_conformance_check(spec, "http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_const_match_passes(self):
        v = self._run({"kind": "user"})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_const_mismatch_fails(self):
        v = self._run({"kind": "admin"})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("const", v.summary)


class TestPrefixItems(unittest.TestCase):
    def _run(self, body):
        import apiverdict.spec_conformance as mod
        spec = {"openapi": "3.1.0",
                "paths": {"/coord": {"get": {"responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"point": {
                            "prefixItems": [{"type": "number"},
                                            {"type": "number"}]}}}}}}}}}}}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, body, {})
        try:
            return mod.run_spec_conformance_check(spec, "http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_prefix_match_passes(self):
        v = self._run({"point": [12.5, 77.1]})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_prefix_mismatch_fails(self):
        v = self._run({"point": [12.5, "north"]})
        self.assertEqual(v.status, "FAIL", v.summary)

    def test_items_applies_after_prefix_only(self):
        import apiverdict.spec_conformance as mod
        # prefix says [string], items says integer: element 0
        # must NOT be judged against items (else "a" would
        # false-FAIL), element 1 must be.
        spec = {"openapi": "3.1.0",
                "paths": {"/t": {"get": {"responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"pair": {
                            "prefixItems": [{"type": "string"}],
                            "items": {"type": "integer"}}}}}}}}}}}}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, {"pair": ["a", 2]}, {})
        try:
            v = mod.run_spec_conformance_check(spec, "http://127.0.0.1:9")
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "PASS", v.summary)

