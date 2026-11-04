"""Pagination cursor aliases: real APIs disagree on the token field
name. The walker must follow next_page_token and cursor, and a
body "next" URL, not only next_cursor."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.pagination as pagination_mod
from apiverdict.pagination import run_pagination_check


def _run(pages):
    """pages: query-key -> (body, headers). The fake answers from the
    key the walker actually sends (cursor token, or offset)."""

    def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
        import urllib.parse
        query = dict(urllib.parse.parse_qsl(
            urllib.parse.urlparse(url).query))
        key = query.get("cursor", query.get("offset", "0"))
        body, hdrs = pages[key]
        return 200, body, hdrs

    orig = pagination_mod.request_json
    pagination_mod.request_json = fake
    try:
        return run_pagination_check(
            url="http://127.0.0.1:9/api/items", page_size=2)
    finally:
        pagination_mod.request_json = orig


def _rows(*ids):
    return [{"id": i} for i in ids]


class TestCursorAliases(unittest.TestCase):
    def test_next_page_token(self):
        verdict = _run({
            "0": ({"items": _rows(1, 2), "next_page_token": "t1"}, {}),
            "t1": ({"items": _rows(3, 4), "next_page_token": None}, {}),
        })
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["rows_collected"], 4)

    def test_cursor_field_and_integer_token(self):
        verdict = _run({
            "0": ({"items": _rows(1, 2), "cursor": 7}, {}),
            "7": ({"items": _rows(3), "cursor": None}, {}),
        })
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["rows_collected"], 3)

    def test_body_next_relative_url_followed_verbatim(self):
        verdict = _run({
            "0": ({"items": _rows(1, 2),
                   "next": "/api/items?offset=2&limit=2"}, {}),
            "2": ({"items": _rows(3, 4)}, {}),
        })
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["rows_collected"], 4)


if __name__ == "__main__":
    unittest.main()


class TestDottedIdField(unittest.TestCase):
    def test_nested_id(self):
        def fake(m, u, payload=None, headers=None, timeout=10.0, **kw):
            import urllib.parse
            query = dict(urllib.parse.parse_qsl(
                urllib.parse.urlparse(u).query))
            if query.get("offset", "0") == "0":
                return 200, {"items": [{"item": {"id": 1}},
                                       {"item": {"id": 2}}],
                             "total": 2}, {}
            return 200, {"items": [], "total": 2}, {}

        orig = pagination_mod.request_json
        pagination_mod.request_json = fake
        try:
            verdict = run_pagination_check(
                url="http://127.0.0.1:9/api/items", page_size=2,
                id_field="item.id")
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["rows_collected"], 2)


class TestCustomPageParams(unittest.TestCase):
    def test_underscore_page_params_and_total_header(self):
        posts = [{"id": i} for i in range(1, 26)]

        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            import urllib.parse
            query = dict(urllib.parse.parse_qsl(
                urllib.parse.urlparse(url).query))
            page = int(query.get("_page", 1))
            limit = int(query.get("_limit", 10))
            chunk = posts[(page - 1) * limit:page * limit]
            return 200, chunk, {"X-Total-Count": "25"}

        orig = pagination_mod.request_json
        pagination_mod.request_json = fake
        try:
            verdict = run_pagination_check(
                url="http://127.0.0.1:9/posts", page_size=10,
                style="page", page_param="_page", size_param="_limit")
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["rows_collected"], 25)
        self.assertEqual(verdict.evidence["total"], 25)


class TestLimitIgnored(unittest.TestCase):
    def test_oversize_page_fails(self):
        # Also pins the early stop: the walk must not burn max_pages.
        calls = {"n": 0}
        rows = [{"id": i} for i in range(1, 26)]

        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            calls["n"] += 1
            return 200, {"items": rows, "total": 25}, {}

        orig = pagination_mod.request_json
        pagination_mod.request_json = fake
        try:
            verdict = run_pagination_check(
                url="http://127.0.0.1:9/api/items", page_size=10)
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertIn("ignored the requested limit", verdict.summary)
        self.assertEqual(verdict.evidence["limit_ignored"]["rows"], 25)
        self.assertEqual(calls["n"], 1, "walk must stop, not burn max_pages")
        self.assertEqual(verdict.evidence["pages"], 1)


class TestSecondWalkParams(unittest.TestCase):
    def test_order_walk_uses_custom_params(self):
        posts = [{"id": i} for i in range(1, 26)]

        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            import urllib.parse
            query = dict(urllib.parse.parse_qsl(
                urllib.parse.urlparse(url).query))
            if "_page" not in query:
                # Default page/size names: this API does not know
                # them, so it errors instead of paging.
                return 400, {"error": "unknown params"}, {}
            page = int(query.get("_page", 1))
            limit = int(query.get("_limit", 10))
            chunk = posts[(page - 1) * limit:page * limit]
            return 200, chunk, {"X-Total-Count": "25"}

        orig = pagination_mod.request_json
        pagination_mod.request_json = fake
        try:
            verdict = run_pagination_check(
                url="http://127.0.0.1:9/posts", page_size=10,
                style="page", page_param="_page", size_param="_limit")
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertIs(verdict.evidence["order_stable"], True, verdict)


class TestWalkNumericGuards(unittest.TestCase):
    def test_zero_max_pages_is_inconclusive(self):
        from apiverdict.pagination import run_pagination_check
        v = run_pagination_check("http://127.0.0.1:9/api/items",
                                 page_size=10, max_pages=0)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("max pages", v.summary)

    def test_zero_page_size_is_inconclusive(self):
        from apiverdict.pagination import run_pagination_check
        v = run_pagination_check("http://127.0.0.1:9/api/items",
                                 page_size=0)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("page size", v.summary)


class TestRowRetype(unittest.TestCase):
    def _run(self, page2_val):
        import apiverdict.pagination as mod
        orig = mod.request_json
        def fake(method, url, *a, **kw):
            import urllib.parse as up
            q = dict(up.parse_qsl(up.urlparse(url).query))
            off = int(q.get("offset", "0"))
            if off == 0:
                return 200, {"items": [{"id": 1, "val": 10}],
                             "total": 2}, {}
            return 200, {"items": [{"id": 2, "val": page2_val}],
                         "total": 2, "next_offset": None}, {}
        mod.request_json = fake
        try:
            return mod.run_pagination_check("http://127.0.0.1:9/items",
                                            page_size=1, style="offset")
        finally:
            mod.request_json = orig

    def test_retype_fails(self):
        v = self._run("ten")
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("val", v.summary)

    def test_nullable_does_not_fail(self):
        v = self._run(None)
        self.assertEqual(v.status, "PASS", v.summary)
