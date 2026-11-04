import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.casestudy import run_casestudy


class TestCaseStudy(unittest.TestCase):
    def test_naive_all_eight_fail(self):
        report = run_casestudy("naive")
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(len(report["checks"]), 8)
        self.assertTrue(all(c["status"] == "FAIL" for c in report["checks"]),
                        report["checks"])
        self.assertEqual(
            {c["check"] for c in report["checks"]},
            {"burst", "pagination", "error-shape", "auth-matrix",
             "rate-limit", "idempotency", "webhook", "cache"})

    def test_fixed_all_eight_pass(self):
        report = run_casestudy("fixed")
        self.assertEqual(report["overall"], "PASS")
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(all(c["status"] == "PASS" for c in report["checks"]),
                        report["checks"])


if __name__ == "__main__":
    unittest.main()
