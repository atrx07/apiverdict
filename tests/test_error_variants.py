"""Error-variants: one endpoint, many invalid payload kinds, one
shape required. Proven against the fixture /api/variant endpoint."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.error_variants import run_error_variants_check
from fixture_util import FixtureServer


class TestErrorVariants(unittest.TestCase):
    def test_naive_fails_three_shapes(self):
        with FixtureServer("naive") as base:
            verdict = run_error_variants_check(url=f"{base}/api/variant")
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(len(verdict.evidence["clusters"]), 3)

    def test_fixed_passes_one_shape(self):
        with FixtureServer("fixed") as base:
            verdict = run_error_variants_check(url=f"{base}/api/variant")
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(len(verdict.evidence["clusters"]), 1)

    def test_unreachable_is_inconclusive(self):
        verdict = run_error_variants_check(url="http://127.0.0.1:9/api/variant")
        self.assertEqual(verdict.status, "INCONCLUSIVE")

    def test_no_payloads_is_inconclusive(self):
        with FixtureServer("fixed") as base:
            verdict = run_error_variants_check(
                url=f"{base}/api/variant", payloads=[])
        self.assertEqual(verdict.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()


class TestVariantsSinglePayload(unittest.TestCase):
    def test_single_payload_is_inconclusive(self):
        from apiverdict.error_variants import run_error_variants_check
        v = run_error_variants_check(
            "http://127.0.0.1:9/api/book", payloads=[{"__invalid__": True}])
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("at least 2", v.summary)


class TestVariantServerError(unittest.TestCase):
    def test_500_variant_fails(self):
        import apiverdict.error_variants as mod
        orig = mod.request_json
        calls = {"n": 0}
        def fake(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return 400, {"error": "bad"}, {}
            return 500, {"error": "boom"}, {}
        mod.request_json = fake
        try:
            v = mod.run_error_variants_check("http://127.0.0.1:9/x")
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("5xx", v.summary)


class TestErrorVariantsMethodValidation(unittest.TestCase):
    def test_typo_method_is_inconclusive(self):
        import apiverdict.error_variants as mod
        orig = mod.request_json
        calls = []
        mod.request_json = lambda *a, **kw: (calls.append(a), (400, {}, {}))[1]
        try:
            v = mod.run_error_variants_check("http://127.0.0.1:9/api/x",
                                             method="PST")
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)
        self.assertIn("PST", v.summary)
        self.assertEqual(calls, [])


class TestMalformedJson(unittest.TestCase):
    def _run(self, shape_statuses, malformed):
        import apiverdict.error_variants as mod
        orig = mod.request_json
        calls = {"n": 0}
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            if kw.get("raw") is not None:
                if isinstance(malformed, Exception):
                    raise malformed
                return malformed, {"error": "parse failed"}, {}
            calls["n"] += 1
            status = shape_statuses[min(calls["n"] - 1, len(shape_statuses) - 1)]
            return status, {"error": "invalid", "code": "BAD_REQUEST"}, {}
        mod.request_json = fake
        try:
            return mod.run_error_variants_check("http://127.0.0.1:9/x")
        finally:
            mod.request_json = orig

    def test_malformed_400_passes(self):
        v = self._run([400, 400, 400], 400)
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertEqual(v.evidence["malformed_status"], 400)
        self.assertEqual(len(v.evidence["clusters"]), 1)

    def test_malformed_shape_exempt(self):
        # Malformed returns a DIFFERENT body shape, but the check
        # still passes: only the status class is judged for it.
        import apiverdict.error_variants as mod
        orig = mod.request_json
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            if kw.get("raw") is not None:
                return 400, "plain text parser error", {}
            return 400, {"error": "invalid", "code": "BAD_REQUEST"}, {}
        mod.request_json = fake
        try:
            v = mod.run_error_variants_check("http://127.0.0.1:9/x")
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertEqual(len(v.evidence["clusters"]), 1)

    def test_malformed_500_fails(self):
        v = self._run([400, 400, 400], 500)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("malformed", v.summary)

    def test_malformed_200_fails(self):
        v = self._run([400, 400, 400], 200)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("malformed", v.summary)

    def test_malformed_transport_inconclusive(self):
        v = self._run([400, 400, 400], ConnectionError("refused"))
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)

    def test_malformed_3xx_inconclusive(self):
        v = self._run([400, 400, 400], 302)
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)

    def test_shape_fail_dominates_malformed_transport(self):
        v = self._run([400, 422, 400], ConnectionError("refused"))
        self.assertEqual(v.status, "FAIL", v.summary)
