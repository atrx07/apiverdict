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
