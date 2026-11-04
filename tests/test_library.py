"""Second case study (library): naive must fail all 8 checks, fixed
must pass all 8 — same proof shape as the shop case study, in a
different domain, so the suite is not over-fitted to one fixture."""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.library import run_library

ROOT = Path(__file__).resolve().parents[1]


class TestLibraryCaseStudy(unittest.TestCase):
    def test_naive_fails_all_eight(self):
        report = run_library("naive")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(len(report["checks"]), 8)
        self.assertTrue(all(c["status"] == "FAIL" for c in report["checks"]),
                        report)

    def test_fixed_passes_all_eight(self):
        report = run_library("fixed")
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(all(c["status"] == "PASS" for c in report["checks"]),
                        report)

    def test_cli_modes_exit_codes(self):
        for mode, expected in (("naive", 1), ("fixed", 0)):
            out = subprocess.run(
                [sys.executable, "-m", "apiverdict", "library",
                 "--mode", mode],
                capture_output=True, text=True, cwd=ROOT, timeout=60)
            self.assertEqual(out.returncode, expected, out.stdout)


if __name__ == "__main__":
    unittest.main()
