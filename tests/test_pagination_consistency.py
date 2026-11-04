"""Pagination consistency: same list at two page sizes must give the
same sequence and the same total. A list that changes with page size
is a bug the single-size walker cannot see."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import apiverdict.pagination as pagination_mod
from apiverdict.pagination_consistency import (
    run_pagination_consistency_check)
from fixture_util import FixtureServer


class TestPaginationConsistencyFixture(unittest.TestCase):
    def test_fixed_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_pagination_consistency_check(
                url=f"{base}/api/items")
        self.assertEqual(verdict.status, "PASS")
        self.assertTrue(verdict.evidence["same_sequence"])

    def test_naive_fails(self):
        with FixtureServer("naive") as base:
            verdict = run_pagination_consistency_check(
                url=f"{base}/api/items")
        self.assertEqual(verdict.status, "FAIL")
        self.assertFalse(verdict.evidence["same_sequence"])

    def test_equal_page_sizes_is_inconclusive(self):
        with FixtureServer("fixed") as base:
            verdict = run_pagination_consistency_check(
                url=f"{base}/api/items", page_size_a=10, page_size_b=10)
        self.assertEqual(verdict.status, "INCONCLUSIVE")


class TestPaginationConsistencyFakeTransport(unittest.TestCase):
    """Total that changes with page size is a FAIL even when each
    walk alone is internally clean (no duplicates, no gaps at its
    own size) — that is exactly the case the plain walker misses."""

    def test_total_changes_with_page_size_fails(self):
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            import urllib.parse
            query = dict(urllib.parse.parse_qsl(
                urllib.parse.urlparse(url).query))
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", 10))
            dataset = [{"id": i} for i in range(20)]
            page = dataset[offset:offset + limit]
            nxt = offset + limit if offset + limit < len(dataset) else None
            # Bug: total reported as pages*size instead of row count.
            total = ((len(dataset) + limit - 1) // limit) * limit
            return 200, {"items": page, "total": total,
                         "next_offset": nxt}, {}

        orig = pagination_mod.request_json
        pagination_mod.request_json = fake
        try:
            verdict = run_pagination_consistency_check(
                url="http://127.0.0.1:9/api/items",
                page_size_a=10, page_size_b=7)
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "FAIL")
        self.assertIn("total changes with page size", verdict.summary)


if __name__ == "__main__":
    unittest.main()


class TestUnderReportedTotal(unittest.TestCase):
    def _fake(self, total):
        import apiverdict.pagination as mod
        orig = mod.request_json
        def fake(method, url, *a, **kw):
            import urllib.parse as up
            q = dict(up.parse_qsl(up.urlparse(url).query))
            off = int(q.get("offset", "0"))
            if off == 0:
                return 200, {"items": [{"id": 1}], "total": total,
                             "next_offset": 1}, {}
            return 200, {"items": [{"id": 2}], "total": total,
                         "next_offset": None}, {}
        return orig, fake

    def test_pagination_under_reported_total_fails(self):
        import apiverdict.pagination as mod
        orig, fake = self._fake(1)
        mod.request_json = fake
        try:
            v = mod.run_pagination_check("http://127.0.0.1:9/items",
                                         page_size=1, style="offset")
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("under-reported", v.summary)

    def test_consistency_under_reported_total_fails(self):
        import apiverdict.pagination as mod
        import apiverdict.pagination_consistency as cons
        orig, fake = self._fake(1)
        mod.request_json = fake
        try:
            v = cons.run_pagination_consistency_check(
                "http://127.0.0.1:9/items",
                page_size_a=1, page_size_b=2)
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("under-reported", v.summary)
