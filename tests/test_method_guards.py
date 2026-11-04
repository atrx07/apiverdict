import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.method_guards as mod


def _run(status, allow):
    orig = mod.request_json
    headers = {} if allow is None else {"Allow": allow}
    mod.request_json = lambda *a, **kw: (status, "", headers)
    try:
        return mod.run_method_guards_check(
            [{"method": "DELETE", "url": "http://127.0.0.1:9/x"}],
            allow_remote=True)
    finally:
        mod.request_json = orig


class TestMethodGuards(unittest.TestCase):
    def test_proper_405_passes(self):
        v = _run(405, "GET, POST")
        self.assertEqual(v.status, "PASS", v.summary)

    def test_405_without_allow_fails(self):
        v = _run(405, None)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("without Allow", v.summary)

    def test_allow_listing_rejected_method_fails(self):
        v = _run(405, "GET, DELETE")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("itself", v.summary)

    def test_accepted_method_fails(self):
        v = _run(200, "GET, POST")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("accepted", v.summary)

    def test_wrong_status_fails(self):
        v = _run(404, None)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("expected 405", v.summary)

    def test_no_targets_is_inconclusive(self):
        v = mod.run_method_guards_check([])
        self.assertEqual(v.status, "INCONCLUSIVE")

    def test_remote_mutating_probe_guarded(self):
        v = mod.run_method_guards_check(
            [{"method": "DELETE", "url": "https://example.com/x"}])
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()


class TestMethodGuardsValidation(unittest.TestCase):
    def test_typo_method_is_inconclusive(self):
        import apiverdict.method_guards as mod
        orig = mod.request_json
        calls = []
        mod.request_json = lambda *a, **kw: (calls.append(a), (200, {}, {}))[1]
        try:
            v = mod.run_method_guards_check(
                [{"method": "DELET", "url": "http://127.0.0.1:9/api/x"}])
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)
        self.assertIn("DELET", v.summary)
        self.assertEqual(calls, [])
