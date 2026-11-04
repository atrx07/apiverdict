"""Pagination under a mid-walk write (milestone 3).

Same fixture server, same single write after page 1, two styles:
offset must FAIL (duplicates + missed baseline rows — the drift),
cursor must PASS (anchored on the last row seen, not a position).
This is the deterministic form of the production bug: a nightly
export paging through orders while orders keep arriving.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.pagination_concurrent import run_pagination_concurrent_check
from fixture_util import FixtureServer


class TestPaginationConcurrent(unittest.TestCase):
    def test_offset_fails_under_mid_walk_write(self):
        with FixtureServer("fixed") as base:
            v = run_pagination_concurrent_check(
                url=f"{base}/api/feed", page_size=10, style="offset",
                write_url=f"{base}/api/feed",
                write_payload={"name": "feed-new-{seq}"},
                write_after_page=1)
        self.assertEqual(v.status, "FAIL")
        # One prepended row shifts the window by one: id 10 is seen
        # twice. (Missing rows appear with deletions, or when a reader
        # stops after the original page count; this walker runs to the
        # new total, so duplicates are the drift symptom here.)
        self.assertTrue(v.evidence["duplicates"])
        self.assertEqual(v.evidence["baseline_rows"], 20)

    def test_cursor_passes_under_same_write(self):
        with FixtureServer("fixed") as base:
            v = run_pagination_concurrent_check(
                url=f"{base}/api/feed", page_size=10, style="cursor",
                write_url=f"{base}/api/feed",
                write_payload={"name": "feed-new-{seq}"},
                write_after_page=1)
        self.assertEqual(v.status, "PASS")
        self.assertEqual(v.evidence["baseline_rows"], 20)
        self.assertEqual(v.evidence["missing_baseline"], [])

    def test_no_write_url_is_inconclusive(self):
        with FixtureServer("fixed") as base:
            v = run_pagination_concurrent_check(
                url=f"{base}/api/feed", page_size=10, style="cursor")
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
