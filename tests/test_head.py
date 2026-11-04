import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.head as mod


def _run(get_headers, head_status, head_headers):
    orig = mod.request_json
    def fake(method, url, *a, **kw):
        if method == "GET":
            return 200, {"id": 1}, get_headers
        return head_status, "", head_headers
    mod.request_json = fake
    try:
        return mod.run_head_consistency_check(["http://127.0.0.1:9/x"])
    finally:
        mod.request_json = orig


class TestHeadConsistency(unittest.TestCase):
    def test_matching_passes(self):
        v = _run({"Content-Type": "application/json; charset=utf-8",
                  "Content-Length": "12"},
                 200, {"Content-Type": "application/json",
                       "Content-Length": "12"})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_length_lie_fails(self):
        v = _run({"Content-Type": "application/json",
                  "Content-Length": "12"},
                 200, {"Content-Type": "application/json",
                       "Content-Length": "19"})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("Content-Length", v.summary)

    def test_head_404_fails(self):
        v = _run({"Content-Type": "application/json"}, 404, {})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("HEAD status 404", v.summary)

    def test_head_405_is_unjudged(self):
        v = _run({"Content-Type": "application/json"}, 405, {})
        self.assertEqual(v.status, "INCONCLUSIVE")

    def test_missing_length_not_judged(self):
        v = _run({"Content-Type": "application/json"},
                 200, {"Content-Type": "application/json"})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_no_urls_is_inconclusive(self):
        v = mod.run_head_consistency_check([])
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
