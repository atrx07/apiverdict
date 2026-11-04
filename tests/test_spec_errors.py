"""Spec error-documentation: every operation documents an error
response, or it is listed. Static check, no network."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.spec_errors import run_spec_errors_check


def _spec(ops):
    return {"openapi": "3.0.3", "paths": ops}


class TestSpecErrors(unittest.TestCase):
    def test_all_documented_passes(self):
        spec = _spec({"/a": {"get": {"responses": {
            "200": {"description": "ok"},
            "404": {"description": "not found"}}}}})
        verdict = run_spec_errors_check(spec)
        self.assertEqual(verdict.status, "PASS")

    def test_default_counts_as_error_response(self):
        spec = _spec({"/a": {"get": {"responses": {
            "200": {"description": "ok"},
            "default": {"description": "error"}}}}})
        self.assertEqual(run_spec_errors_check(spec).status, "PASS")

    def test_undocumented_operation_listed_and_fails(self):
        spec = _spec({
            "/a": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/b": {"post": {"responses": {
                "201": {"description": "created"},
                "400": {"description": "bad"}}}},
        })
        verdict = run_spec_errors_check(spec)
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(verdict.evidence["undocumented"], ["GET /a"])
        self.assertIn("GET /a", verdict.summary)

    def test_nonstandard_operations_without_error_response_fail(self):
        # QUERY and additionalOperations are both iterated as
        # operations; one spec listing both covers the branch.
        spec = _spec({"/search": {"query": {"responses": {
            "200": {"description": "ok"}}}},
            "/files": {"additionalOperations": {
                "COPY": {"responses": {"200": {"description": "ok"}}}}}})
        verdict = run_spec_errors_check(spec)
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(set(verdict.evidence["undocumented"]),
                         {"QUERY /search", "COPY /files"})

    def test_no_operations_is_inconclusive(self):
        verdict = run_spec_errors_check({"openapi": "3.0.3", "paths": {}})
        self.assertEqual(verdict.status, "INCONCLUSIVE")

    def test_non_operation_keys_ignored(self):
        spec = _spec({"/a": {"parameters": [],
                             "get": {"responses": {"200": {"description": "ok"},
                                                   "500": {"description": "err"}}}}})
        self.assertEqual(run_spec_errors_check(spec).status, "PASS")


if __name__ == "__main__":
    unittest.main()


class TestPathItemRef(unittest.TestCase):
    def test_ref_path_item_is_judged(self):
        from apiverdict.spec_errors import run_spec_errors_check
        spec = {
            "openapi": "3.1.0",
            "components": {"pathitems": {"Items": {
                "get": {"responses": {
                    "200": {"description": "ok"},
                    "400": {"description": "bad"}}}}}},
            "paths": {"/items": {"$ref": "#/components/pathitems/Items"}},
        }
        v = run_spec_errors_check(spec)
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertEqual(v.evidence["operations"], 1)

    def test_ref_path_item_without_error_doc_fails(self):
        from apiverdict.spec_errors import run_spec_errors_check
        spec = {
            "openapi": "3.1.0",
            "components": {"pathitems": {"Items": {
                "get": {"responses": {
                    "200": {"description": "ok"}}}}}},
            "paths": {"/items": {"$ref": "#/components/pathitems/Items"}},
        }
        v = run_spec_errors_check(spec)
        self.assertEqual(v.status, "FAIL")
