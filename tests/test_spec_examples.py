"""spec-examples: a spec's own example that violates its schema is
a self-inconsistent spec, and must FAIL."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.spec_examples import run_spec_examples_check


def _spec(request_example, response_example):
    req_schema = {"type": "object",
                  "properties": {"name": {"type": "string"},
                                 "tag": {"type": "string"}},
                  "required": ["name"]}
    resp_schema = {"type": "object",
                   "properties": {"id": {"type": "integer"}},
                   "required": ["id"]}
    return {"paths": {"/pets": {"post": {
        "requestBody": {"content": {"application/json": {
            "schema": req_schema, "example": request_example}}},
        "responses": {"201": {"content": {"application/json": {
            "schema": resp_schema, "example": response_example}}}}}}}}


class TestQueryAndAdditionalOperations(unittest.TestCase):
    def _qspec(self, ops):
        return {"openapi": "3.2.0", "paths": {"/search": ops}}

    def test_nonstandard_operation_examples_checked(self):
        # QUERY and additionalOperations are both iterated as
        # operations; one test covers both kinds, violating and valid.
        spec = self._qspec({"query": {"responses": {
            "200": {"content": {"application/json": {
                "schema": {"type": "object",
                           "properties": {"id": {"type": "integer"}},
                           "required": ["id"]},
                "example": {"id": "not-an-int"}}}}}}})
        v = run_spec_examples_check(spec)
        self.assertEqual(v.status, "FAIL", v)
        self.assertTrue(any("id" in p for p in v.evidence["problems"]))
        spec = self._qspec({"additionalOperations": {
            "COPY": {"responses": {
                "200": {"content": {"application/json": {
                    "schema": {"type": "object",
                               "properties": {"id": {"type": "integer"}},
                               "required": ["id"]},
                    "example": {"id": 7}}}}}}}})
        v = run_spec_examples_check(spec)
        self.assertEqual(v.status, "PASS", v)
        self.assertEqual(v.evidence["examples_checked"], 1)



class TestSpecExamples(unittest.TestCase):
    def test_valid_examples_pass(self):
        verdict = run_spec_examples_check(
            _spec({"name": "rex"}, {"id": 7}))
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["examples_checked"], 2)

    def test_wrong_type_example_fails(self):
        verdict = run_spec_examples_check(
            _spec({"name": 123}, {"id": 7}))
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any("requestBody" in p
                            for p in verdict.evidence["problems"]))

    def test_missing_required_example_fails(self):
        verdict = run_spec_examples_check(
            _spec({"name": "rex"}, {"other": 1}))
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any("id" in p
                            for p in verdict.evidence["problems"]))

    def test_no_examples_passes_with_zero_checked(self):
        schema = {"type": "object",
                  "properties": {"id": {"type": "integer"}}}
        media = {"application/json": {"schema": schema}}
        spec = {"paths": {"/pets": {"get": {"responses": {
            "200": {"content": media}}}}}}
        verdict = run_spec_examples_check(spec)
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["examples_checked"], 0)

    def test_named_examples_entries_checked(self):
        spec = {"paths": {"/pets": {"post": {
            "requestBody": {"content": {"application/json": {
                "schema": {"type": "object",
                           "properties": {"name": {"type": "string"}}},
                "examples": {
                    "good": {"value": {"name": "rex"}},
                    "bad": {"value": {"name": 5}}}}}},
            "responses": {"201": {"description": "created"}}}}}}
        verdict = run_spec_examples_check(spec)
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertEqual(verdict.evidence["examples_checked"], 2)


if __name__ == "__main__":
    unittest.main()


class TestParamExamples(unittest.TestCase):
    def _spec(self, example):
        return {"openapi": "3.0.0", "paths": {"/api/items": {"get": {
            "parameters": [{"name": "limit", "in": "query",
                            "schema": {"type": "integer", "minimum": 1},
                            "example": example}],
            "responses": {"200": {"description": "ok"}}}}}}

    def test_matching_param_example_passes(self):
        verdict = run_spec_examples_check(self._spec(10))
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["examples_checked"], 1)

    def test_violating_param_example_fails(self):
        verdict = run_spec_examples_check(self._spec(0))
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any("minimum" in p for p in verdict.evidence["problems"]))


class TestRemoteRefExamples(unittest.TestCase):
    def test_remote_ref_example_is_skipped_not_checked(self):
        spec = {"openapi": "3.0.0", "paths": {"/api/item": {"post": {
            "requestBody": {"content": {"application/json": {
                "schema": {"$ref": "https://example.com/schemas/Item.json"},
                "example": {"id": "not-an-integer"}}}},
            "responses": {"200": {"description": "ok"}}}}}}
        verdict = run_spec_examples_check(spec)
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["examples_checked"], 0)
        self.assertEqual(verdict.evidence["skipped_remote_ref"], 1)


class TestRefExamples(unittest.TestCase):
    def test_ref_example_is_judged(self):
        from apiverdict.spec_examples import run_spec_examples_check
        spec = {
            "openapi": "3.0.0",
            "components": {"examples": {"BadItem": {
                "value": {"name": 123}}}},
            "paths": {"/items": {"post": {
                "requestBody": {"content": {"application/json": {
                    "schema": {"type": "object", "required": ["name"],
                               "properties": {"name": {"type": "string"}}},
                    "examples": {"bad": {"$ref": "#/components/examples/BadItem"}}}}},
                "responses": {"200": {"description": "ok"}}}}},
        }
        v = run_spec_examples_check(spec)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertEqual(v.evidence["examples_checked"], 1)

    def test_external_value_example_is_skipped_not_judged(self):
        from apiverdict.spec_examples import run_spec_examples_check
        spec = {
            "openapi": "3.0.0",
            "paths": {"/items": {"post": {
                "requestBody": {"content": {"application/json": {
                    "schema": {"type": "object"},
                    "examples": {"ext": {"externalValue": "https://example.com/ex.json"}}}}},
                "responses": {"200": {"description": "ok"}}}}},
        }
        v = run_spec_examples_check(spec)
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertEqual(v.evidence["examples_checked"], 0)
        self.assertEqual(v.evidence["skipped_remote_ref"], 1)


class TestPathItemRefExamples(unittest.TestCase):
    def test_ref_path_item_example_is_judged(self):
        from apiverdict.spec_examples import run_spec_examples_check
        spec = {
            "openapi": "3.1.0",
            "components": {"pathitems": {"Items": {
                "post": {
                    "requestBody": {"content": {"application/json": {
                        "schema": {"type": "object", "required": ["name"],
                                   "properties": {"name": {"type": "string"}}},
                        "example": {"name": 123}}}},
                    "responses": {"200": {"description": "ok"}}}}}},
            "paths": {"/items": {"$ref": "#/components/pathitems/Items"}},
        }
        v = run_spec_examples_check(spec)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertEqual(v.evidence["examples_checked"], 1)
