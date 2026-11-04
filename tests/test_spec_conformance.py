import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.casestudy import create_case_server
from apiverdict.error_shape import run_error_shape_check
from apiverdict.spec_conformance import run_spec_conformance_check
from fixture_util import FixtureServer


def _spec_for_items(required, total_type="integer"):
    return {"openapi": "3.0.3", "paths": {"/api/items": {"get": {
        "responses": {"200": {"content": {"application/json": {"schema": {
            "type": "object",
            "required": required,
            "properties": {
                "items": {"type": "array"},
                "total": {"type": total_type},
            }}}}}}}}}}


class TestSpecConformance(unittest.TestCase):
    def test_conforming_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_spec_conformance_check(
                _spec_for_items(["items", "total"]), base)
        self.assertEqual(verdict.status, "PASS")

    def test_missing_required_fails(self):
        with FixtureServer("fixed") as base:
            verdict = run_spec_conformance_check(
                _spec_for_items(["items", "total", "bogus"]), base)
        self.assertEqual(verdict.status, "FAIL")
        self.assertIn("bogus", verdict.summary)

    def test_wrong_type_fails(self):
        with FixtureServer("fixed") as base:
            verdict = run_spec_conformance_check(
                _spec_for_items(["items", "total"], total_type="string"), base)
        self.assertEqual(verdict.status, "FAIL")
        self.assertIn("total", verdict.summary)

    def test_no_usable_schema_is_inconclusive(self):
        with FixtureServer("fixed") as base:
            verdict = run_spec_conformance_check(
                {"openapi": "3.0.3", "paths": {"/api/items": {"get": {
                    "responses": {"200": {"description": "ok"}}}}}}, base)
        self.assertEqual(verdict.status, "INCONCLUSIVE")


class TestTwoXxContentType(unittest.TestCase):
    def _run(self, body, ctype):
        import apiverdict.spec_conformance as mod
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, body, {"Content-Type": ctype})
        spec = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
                "paths": {"/api/items": {"get": {"responses": {
                    "2XX": {"description": "ok", "content": {
                        "application/json": {"schema": {
                            "type": "object",
                            "properties": {"id": {"type": "integer"}},
                            "required": ["id"]}}}}}}}}}
        try:
            return mod.run_spec_conformance_check(spec=spec, base_url="http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_2xx_content_type_mismatch_fails(self):
        v = self._run({"id": 1}, "text/html")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("Content-Type", v.summary)


class TestTwoXxWildcard(unittest.TestCase):
    def _spec(self, required):
        return {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {"/api/items": {"get": {
                "responses": {"2XX": {"description": "ok", "content": {
                    "application/json": {"schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                        "required": required}}}}}}}},
        }

    def _run(self, spec, body):
        import apiverdict.spec_conformance as mod
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, body, {"Content-Type": "application/json"})
        try:
            return mod.run_spec_conformance_check(spec=spec, base_url="http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_2xx_only_conforming_passes(self):
        v = self._run(self._spec(["id"]), {"id": 1})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_2xx_only_violating_fails(self):
        v = self._run(self._spec(["id"]), {"name": "x"})
        self.assertEqual(v.status, "FAIL", v.summary)


class TestQueryOperationsUnjudged(unittest.TestCase):
    def test_query_only_spec_is_inconclusive_with_reason(self):
        spec = {"openapi": "3.2.0", "paths": {"/search": {"query": {
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}}}}}},
            "responses": {"200": {"description": "ok"}}}}}}
        v = run_spec_conformance_check(spec, "http://127.0.0.1:9")
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)
        self.assertEqual(len(v.evidence["unjudged"]), 1)
        self.assertIn("QUERY", v.evidence["unjudged"][0]["reason"])
        self.assertIn("1 unjudged", v.summary)

    def test_query_listed_alongside_judged_get(self):
        with FixtureServer("fixed") as base:
            spec = _spec_for_items(["items", "total"])
            spec["paths"]["/api/search"] = {"query": {
                "responses": {"200": {"description": "ok"}}}}
            v = run_spec_conformance_check(spec, base)
        self.assertEqual(v.status, "PASS", v.summary)
        urls = [u["url"] for u in v.evidence["unjudged"]]
        self.assertTrue(any(u.endswith("/api/search") for u in urls), urls)



    def test_plain_shape_fails_when_required(self):
        with FixtureServer("fixed") as base:
            verdict = run_error_shape_check(
                targets=[{"method": "POST", "url": f"{base}/api/book"},
                         {"method": "POST", "url": f"{base}/api/order"}],
                invalid_payload={"__invalid__": True}, require_rfc7807=True)
        self.assertEqual(verdict.status, "FAIL")

    def test_problem_details_pass_when_required(self):
        server = create_case_server("fixed")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            verdict = run_error_shape_check(
                targets=[{"method": "POST", "url": f"{base}/api/bookings"},
                         {"method": "POST", "url": f"{base}/api/payments"}],
                invalid_payload={"__invalid__": True}, require_rfc7807=True)
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "PASS")


if __name__ == "__main__":
    unittest.main()


class TestRefResponse(unittest.TestCase):
    def test_refd_response_schema_is_extracted(self):
        from apiverdict.spec_conformance import _schema_of
        spec = {"components": {"responses": {"Ok": {"content": {
            "application/json": {"schema": {
                "type": "object",
                "properties": {"id": {"type": "integer"}}}}}}}}}
        op = {"responses": {"200": {"$ref": "#/components/responses/Ok"}}}
        schema = _schema_of(op, spec)
        self.assertIsInstance(schema, dict)
        self.assertEqual(schema.get("type"), "object")


class TestUndocumentedStatus(unittest.TestCase):
    def _spec(self, responses):
        return {"openapi": "3.0.0",
                "paths": {"/item": {"get": {
                    "responses": responses}}}}

    def _run(self, spec, status):
        import apiverdict.spec_conformance as sc
        orig = sc.request_json
        sc.request_json = lambda *a, **kw: (status, {}, {})
        try:
            return sc.run_spec_conformance_check(
                spec, "http://127.0.0.1", timeout=5)
        finally:
            sc.request_json = orig

    def test_documented_404_stays_unjudged(self):
        spec = self._spec({"200": {"description": "ok", "content": {
            "application/json": {"schema": {"type": "object", "properties": {}}}}},
            "404": {"description": "not found"}})
        v = self._run(spec, 404)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertEqual(len(v.evidence["unjudged"]), 1)
        self.assertEqual(v.evidence["problems"], [])

    def test_undocumented_500_fails(self):
        spec = self._spec({"200": {"description": "ok", "content": {
            "application/json": {"schema": {"type": "object", "properties": {}}}}},
            "404": {"description": "not found"}})
        v = self._run(spec, 500)
        self.assertEqual(v.status, "FAIL")
        self.assertIn("not documented", v.summary)

    def test_default_response_covers_any_status(self):
        spec = self._spec({"200": {"description": "ok", "content": {
            "application/json": {"schema": {"type": "object", "properties": {}}}}},
            "default": {"description": "error"}})
        v = self._run(spec, 500)
        self.assertEqual(v.status, "INCONCLUSIVE")


class TestPathItemRefConformance(unittest.TestCase):
    def test_ref_path_item_is_judged(self):
        import apiverdict.spec_conformance as sc
        orig = sc.request_json
        sc.request_json = lambda *a, **kw: (200, {"name": 123}, {})
        try:
            spec = {
                "openapi": "3.1.0",
                "components": {"pathitems": {"Items": {
                    "get": {"responses": {"200": {"content": {
                        "application/json": {"schema": {
                            "type": "object", "required": ["name"],
                            "properties": {"name": {"type": "string"}}}}}}}}}}},
                "paths": {"/items": {"$ref": "#/components/pathitems/Items"}},
            }
            v = sc.run_spec_conformance_check(spec, "http://127.0.0.1")
        finally:
            sc.request_json = orig
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertEqual(len(v.evidence["checked_endpoints"]), 1)


class TestContentTypeRule(unittest.TestCase):
    def _run(self, content_type):
        import apiverdict.spec_conformance as mod
        spec = {"openapi": "3.0.0",
                "paths": {"/items": {"get": {"responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}}}}}}}}}}}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, {"id": 1},
                                              {"Content-Type": content_type})
        try:
            return mod.run_spec_conformance_check(spec, "http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_wrong_media_type_fails(self):
        v = self._run("text/html; charset=utf-8")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("Content-Type", v.summary)

    def test_matching_media_type_with_charset_passes(self):
        v = self._run("application/json; charset=utf-8")
        self.assertEqual(v.status, "PASS", v.summary)


class TestErrorSchemaJudging(unittest.TestCase):
    def _run(self, body, with_schema=True):
        import apiverdict.spec_conformance as mod
        err_resp = {"description": "not found"}
        if with_schema:
            err_resp["content"] = {"application/json": {"schema": {
                "type": "object",
                "properties": {"code": {"type": "integer"}},
                "required": ["code"]}}}
        spec = {"openapi": "3.0.0",
                "paths": {"/item": {"get": {"responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}}}}}},
                    "404": err_resp}}}}}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (404, body, {})
        try:
            return mod.run_spec_conformance_check(spec, "http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_violating_error_body_fails(self):
        v = self._run({"message": "nope"})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("code", v.summary)

    def test_conforming_error_body_passes(self):
        v = self._run({"code": 404})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_error_without_schema_stays_unjudged(self):
        v = self._run({"anything": True}, with_schema=False)
        self.assertEqual(v.status, "INCONCLUSIVE")


class TestWriteOnlyLeak(unittest.TestCase):
    def _run(self, body):
        import apiverdict.spec_conformance as mod
        spec = {"openapi": "3.0.0",
                "paths": {"/user": {"get": {"responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "password": {"type": "string",
                                         "writeOnly": True}}}}}}}}}}}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, body, {})
        try:
            return mod.run_spec_conformance_check(spec, "http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_writeonly_in_response_fails(self):
        v = self._run({"id": 1, "password": "secret"})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("writeOnly", v.summary)

    def test_no_writeonly_passes(self):
        v = self._run({"id": 1})
        self.assertEqual(v.status, "PASS", v.summary)


class TestNullable(unittest.TestCase):
    def _run(self, body, schema):
        import apiverdict.spec_conformance as mod
        spec = {"openapi": "3.0.0",
                "paths": {"/user": {"get": {"responses": {
                    "200": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"name": schema}}}}}}}}}}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, body, {})
        try:
            return mod.run_spec_conformance_check(spec, "http://127.0.0.1:9")
        finally:
            mod.request_json = orig

    def test_nullable_null_passes(self):
        v = self._run({"name": None}, {"type": "string", "nullable": True})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_nullable_wrong_type_still_fails(self):
        v = self._run({"name": 123}, {"type": "string", "nullable": True})
        self.assertEqual(v.status, "FAIL", v.summary)

    def test_non_nullable_null_still_fails(self):
        v = self._run({"name": None}, {"type": "string"})
        self.assertEqual(v.status, "FAIL", v.summary)
