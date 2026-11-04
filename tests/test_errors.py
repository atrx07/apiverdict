import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.error_shape import run_error_shape_check
from fixture_util import FixtureServer


def _targets(base):
    return [
        {"method": "POST", "url": f"{base}/api/book"},
        {"method": "POST", "url": f"{base}/api/order"},
    ]


class TestErrorShape(unittest.TestCase):
    def test_naive_fails_two_shapes(self):
        with FixtureServer("naive") as base:
            verdict = run_error_shape_check(
                targets=_targets(base), invalid_payload={"__invalid__": True})
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(len(verdict.evidence["clusters"]), 2)

    def test_fixed_passes_one_shape(self):
        with FixtureServer("fixed") as base:
            verdict = run_error_shape_check(
                targets=_targets(base), invalid_payload={"__invalid__": True})
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(len(verdict.evidence["clusters"]), 1)

    def test_no_endpoints_is_inconclusive(self):
        verdict = run_error_shape_check(targets=[])
        self.assertEqual(verdict.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()


class TestRfc9457MediaType(unittest.TestCase):
    def _run(self, content_type):
        import apiverdict.error_shape as mod
        body = {"type": "about:blank", "title": "Bad input",
                "status": 400, "detail": "x is wrong"}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (400, body,
                                              {"Content-Type": content_type})
        try:
            return mod.run_error_shape_check(
                targets=[{"method": "POST", "url": "http://127.0.0.1:9/x"}],
                require_rfc9457_strict=True)
        finally:
            mod.request_json = orig

    def test_wrong_media_type_fails_strict(self):
        verdict = self._run("application/json")
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any("application/problem+json" in p
                            for p in verdict.evidence["rfc9457_strict_problems"]))

    def test_problem_json_media_type_passes_strict(self):
        verdict = self._run("application/problem+json; charset=utf-8")
        self.assertEqual(verdict.status, "PASS", verdict)


class TestServerErrorOnInvalidInput(unittest.TestCase):
    def test_consistent_500_shape_still_fails(self):
        import apiverdict.error_shape as mod
        body = {"error": "boom"}
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (500, body, {})
        try:
            v = mod.run_error_shape_check(
                targets=[{"method": "POST", "url": "http://127.0.0.1:9/a"},
                         {"method": "POST", "url": "http://127.0.0.1:9/b"}])
        finally:
            mod.request_json = orig
        # One cluster (consistent shape) but still FAIL: the crash
        # itself is the finding, and consistency must not mask it.
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("5xx", v.summary)
        self.assertEqual(len(v.evidence["clusters"]), 1)


class TestErrorShapeMethodValidation(unittest.TestCase):
    def test_typo_method_is_inconclusive(self):
        import apiverdict.error_shape as mod
        orig = mod.request_json
        calls = []
        mod.request_json = lambda *a, **kw: (calls.append(a), (400, {}, {}))[1]
        try:
            v = mod.run_error_shape_check(
                targets=[{"method": "PST", "url": "http://127.0.0.1:9/api/x"}])
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)
        self.assertIn("PST", v.summary)
        self.assertEqual(calls, [])
