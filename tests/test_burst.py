import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.burst import run_burst_check
from fixture_util import FixtureServer


def _burst(base, slot_prefix="slot"):
    return run_burst_check(
        url=f"{base}/api/book",
        payload={"slot": f"{slot_prefix}-{{round}}"},
        n=10,
        rounds=2,
        verify_url=f"{base}/api/count?slot={slot_prefix}-{{round}}",
        expect_delta=1,
    )


class TestBurst(unittest.TestCase):
    def test_naive_fails_with_duplicates(self):
        with FixtureServer("naive") as base:
            verdict = _burst(base)
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(verdict.evidence["per_round"][0]["delta"], 10)

    def test_fixed_passes(self):
        with FixtureServer("fixed") as base:
            verdict = _burst(base)
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(verdict.evidence["rounds_failed"], [])

    def test_verify_cmd_failure_is_inconclusive_not_pass(self):
        with FixtureServer("fixed") as base:
            verdict = run_burst_check(
                url=f"{base}/api/book",
                payload={"slot": "x-{round}"},
                n=5, rounds=1,
                verify_cmd="false",  # exits 1, prints nothing
                expect_delta=1,
            )
        self.assertEqual(verdict.status, "INCONCLUSIVE")

    def test_verify_cmd_non_numeric_is_inconclusive(self):
        with FixtureServer("fixed") as base:
            verdict = run_burst_check(
                url=f"{base}/api/book",
                payload={"slot": "x-{round}"},
                n=5, rounds=1,
                verify_cmd="echo notanumber",
                expect_delta=1,
            )
        self.assertEqual(verdict.status, "INCONCLUSIVE")

    def test_missing_verify_is_inconclusive(self):
        with FixtureServer("fixed") as base:
            verdict = run_burst_check(
                url=f"{base}/api/book",
                payload={"slot": "x-{round}"},
                n=5, rounds=1,
            )
        self.assertEqual(verdict.status, "INCONCLUSIVE")

    def test_non_localhost_without_allow_remote_is_refused(self):
        verdict = run_burst_check(
            url="https://example.invalid/api/book",
            payload={"slot": "x"},
            n=5, rounds=1,
            verify_url="https://example.invalid/api/count",
            allow_remote=False,
        )
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("allow-remote", verdict.summary)


if __name__ == "__main__":
    unittest.main()


class TestVerifyPrecedence(unittest.TestCase):
    def test_both_verify_sources_is_inconclusive(self):
        with FixtureServer("fixed") as base:
            verdict = run_burst_check(
                url=f"{base}/api/book",
                payload={"slot": "both-{round}"},
                n=2, rounds=1,
                verify_url=f"{base}/api/count?slot=both-{{round}}",
                verify_cmd="echo 0",
                expect_delta=1,
            )
        self.assertEqual(verdict.status, "INCONCLUSIVE", verdict)
        self.assertIn("exactly one", verdict.summary)


class TestBurstSizeCap(unittest.TestCase):
    def test_n_over_1000_is_exit_2(self):
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "apiverdict", "burst",
             "--url", "http://127.0.0.1:9/api/book", "--n", "1001",
             "--verify-url", "http://127.0.0.1:9/api/count"],
            capture_output=True, text=True, cwd=".")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("--n must be <= 1000", proc.stderr)


class TestBurstMethodValidation(unittest.TestCase):
    def test_get_burst_is_inconclusive(self):
        from apiverdict.burst import run_burst_check
        v = run_burst_check("http://127.0.0.1/api/book", method="GET",
                            verify_url="http://127.0.0.1/api/count")
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("GET", v.summary)

    def test_get_burst_cli_exits_2(self):
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "apiverdict", "burst",
             "--url", "http://127.0.0.1:9/api/book", "--method", "GET",
             "--verify-url", "http://127.0.0.1:9/api/count"],
            capture_output=True, text=True, cwd=".")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


class TestBurstZeroRounds(unittest.TestCase):
    def test_zero_rounds_is_inconclusive(self):
        from apiverdict.burst import run_burst_check
        v = run_burst_check("http://127.0.0.1/api/book", rounds=0,
                            verify_url="http://127.0.0.1/api/count")
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("rounds", v.summary)

    def test_negative_expect_delta_is_inconclusive(self):
        from apiverdict.burst import run_burst_check
        v = run_burst_check("http://127.0.0.1/api/book", expect_delta=-1,
                            verify_url="http://127.0.0.1/api/count")
        self.assertEqual(v.status, "INCONCLUSIVE")


class TestBurstCompareResponses(unittest.TestCase):
    def _run(self, bodies, key="k-1"):
        import threading
        import apiverdict.burst as mod
        lock = threading.Lock()
        calls = {"n": 0, "reads": 0}
        def fake_post(method, url, payload, headers, *a, **kw):
            with lock:
                calls["n"] += 1
                body = bodies[min(calls["n"] - 1, len(bodies) - 1)]
            return 201, body, {}
        orig_post, orig_state = mod.request_json, mod._read_state
        def fake_state(cmd, url, rnd, verify_headers=None):
            with lock:
                calls["reads"] += 1
                return 0 if calls["reads"] % 2 == 1 else 1
        mod.request_json, mod._read_state = fake_post, fake_state
        try:
            return mod.run_burst_check(
                "http://127.0.0.1/x", n=3, rounds=1,
                verify_url="http://127.0.0.1/count", allow_remote=True,
                idempotency_key=key, compare_responses=True)
        finally:
            mod.request_json, mod._read_state = orig_post, orig_state

    def test_distinct_replay_bodies_fail(self):
        v = self._run([{"id": "a"}, {"id": "b"}, {"id": "c"}])
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("distinct replay", v.summary)

    def test_identical_replay_bodies_pass(self):
        v = self._run([{"id": "a"}])
        self.assertEqual(v.status, "PASS", v.summary)

    def test_compare_without_key_is_inconclusive(self):
        import apiverdict.burst as mod
        v = mod.run_burst_check("http://127.0.0.1/x", n=3, rounds=1,
                                verify_url="http://127.0.0.1/count",
                                allow_remote=True, compare_responses=True)
        self.assertEqual(v.status, "INCONCLUSIVE")
