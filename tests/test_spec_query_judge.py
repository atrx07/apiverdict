"""spec-conformance judges 3.2 QUERY operations when a valid request
body can be built from the requestBody schema. A non-200 for that
schema-derived body is unjudged, never a FAIL — a rejection of
invented data proves nothing. No buildable body -> unjudged."""
import copy
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.spec_conformance import run_spec_conformance_check

SPEC = {
    "openapi": "3.2.0",
    "paths": {
        "/search": {
            "query": {
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"},
                                   "limit": {"type": "integer"}},
                    "required": ["q"]}}}},
                "responses": {"200": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {"results": {"type": "array"},
                                   "total": {"type": "integer"}},
                    "required": ["results", "total"]}}}}},
            },
        },
        "/items": {
            "get": {
                "responses": {"200": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"]}}}}},
            },
        },
    },
}


class _Server:
    """Throwaway server; mode controls what QUERY returns."""

    def __init__(self, mode):
        self.mode = mode
        self.last_query_body = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_QUERY(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    outer.last_query_body = json.loads(raw.decode())
                except ValueError:
                    outer.last_query_body = None
                if outer.mode == "bad":
                    self._send(200, {"results": [], "total": "0"})
                elif outer.mode == "reject":
                    self._send(400, {"error": "bad query"})
                else:
                    self._send(200, {"results": [], "total": 0})

            def do_GET(self):  # noqa: N802
                self._send(200, {"id": 1})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TestQueryJudged(unittest.TestCase):
    def test_query_passes_and_body_is_built_from_schema(self):
        srv = _Server("good")
        try:
            verdict = run_spec_conformance_check(copy.deepcopy(SPEC), srv.base)
        finally:
            srv.close()
        self.assertEqual(verdict.status, "PASS", verdict.summary)
        self.assertEqual(srv.last_query_body, {"q": "x", "limit": 1})
        self.assertIn(f"{srv.base}/search",
                      verdict.evidence["checked_endpoints"])

    def test_query_wrong_type_fails(self):
        srv = _Server("bad")
        try:
            verdict = run_spec_conformance_check(copy.deepcopy(SPEC), srv.base)
        finally:
            srv.close()
        self.assertEqual(verdict.status, "FAIL", verdict.summary)
        self.assertIn("total", verdict.summary)

    def test_query_rejection_is_unjudged_not_fail(self):
        srv = _Server("reject")
        try:
            verdict = run_spec_conformance_check(copy.deepcopy(SPEC), srv.base)
        finally:
            srv.close()
        # GET still passes, so the check passes with the QUERY listed
        # as unjudged — a rejection of invented data proves nothing.
        self.assertEqual(verdict.status, "PASS", verdict.summary)
        unjudged = verdict.evidence["unjudged"]
        self.assertEqual(len(unjudged), 1)
        self.assertIn("HTTP 400", unjudged[0]["reason"])


class TestQueryUnjudgedWithoutBody(unittest.TestCase):
    def test_query_without_request_body_is_unjudged(self):
        spec = {"openapi": "3.2.0", "paths": {
            "/search": {"query": {"responses": {"200": {"content": {
                "application/json": {"schema": {
                    "type": "object",
                    "properties": {"results": {"type": "array"}},
                    "required": ["results"]}}}}}}}}}
        srv = _Server("good")
        try:
            verdict = run_spec_conformance_check(spec, srv.base)
        finally:
            srv.close()
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("needs a request body",
                      verdict.evidence["unjudged"][0]["reason"])

    def test_query_with_empty_object_schema_is_unjudged(self):
        spec = copy.deepcopy(SPEC)
        spec["paths"] = {"/search": copy.deepcopy(SPEC["paths"]["/search"])}
        spec["paths"]["/search"]["query"]["requestBody"]["content"][
            "application/json"]["schema"] = {"type": "object"}
        srv = _Server("good")
        try:
            verdict = run_spec_conformance_check(spec, srv.base)
        finally:
            srv.close()
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("needs a request body",
                      verdict.evidence["unjudged"][0]["reason"])


class TestSkippedCountReported(unittest.TestCase):
    """Regression: the GET judging refactor once passed `skipped`
    (an int) into a helper that incremented it locally, so the
    caller's count never moved and evidence["skipped"] always
    read 0. The helpers return the new count now."""

    def test_skipped_endpoint_counted_in_evidence(self):
        spec = {
            "openapi": "3.2.0",
            "paths": {
                "/items": {"get": {"responses": {"200": {"content": {
                    "application/json": {"schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                        "required": ["id"]}}}}}}},
                "/empty": {"get": {"responses": {
                    "200": {"description": "ok"}}}},
            },
        }
        srv = _Server("good")
        try:
            verdict = run_spec_conformance_check(spec, srv.base)
        finally:
            srv.close()
        self.assertEqual(verdict.status, "PASS", verdict.summary)
        self.assertEqual(verdict.evidence["skipped"], 1)
        self.assertEqual(len(verdict.evidence["checked_endpoints"]), 1)


class TestQueryRefBodies(unittest.TestCase):
    def test_query_with_ref_request_body_and_ref_schema(self):
        spec = {
            "openapi": "3.2.0",
            "components": {
                "requestBodies": {"Search": {"content": {"application/json": {
                    "schema": {"type": "object",
                               "properties": {"q": {"type": "string"}},
                               "required": ["q"]}}}}},
                "schemas": {"SearchResult": {"type": "object",
                                             "properties": {
                                                 "results": {"type": "array"},
                                                 "total": {"type": "integer"}},
                                             "required": ["results", "total"]}},
            },
            "paths": {
                "/search": {"query": {
                    "requestBody": {"$ref": "#/components/requestBodies/Search"},
                    "responses": {"200": {"content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/SearchResult"}}}}}}},
            },
        }
        srv = _Server("good")
        try:
            verdict = run_spec_conformance_check(spec, srv.base)
        finally:
            srv.close()
        self.assertEqual(verdict.status, "PASS", verdict.summary)
        self.assertEqual(srv.last_query_body, {"q": "x"})
        self.assertIn(f"{srv.base}/search",
                      verdict.evidence["checked_endpoints"])


if __name__ == "__main__":
    unittest.main()
