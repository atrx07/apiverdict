import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.burst as burst
import apiverdict.delete_replay as mod


def _run(replay_status, replay_deletes_again=False, first_status=204):
    state = {"count": 1, "deleted": False}
    orig_v, orig_post = burst.request_json, mod.request_json

    def fake_verify(method, url, *a, **kw):
        return 200, {"count": state["count"]}, {}

    def fake_delete(method, url, payload=None, headers=None, *a, **kw):
        if not state["deleted"]:
            state["deleted"] = True
            state["count"] -= 1
            return first_status, {}, {}
        if replay_deletes_again:
            state["count"] -= 1
        return replay_status, {}, {}

    burst.request_json, mod.request_json = fake_verify, fake_delete
    try:
        return mod.run_delete_replay_check(
            "http://127.0.0.1/item/1",
            verify_url="http://127.0.0.1/count", allow_remote=True)
    finally:
        burst.request_json, mod.request_json = orig_v, orig_post


class TestDeleteReplay(unittest.TestCase):
    def test_replay_404_passes(self):
        v = _run(404)
        self.assertEqual(v.status, "PASS", v.summary)

    def test_replay_2xx_passes(self):
        v = _run(204)
        self.assertEqual(v.status, "PASS", v.summary)

    def test_replay_500_fails(self):
        v = _run(500)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("replay", v.summary)

    def test_replay_changing_state_fails(self):
        v = _run(404, replay_deletes_again=True)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("changed state", v.summary)

    def test_first_delete_failing_is_inconclusive(self):
        v = _run(404, first_status=404)
        self.assertEqual(v.status, "INCONCLUSIVE")

    def test_missing_verify_is_inconclusive(self):
        v = mod.run_delete_replay_check("http://127.0.0.1/item/1",
                                        allow_remote=True)
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
