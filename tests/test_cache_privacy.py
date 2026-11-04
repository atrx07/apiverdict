import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.cache_privacy as mod


def _run(body_a, body_b, status_b=200, ignore=None):
    orig = mod.request_json
    def fake(method, url, headers=None, *a, **kw):
        if (headers or {}).get("Authorization") == "Bearer A":
            return 200, body_a, {}
        return status_b, body_b, {}
    mod.request_json = fake
    try:
        return mod.run_cache_privacy_check(
            "http://127.0.0.1/me",
            headers_a={"Authorization": "Bearer A"},
            headers_b={"Authorization": "Bearer B"},
            ignore_fields=ignore)
    finally:
        mod.request_json = orig


class TestCachePrivacy(unittest.TestCase):
    def test_identical_fails(self):
        v = _run({"user": "alice"}, {"user": "alice"})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("identical", v.summary)

    def test_differing_passes(self):
        v = _run({"user": "alice"}, {"user": "bob"})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_unignored_difference_passes(self):
        v = _run({"user": "alice", "ts": "t1"},
                 {"user": "bob", "ts": "t2"}, ignore=["user"])
        # Stripped of "user", both are {"ts": ...} — but ts differs
        # and is NOT ignored, so they differ -> PASS.
        self.assertEqual(v.status, "PASS", v.summary)

    def test_ignored_field_making_identical_fails(self):
        v = _run({"user": "alice", "ts": "t1"},
                 {"user": "alice", "ts": "t2"}, ignore=["ts"])
        self.assertEqual(v.status, "FAIL", v.summary)

    def test_denied_second_caller_is_inconclusive(self):
        v = _run({"user": "alice"}, {"error": "denied"}, status_b=403)
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
