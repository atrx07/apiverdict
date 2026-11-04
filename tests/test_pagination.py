import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.pagination import run_pagination_check
from fixture_util import FixtureServer


class TestPagination(unittest.TestCase):
    def test_naive_fails_duplicates_and_missing(self):
        with FixtureServer("naive") as base:
            verdict = run_pagination_check(url=f"{base}/api/items", page_size=10)
        self.assertEqual(verdict.status, "FAIL")
        self.assertTrue(verdict.evidence["duplicates"])
        self.assertEqual(verdict.evidence["missing"], 10)
        self.assertEqual(verdict.evidence["total"], 30)

    def test_fixed_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_pagination_check(url=f"{base}/api/items", page_size=10)
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(verdict.evidence["unique_rows"], 30)

    def test_page_style_fixed_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_pagination_check(
                url=f"{base}/api/items", page_size=10, style="page")
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(verdict.evidence["unique_rows"], 30)

    def test_cursor_style_fixed_passes_and_naive_fails(self):
        with FixtureServer("fixed") as base:
            fixed = run_pagination_check(
                url=f"{base}/api/items", page_size=10, style="cursor")
        self.assertEqual(fixed.status, "PASS")
        with FixtureServer("naive") as base:
            naive = run_pagination_check(
                url=f"{base}/api/items", page_size=10, style="cursor")
        self.assertEqual(naive.status, "FAIL")

    def test_unreachable_is_inconclusive(self):
        verdict = run_pagination_check(url="http://127.0.0.1:1/api/items")
        self.assertEqual(verdict.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
