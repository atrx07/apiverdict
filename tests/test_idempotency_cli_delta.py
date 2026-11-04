"""CLI --expect-delta reaches run_idempotency_check (fixture
end-to-end: delta 1 passes, expecting 2 fails)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_util import FixtureServer
import subprocess


def _cli(base, delta):
    return subprocess.run(
        [sys.executable, "-m", "apiverdict", "idempotency",
         "--url", f"{base}/api/book",
         "--payload", '{"slot": "idem-cli-fixed"}',
         "--verify-url", f"{base}/api/count?slot=idem-cli-fixed",
         "--expect-delta", str(delta)],
        capture_output=True, text=True, cwd=".", timeout=60)


class TestIdempotencyCliDelta(unittest.TestCase):
    def test_expect_delta_passthrough(self):
        with FixtureServer("fixed") as base:
            ok = _cli(base, 1)
            bad = _cli(base, 2)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)


if __name__ == "__main__":
    unittest.main()


class TestVerifyHeaderFlag(unittest.TestCase):
    def test_flag_parsing_and_fallback(self):
        from apiverdict.__main__ import _verify_headers_for
        from types import SimpleNamespace
        self.assertIsNone(_verify_headers_for(SimpleNamespace(verify_header=[])))
        self.assertIsNone(_verify_headers_for(SimpleNamespace()))
        self.assertEqual(
            _verify_headers_for(SimpleNamespace(
                verify_header=["Authorization: Bearer admin", "X-A: 1"])),
            {"Authorization": "Bearer admin", "X-A": "1"})

    def test_help_lists_flag_on_all_four_commands(self):
        for cmd in ("burst", "idempotency", "idempotency-variants", "webhook"):
            proc = subprocess.run(
                [sys.executable, "-m", "apiverdict", cmd, "--help"],
                capture_output=True, text=True, cwd=".")
            self.assertIn("--verify-header", proc.stdout, cmd)
