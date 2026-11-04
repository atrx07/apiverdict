import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.cors as mod


def _run(status, headers):
    orig = mod.request_json
    def fake(method, url, *a, **kw):
        assert method == "OPTIONS"
        assert kw.get("headers", {}).get("Origin") == "https://example.com"
        assert kw.get("headers", {}).get("Access-Control-Request-Method") == "POST"
        return status, "", headers
    mod.request_json = fake
    try:
        return mod.run_cors_check(["http://127.0.0.1:9/x"])
    finally:
        mod.request_json = orig


class TestCors(unittest.TestCase):
    def test_full_preflight_passes(self):
        v = _run(204, {"Access-Control-Allow-Origin": "https://example.com",
                       "Access-Control-Allow-Methods": "GET, POST"})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_wildcard_origin_passes(self):
        v = _run(200, {"Access-Control-Allow-Origin": "*",
                       "Access-Control-Allow-Methods": "POST"})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_missing_allow_origin_fails(self):
        v = _run(204, {"Access-Control-Allow-Methods": "POST"})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("Allow-Origin missing", v.summary)

    def test_method_not_listed_fails(self):
        v = _run(204, {"Access-Control-Allow-Origin": "*",
                       "Access-Control-Allow-Methods": "GET"})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("does not list POST", v.summary)

    def test_preflight_404_fails(self):
        v = _run(404, {})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("preflight status 404", v.summary)

    def test_no_urls_is_inconclusive(self):
        v = mod.run_cors_check([])
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
