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


class TestEmptyPages(unittest.TestCase):
    def _run(self, pages_meta):
        """pages_meta: {page_no: (rows, total_pages)}."""
        import urllib.parse
        import apiverdict.pagination as mod
        orig = mod.request_json
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
            page_no = int(q.get("page", "1"))
            rows, total_pages = pages_meta[page_no]
            return 200, {"items": rows, "page": page_no,
                         "total_pages": total_pages}, {}
        mod.request_json = fake
        try:
            return mod.run_pagination_check(
                "http://127.0.0.1:9/api/items", page_size=10, style="page")
        finally:
            mod.request_json = orig

    def test_many_empty_pages_fail(self):
        p1 = [{"id": i} for i in range(1, 11)]
        p2 = [{"id": i} for i in range(11, 21)]
        v = self._run({1: (p1, 4), 2: (p2, 4), 3: ([], 4), 4: ([], 4)})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("empty page", v.summary)

    def test_one_empty_page_tolerated(self):
        p1 = [{"id": i} for i in range(1, 11)]
        p2 = [{"id": i} for i in range(11, 21)]
        v = self._run({1: (p1, 3), 2: (p2, 3), 3: ([], 3)})
        self.assertEqual(v.status, "PASS", v.summary)


class TestTotalPagesDrift(unittest.TestCase):
    def test_total_pages_drift_is_inconclusive(self):
        import urllib.parse
        import apiverdict.pagination as mod
        orig = mod.request_json
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
            page_no = int(q.get("page", "1"))
            rows = [{"id": i} for i in range(1, 11)]
            tp = 5 if page_no == 1 else 4
            return 200, {"items": rows, "page": page_no,
                         "total_pages": tp}, {}
        mod.request_json = fake
        try:
            v = mod.run_pagination_check(
                "http://127.0.0.1:9/api/items", page_size=10, style="page")
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)
        self.assertIn("total_pages changed", v.summary)
