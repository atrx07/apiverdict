import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.demo import run_demo
from apiverdict.runner import Verdict, exit_code_for


class TestRunner(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(exit_code_for([Verdict("a", "PASS", "ok")]), 0)
        self.assertEqual(
            exit_code_for([Verdict("a", "PASS", "ok"),
                           Verdict("b", "INCONCLUSIVE", "no")]), 2)
        # FAIL beats INCONCLUSIVE, and is never green on a skipped check.
        self.assertEqual(
            exit_code_for([Verdict("a", "FAIL", "bad"),
                           Verdict("b", "INCONCLUSIVE", "no")]), 1)


class TestDemo(unittest.TestCase):
    def test_demo_naive_fails(self):
        report = run_demo("naive")
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual({c["check"] for c in report["checks"]},
                         {"burst", "pagination", "error-shape"})
        self.assertTrue(all(c["status"] == "FAIL" for c in report["checks"]))

    def test_demo_fixed_passes(self):
        report = run_demo("fixed")
        self.assertEqual(report["overall"], "PASS")
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(all(c["status"] == "PASS" for c in report["checks"]))


if __name__ == "__main__":
    unittest.main()
