"""Spec-conformance: required query params are filled, and one
unjudged endpoint no longer aborts the whole check (Petstore lesson:
findByStatus without ?status=... returned 400 and killed the run)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.spec_conformance import run_spec_conformance_check
from apiverdict.spec_util import fill_query_params
from fixture_util import FixtureServer


def _items_spec(paths):
    return {"openapi": "3.0.3", "paths": paths}


_ITEMS_SCHEMA = {
    "type": "object", "required": ["items", "total"],
    "properties": {"items": {"type": "array"},
                   "total": {"type": "integer"}},
}
# The 404 is documented on purpose: since the undocumented-status
# rule, a 404 the spec never mentions would FAIL, not sit unjudged.
# These tests pin the "documented error does not abort the run"
# behaviour; the undocumented case is pinned in
# test_spec_conformance.TestUndocumentedStatus.
_ITEMS_OP = {"responses": {"200": {"content": {"application/json": {
    "schema": _ITEMS_SCHEMA}}},
    "404": {"description": "not found"}}}


class TestQueryFilling(unittest.TestCase):
    def test_required_query_param_filled_from_default(self):
        params = [{"name": "status", "in": "query", "required": True,
                   "schema": {"type": "string", "default": "available",
                              "enum": ["available", "pending", "sold"]}}]
        self.assertEqual(fill_query_params(params), {"status": "available"})

    def test_required_query_param_filled_from_enum(self):
        params = [{"name": "kind", "in": "query", "required": True,
                   "schema": {"type": "string", "enum": ["a", "b"]}}]
        self.assertEqual(fill_query_params(params), {"kind": "a"})

    def test_optional_param_without_hint_not_invented(self):
        params = [{"name": "q", "in": "query", "required": False,
                   "schema": {"type": "string"}}]
        self.assertEqual(fill_query_params(params), {})

    def test_path_param_not_treated_as_query(self):
        params = [{"name": "petId", "in": "path", "required": True,
                   "schema": {"type": "integer"}}]
        self.assertEqual(fill_query_params(params), {})


class TestUnjudgedContinues(unittest.TestCase):
    def test_one_404_does_not_abort_other_endpoints(self):
        spec = _items_spec({
            "/api/items": {"get": _ITEMS_OP},
            "/api/missing": {"get": _ITEMS_OP},
        })
        with FixtureServer("fixed") as base:
            verdict = run_spec_conformance_check(spec, base)
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(len(verdict.evidence["unjudged"]), 1)
        self.assertIn("missing", verdict.evidence["unjudged"][0]["url"])

    def test_nothing_judged_is_inconclusive(self):
        spec = _items_spec({"/api/missing": {"get": _ITEMS_OP}})
        with FixtureServer("fixed") as base:
            verdict = run_spec_conformance_check(spec, base)
        self.assertEqual(verdict.status, "INCONCLUSIVE")

    def test_required_query_param_reaches_checked_url(self):
        spec = _items_spec({
            "/api/items": {"get": {
                "parameters": [{"name": "status", "in": "query",
                                "required": True,
                                "schema": {"type": "string",
                                           "default": "available"}}],
                "responses": _ITEMS_OP["responses"]}},
        })
        with FixtureServer("fixed") as base:
            verdict = run_spec_conformance_check(spec, base)
        self.assertEqual(verdict.status, "PASS")
        self.assertTrue(any("status=available" in u
                            for u in verdict.evidence["checked_endpoints"]))


if __name__ == "__main__":
    unittest.main()


class TestRefParams(unittest.TestCase):
    SPEC = {"components": {"parameters": {
        "UserId": {"name": "userId", "in": "path", "required": True,
                   "schema": {"type": "integer"}},
        "Status": {"name": "status", "in": "query", "required": True,
                   "schema": {"type": "string", "enum": ["available"]}}}}}

    def test_ref_path_param_fills(self):
        from apiverdict.spec_util import fill_path_params
        filled = fill_path_params(
            "/pet/{userId}",
            [{"$ref": "#/components/parameters/UserId"}], self.SPEC)
        self.assertEqual(filled, "/pet/1")

    def test_ref_query_param_fills(self):
        from apiverdict.spec_util import fill_query_params
        filled = fill_query_params(
            [{"$ref": "#/components/parameters/Status"}], self.SPEC)
        self.assertEqual(filled, {"status": "available"})

    def test_ref_paging_params_generate_plan(self):
        from apiverdict.openapi import generate_plan
        spec = {"openapi": "3.0.0",
                "components": {"parameters": {
                    "Page": {"name": "_page", "in": "query"},
                    "Limit": {"name": "_limit", "in": "query"}}},
                "paths": {"/posts": {"get": {
                    "parameters": [
                        {"$ref": "#/components/parameters/Page"},
                        {"$ref": "#/components/parameters/Limit"}],
                    "responses": {"200": {"description": "ok"}}}}}}
        cands = generate_plan(spec, "http://x")["pagination"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["style"], "page")
        self.assertEqual(cands[0]["page_param"], "_page")
