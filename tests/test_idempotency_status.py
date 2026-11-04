"""Idempotency send-status rules: a failed first send is
INCONCLUSIVE (nothing to replay), a replay that errors is a
FAIL even when the delta matches."""
import unittest

from apiverdict import idempotency as idem


def _run(monkey_statuses, states, fn, **kw):
    orig_req, orig_state = idem.request_json, idem._read_state
    calls = {"n": 0}
    state_iter = iter(states)

    def fake_req(method, url, payload=None, headers=None, timeout=10.0, **k):
        status = monkey_statuses[min(calls["n"], len(monkey_statuses) - 1)]
        calls["n"] += 1
        return status, {}, {}

    idem.request_json = fake_req
    idem._read_state = lambda *a, **k: next(state_iter)
    try:
        return fn(**kw)
    finally:
        idem.request_json, idem._read_state = orig_req, orig_state


class TestIdempotencyStatus(unittest.TestCase):
    def test_first_send_failure_inconclusive(self):
        v = _run([500, 500], [0], idem.run_idempotency_check,
                 url="http://127.0.0.1:9/api/book", verify_url="http://127.0.0.1:9/api/count")
        self.assertEqual(v.status, "INCONCLUSIVE", v)
        self.assertIn("first send failed", v.summary)

    def test_replay_error_fails_despite_good_delta(self):
        v = _run([201, 500], [0, 1], idem.run_idempotency_check,
                 url="http://127.0.0.1:9/api/book", verify_url="http://127.0.0.1:9/api/count")
        self.assertEqual(v.status, "FAIL", v)
        self.assertIn("replay errored", v.summary)

    def test_clean_replay_still_passes(self):
        v = _run([201, 200], [0, 1], idem.run_idempotency_check,
                 url="http://127.0.0.1:9/api/book", verify_url="http://127.0.0.1:9/api/count")
        self.assertEqual(v.status, "PASS", v)

    def test_variants_alt_5xx_fails(self):
        v = _run([201, 200, 500], [0, 1, 1], idem.run_idempotency_variants_check,
                 url="http://127.0.0.1:9/api/book", verify_url="http://127.0.0.1:9/api/count")
        self.assertEqual(v.status, "FAIL", v)
        self.assertIn("alt-payload send errored", v.summary)


if __name__ == "__main__":
    unittest.main()


class TestIdempotencyVerifyHeaders(unittest.TestCase):
    def test_verify_url_receives_check_headers(self):
        import apiverdict.burst as burst
        import apiverdict.idempotency as idem
        seen = []
        counts = [0, 1]

        def fake_verify(method, url, *a, **kw):
            headers = kw.get("headers") or (a[1] if len(a) > 1 else None)
            seen.append(headers)
            return 200, {"count": counts.pop(0)}, {}

        def fake_post(method, url, *a, **kw):
            return 201, {}, {}

        orig_verify, orig_post = burst.request_json, idem.request_json
        burst.request_json, idem.request_json = fake_verify, fake_post
        try:
            verdict = idem.run_idempotency_check(
                "http://127.0.0.1/api/book",
                headers={"Authorization": "Bearer tok"},
                verify_url="http://127.0.0.1/api/count",
                allow_remote=True)
        finally:
            burst.request_json, idem.request_json = orig_verify, orig_post
        self.assertEqual(verdict.status, "PASS", verdict.summary)
        self.assertEqual(len(seen), 2)
        for headers in seen:
            self.assertEqual(headers.get("Authorization"), "Bearer tok")


class TestVerifyHeadersOverride(unittest.TestCase):
    def test_distinct_verify_headers_win(self):
        import apiverdict.burst as burst
        import apiverdict.idempotency as idem
        seen = []
        counts = [0, 1]

        def fake_verify(method, url, *a, **kw):
            seen.append(kw.get("headers"))
            return 200, {"count": counts.pop(0)}, {}

        def fake_post(method, url, *a, **kw):
            return 201, {}, {}

        orig_verify, orig_post = burst.request_json, idem.request_json
        burst.request_json, idem.request_json = fake_verify, fake_post
        try:
            verdict = idem.run_idempotency_check(
                "http://127.0.0.1/api/book",
                headers={"Authorization": "Bearer user"},
                verify_headers={"Authorization": "Bearer admin"},
                verify_url="http://127.0.0.1/api/count",
                allow_remote=True)
        finally:
            burst.request_json, idem.request_json = orig_verify, orig_post
        self.assertEqual(verdict.status, "PASS", verdict.summary)
        self.assertTrue(seen)
        for headers in seen:
            self.assertEqual(headers.get("Authorization"), "Bearer admin")


class TestIdempotencyMethodValidation(unittest.TestCase):
    def test_get_replay_is_inconclusive(self):
        import apiverdict.idempotency as idem
        v = idem.run_idempotency_check(
            "http://127.0.0.1/api/book", method="GET",
            verify_url="http://127.0.0.1/api/count", allow_remote=True)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("GET", v.summary)


class TestIdempotencyDeltaGuard(unittest.TestCase):
    def test_negative_expect_delta_is_inconclusive(self):
        import apiverdict.idempotency as idem
        v = idem.run_idempotency_check(
            "http://127.0.0.1/api/book", expect_delta=-1,
            verify_url="http://127.0.0.1/api/count", allow_remote=True)
        self.assertEqual(v.status, "INCONCLUSIVE")


class TestIdempotencyScope(unittest.TestCase):
    """Fake transports: 'global' dedupes by key across endpoints
    (the bug); 'scoped' dedupes per endpoint (correct)."""

    def _run(self, mode, b_status=201):
        import apiverdict.burst as burst
        import apiverdict.idempotency as idem
        counts = {"a": 0, "b": 0}
        seen_global = set()
        seen_per_url = set()

        def fake_verify(method, url, *a, **kw):
            if "/count-a" in url:
                return 200, {"count": counts["a"]}, {}
            return 200, {"count": counts["b"]}, {}

        def fake_post(method, url, payload, headers, *a, **kw):
            key = (headers or {}).get("Idempotency-Key")
            slot = "a" if "/a" in url else "b"
            if mode == "global":
                if key in seen_global:
                    return 200, {}, {}
                seen_global.add(key)
            else:
                if (slot, key) in seen_per_url:
                    return 200, {}, {}
                seen_per_url.add((slot, key))
            if slot == "b" and b_status != 201:
                return b_status, {}, {}
            counts[slot] += 1
            return 201, {}, {}

        orig_verify, orig_post = burst.request_json, idem.request_json
        burst.request_json, idem.request_json = fake_verify, fake_post
        try:
            return idem.run_idempotency_scope_check(
                "http://127.0.0.1/a", "http://127.0.0.1/b",
                verify_url_a="http://127.0.0.1/count-a",
                verify_url_b="http://127.0.0.1/count-b",
                allow_remote=True)
        finally:
            burst.request_json, idem.request_json = orig_verify, orig_post

    def test_global_dedupe_fails(self):
        v = self._run("global")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("across endpoints", v.summary)

    def test_scoped_dedupe_passes(self):
        v = self._run("scoped")
        self.assertEqual(v.status, "PASS", v.summary)

    def test_b_rejection_is_inconclusive(self):
        v = self._run("scoped", b_status=409)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("rejected", v.summary)

    def test_missing_verify_is_inconclusive(self):
        import apiverdict.idempotency as idem
        v = idem.run_idempotency_scope_check(
            "http://127.0.0.1/a", "http://127.0.0.1/b",
            verify_url_a="http://127.0.0.1/count-a", allow_remote=True)
        self.assertEqual(v.status, "INCONCLUSIVE")


class TestReplayResponseComparison(unittest.TestCase):
    def _run(self, second_body, ignore=None):
        import apiverdict.burst as burst
        import apiverdict.idempotency as idem
        calls = {"n": 0, "reads": 0}
        def fake_verify(method, url, *a, **kw):
            calls["reads"] += 1
            return 200, {"count": 0 if calls["reads"] == 1 else 1}, {}
        def fake_post(method, url, payload, headers, *a, **kw):
            calls["n"] += 1
            return 201, ({"id": "ord-1", "ts": "t1"} if calls["n"] == 1
                         else second_body), {}
        orig_v, orig_p = burst.request_json, idem.request_json
        burst.request_json, idem.request_json = fake_verify, fake_post
        try:
            return idem.run_idempotency_check(
                "http://127.0.0.1/x",
                verify_url="http://127.0.0.1/count", allow_remote=True,
                compare_responses=True, ignore_fields=ignore)
        finally:
            burst.request_json, idem.request_json = orig_v, orig_p

    def test_differing_replay_fails(self):
        v = self._run({"id": "ord-2", "ts": "t1"})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("differs", v.summary)
        self.assertIn("id", v.summary)

    def test_identical_replay_passes(self):
        v = self._run({"id": "ord-1", "ts": "t1"})
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertTrue(v.evidence["responses_match"])

    def test_ignored_volatile_field_passes(self):
        v = self._run({"id": "ord-1", "ts": "t2"}, ignore=["ts"])
        self.assertEqual(v.status, "PASS", v.summary)


class TestReplayStatusEquality(unittest.TestCase):
    def test_replay_status_change_fails(self):
        import apiverdict.idempotency as mod
        calls = {"n": 0}
        def fake_post(method, url, payload, headers, timeout):
            calls["n"] += 1
            return (201 if calls["n"] == 1 else 200), {"id": 7}, {}
        def fake_state(cmd, url, slot, verify_headers=None):
            return 0 if calls["n"] == 0 else 1
        orig_post, orig_state = mod.request_json, mod._read_state
        mod.request_json, mod._read_state = fake_post, fake_state
        try:
            v = mod.run_idempotency_check(
                "http://127.0.0.1:9/api/book", "POST", {"item": "x"},
                verify_url="http://127.0.0.1:9/api/count",
                compare_responses=True)
        finally:
            mod.request_json, mod._read_state = orig_post, orig_state
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("status 200 differs", v.summary)
