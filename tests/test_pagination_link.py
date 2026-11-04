import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.pagination import run_pagination_check
from fixture_util import FixtureServer


class TestLinkPagination(unittest.TestCase):
    def test_link_auto_fixed_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_pagination_check(url=f"{base}/api/items-link", page_size=10)
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(verdict.evidence["style"], "link")
        self.assertEqual(verdict.evidence["unique_rows"], 30)

    def test_link_explicit_fixed_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_pagination_check(
                url=f"{base}/api/items-link", page_size=10, style="link")
        self.assertEqual(verdict.status, "PASS")

    def test_link_naive_fails(self):
        with FixtureServer("naive") as base:
            verdict = run_pagination_check(url=f"{base}/api/items-link", page_size=10)
        self.assertEqual(verdict.status, "FAIL")
        self.assertTrue(verdict.evidence["duplicates"])


class TestParseLinkNext(unittest.TestCase):
    def test_rel_first(self):
        from apiverdict.http_util import parse_link_next
        self.assertEqual(
            parse_link_next({"Link": '<http://x/items?page=2>; rel="next"'}),
            "http://x/items?page=2")

    def test_rel_after_other_param(self):
        # RFC 8288: params may come in any order; rel need not be first.
        from apiverdict.http_util import parse_link_next
        self.assertEqual(
            parse_link_next({"Link": '<http://x/items?page=2>; title="Next"; rel="next"'}),
            "http://x/items?page=2")

    def test_no_next_rel(self):
        from apiverdict.http_util import parse_link_next
        self.assertIsNone(
            parse_link_next({"Link": '<http://x/items?page=1>; rel="prev"'}))
        self.assertIsNone(parse_link_next({}))


if __name__ == "__main__":
    unittest.main()


class TestRelativeLinkPagination(unittest.TestCase):
    def test_relative_link_fixed_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_pagination_check(
                url=f"{base}/api/items-link-relative",
                page_size=10, style="link")
        self.assertEqual(verdict.status, "PASS", verdict.summary)
        self.assertEqual(verdict.evidence["unique_rows"], 30)

    def test_relative_link_naive_fails(self):
        with FixtureServer("naive") as base:
            verdict = run_pagination_check(
                url=f"{base}/api/items-link-relative",
                page_size=10, style="link")
        self.assertEqual(verdict.status, "FAIL")
        self.assertTrue(verdict.evidence["duplicates"])
