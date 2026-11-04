import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.auth_schemes as mod


def _run(mode):
    orig = mod.request_json
    def fake(method, url, headers=None, *a, **kw):
        auth = (headers or {}).get("Authorization", "")
        if auth == "Bearer tok":
            return 200, {}, {}
        if mode == "confused":
            return 200, {}, {}
        return 401, {}, {}
    mod.request_json = fake
    try:
        return mod.run_auth_schemes_check("http://127.0.0.1:9/me", "tok")
    finally:
        mod.request_json = orig


class TestAuthSchemes(unittest.TestCase):
    def test_strict_server_passes(self):
        v = _run("strict")
        self.assertEqual(v.status, "PASS", v.summary)

    def test_confused_server_fails(self):
        v = _run("confused")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("wrong scheme", v.summary)

    def test_no_token_is_inconclusive(self):
        v = mod.run_auth_schemes_check("http://127.0.0.1:9/me", "")
        self.assertEqual(v.status, "INCONCLUSIVE")

    def test_denied_baseline_is_inconclusive(self):
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (401, {}, {})
        try:
            v = mod.run_auth_schemes_check("http://127.0.0.1:9/me", "tok")
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
