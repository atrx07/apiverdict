"""Per-endpoint invalid payloads + RFC 9457 strict checks (milestone 3)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.error_payloads import invalid_payload_for
from apiverdict.error_shape import _rfc9457_strict_problems, run_error_shape_check
from fixture_util import FixtureServer


class TestInvalidPayloadFor(unittest.TestCase):
    def test_drops_first_required(self):
        schema = {"type": "object", "required": ["name", "qty"],
                  "properties": {"name": {"type": "string"},
                                 "qty": {"type": "integer"}}}
        payload, reason = invalid_payload_for(schema)
        self.assertNotIn("name", payload)
        self.assertEqual(payload["qty"], 1)
        self.assertIn("name", reason)

    def test_wrong_types_string_field(self):
        schema = {"type": "object",
                  "properties": {"name": {"type": "string"}}}
        payload, reason = invalid_payload_for(schema)
        self.assertEqual(payload["name"], 12345)
        self.assertIn("wrong-typed", reason)

    def test_no_schema_falls_back_generic(self):
        payload, reason = invalid_payload_for(None)
        self.assertEqual(payload, {"__invalid__": True})

    def test_ref_resolved_before_probing(self):
        spec = {"components": {"schemas": {"Order": {
            "type": "object", "required": ["order_id"],
            "properties": {"order_id": {"type": "string"}}}}}}
        payload, reason = invalid_payload_for(
            {"$ref": "#/components/schemas/Order"}, spec)
        self.assertNotIn("order_id", payload)
        self.assertIn("order_id", reason)


class TestRfc9457Strict(unittest.TestCase):
    def test_status_mismatch_flagged(self):
        problems = _rfc9457_strict_problems(
            400, {"type": "https://example.invalid/bad", "title": "Bad",
                  "status": 422, "detail": "x"})
        self.assertTrue(any("422" in p for p in problems))

    def test_relative_type_flagged(self):
        problems = _rfc9457_strict_problems(
            400, {"type": "validation-error", "title": "Bad",
                  "status": 400, "detail": "x"})
        self.assertTrue(any("type" in p for p in problems))

    def test_about_blank_and_matching_status_pass(self):
        problems = _rfc9457_strict_problems(
            400, {"type": "about:blank", "title": "Bad",
                  "status": 400, "detail": "x"})
        self.assertEqual(problems, [])

    def test_strict_flag_on_case_study_fixed_passes(self):
        # Case-study fixed errors are proper problem details with
        # matching status + absolute type URIs.
        import threading
        from apiverdict.casestudy import create_case_server
        server = create_case_server("fixed")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            v = run_error_shape_check(
                targets=[{"method": "POST", "url": f"{base}/api/bookings"},
                         {"method": "POST", "url": f"{base}/api/payments"}],
                invalid_payload={"__invalid__": True},
                require_rfc9457_strict=True)
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(v.status, "PASS")


if __name__ == "__main__":
    unittest.main()


class TestRefPropertyPayload(unittest.TestCase):
    def test_ref_typed_property_wrong_typed(self):
        spec = {"components": {"schemas": {
            "Name": {"type": "string"}}}}
        schema = {"type": "object",
                  "properties": {"name": {"$ref": "#/components/schemas/Name"}}}
        payload, reason = invalid_payload_for(schema, spec)
        self.assertEqual(payload, {"name": 12345})
        self.assertIn("wrong-typed", reason)


class TestRefRequestBody(unittest.TestCase):
    def test_refd_request_body_yields_targeted_payload(self):
        from apiverdict.error_payloads import request_body_schema, invalid_payload_for
        spec = {"components": {"requestBodies": {"Create": {"content": {
            "application/json": {"schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"]}}}}}}}
        op = {"requestBody": {"$ref": "#/components/requestBodies/Create"}}
        schema = request_body_schema(op, spec)
        self.assertIsNotNone(schema)
        payload, reason = invalid_payload_for(schema, spec)
        self.assertEqual(payload, {})
        self.assertIn("dropped required field 'name'", reason)
