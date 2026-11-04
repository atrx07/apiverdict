import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.detail as mod


def _run(row, detail):
    orig = mod.request_json
    def fake(method, url, *a, **kw):
        if "/list" in url:
            return 200, {"items": [row]}, {}
        return 200, detail, {}
    mod.request_json = fake
    try:
        return mod.run_detail_superset_check(
            "http://127.0.0.1/list", "http://127.0.0.1/detail/{id}")
    finally:
        mod.request_json = orig


class TestDetailSuperset(unittest.TestCase):
    def test_superset_passes(self):
        v = _run({"id": 1, "name": "a"},
                 {"id": 1, "name": "a", "extra": True})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_missing_field_fails(self):
        v = _run({"id": 1, "name": "a"}, {"id": 1})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("name", v.summary)

    def test_retyped_field_fails(self):
        v = _run({"id": 1, "name": "a"}, {"id": 1, "name": 7})
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("retypes", v.summary)

    def test_null_exempt(self):
        v = _run({"id": 1, "name": None},
                 {"id": 1, "name": "a", "extra": 1})
        self.assertEqual(v.status, "PASS", v.summary)

    def test_empty_list_is_inconclusive(self):
        orig = mod.request_json
        mod.request_json = lambda *a, **kw: (200, {"items": []}, {})
        try:
            v = mod.run_detail_superset_check(
                "http://127.0.0.1/list", "http://127.0.0.1/detail/{id}")
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
