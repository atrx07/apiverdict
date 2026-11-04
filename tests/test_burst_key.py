"""Burst with a shared Idempotency-Key (milestone 7)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.burst import run_burst_check
from fixture_util import FixtureServer


class TestBurstKey(unittest.TestCase):
    def _run(self, base, mode):
        # Slot AND key both carry {round}: without a per-round slot,
        # round 2 would legitimately delta 0 on a fixed server (the
        # slot is already booked) — that is burst round isolation,
        # not a key failure, and the docstring says so.
        return run_burst_check(
            url=f"{base}/api/book", payload={"slot": "key-burst-{round}"},
            n=10, rounds=2,
            verify_url=f"{base}/api/count?slot=key-burst-{{round}}",
            expect_delta=1, idempotency_key="burst-key-{round}")

    def test_fixed_passes(self):
        with FixtureServer("fixed") as base:
            v = self._run(base, "fixed")
        self.assertEqual(v.status, "PASS")
        self.assertEqual(v.evidence["idempotency_key"], "burst-key-{round}")

    def test_naive_fails(self):
        with FixtureServer("naive") as base:
            v = self._run(base, "naive")
        self.assertEqual(v.status, "FAIL")


if __name__ == "__main__":
    unittest.main()
