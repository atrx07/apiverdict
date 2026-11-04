import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.patch as mod


def _run(mode):
    orig = mod.request_json
    state = {"name": "old", "score": 5, "extra": "keep"}
    def fake(method, url, payload=None, headers=None, *a, **kw):
        if method == "GET":
            return 200, dict(state), {}
        if mode == "correct":
            state.update(payload)
        elif mode == "put-semantics":
            state.clear()
            state.update(payload)
        elif mode == "ignored":
            pass
        return 200, dict(state), {}
    mod.request_json = fake
    try:
        return mod.run_patch_preserves_check(
            "http://127.0.0.1/item/1", "name", "new", allow_remote=True)
    finally:
        mod.request_json = orig


class TestPatchPreserves(unittest.TestCase):
    def test_correct_patch_passes(self):
        v = _run("correct")
        self.assertEqual(v.status, "PASS", v.summary)

    def test_put_semantics_fails(self):
        v = _run("put-semantics")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("wiped", v.summary)

    def test_ignored_patch_fails(self):
        v = _run("ignored")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("ignored", v.summary)

    def test_missing_url_is_inconclusive(self):
        # No URL is a CLI concern; library-level: unreachable read.
        v = mod.run_patch_preserves_check(
            "http://127.0.0.1:9/item/1", "name", "new", allow_remote=True)
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
