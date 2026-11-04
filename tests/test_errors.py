import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.error_shape import run_error_shape_check
from fixture_util import FixtureServer


def _targets(base):
    return [
        {"method": "POST", "url": f"{base}/api/book"},
        {"method": "POST", "url": f"{base}/api/order"},
    ]


class TestErrorShape(unittest.TestCase):
    def test_naive_fails_two_shapes(self):
        with FixtureServer("naive") as base:
            verdict = run_error_shape_check(
                targets=_targets(base), invalid_payload={"__invalid__": True})
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(len(verdict.evidence["clusters"]), 2)

    def test_fixed_passes_one_shape(self):
        with FixtureServer("fixed") as base:
            verdict = run_error_shape_check(
                targets=_targets(base), invalid_payload={"__invalid__": True})
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(len(verdict.evidence["clusters"]), 1)

    def test_no_endpoints_is_inconclusive(self):
        verdict = run_error_shape_check(targets=[])
        self.assertEqual(verdict.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
