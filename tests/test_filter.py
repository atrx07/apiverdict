import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import apiverdict.list_filter as mod
from fixture_util import FixtureServer


def _run(rows_by_page, field="status", value="active"):
    """Fake transport: pages keyed by offset."""
    orig = mod._walk_once
    def fake(url, page_size, style, id_field, max_pages, timeout,
             headers=None, rows_out=None, **kw):
        import urllib.parse
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        off = int(q.get("offset", "0"))
        rows = rows_by_page.get(off, [])
        if rows_out is not None:
            rows_out.extend(rows)
        return [r.get("id") for r in rows], {"pages": 1}, None
    mod._walk_once = fake
    try:
        return mod.run_filter_check("http://127.0.0.1:9/api/items",
                                    field, value)
    finally:
        mod._walk_once = orig


class TestFilter(unittest.TestCase):
    def test_all_match_passes(self):
        v = _run({0: [{"id": 1, "status": "active"},
                     {"id": 2, "status": "active"}]})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_violation_fails(self):
        v = _run({0: [{"id": 1, "status": "active"},
                     {"id": 2, "status": "archived"}]})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertEqual(v.evidence["violation_count"], 1)

    def test_missing_field_everywhere_is_inconclusive(self):
        v = _run({0: [{"id": 1, "name": "x"}]}, field="status")
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)

    def test_empty_list_is_inconclusive(self):
        v = _run({})
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)

    def test_case_and_type_coercion(self):
        rows = {0: [{"id": 1, "status": "Active", "count": 5},
                    {"id": 2, "status": "ACTIVE", "count": 5}]}
        v = _run(rows, field="status", value="active")
        self.assertEqual(v.status, "PASS", v.summary)
        v = _run(rows, field="count", value="5")
        self.assertEqual(v.status, "PASS", v.summary)


if __name__ == "__main__":
    unittest.main()


class TestFilterFixture(unittest.TestCase):
    def test_fixture_fixed_applies_filter(self):
        with FixtureServer("fixed") as base:
            v = mod.run_filter_check(f"{base}/api/items",
                                     "id", "1")
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertEqual(v.evidence["rows_checked"], 1)

    def test_fixture_naive_ignores_filter(self):
        with FixtureServer("naive") as base:
            v = mod.run_filter_check(f"{base}/api/items",
                                     "id", "1")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertEqual(v.evidence["rows_checked"], 30)
