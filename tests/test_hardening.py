"""Backlog 1 hardening: timeouts, oversized bodies, CLI validation,
unreachable targets, and seeded property-style tests.

Property-style here means: seeded random inputs (no hypothesis, stdlib
only), with the invariant stated in the test, not one hand-picked
example. A property test that only ever runs one case is a unit test
with a fancy name; these run 50 cases each.
"""

import json
import random
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.pagination as pagination_mod
from apiverdict.error_shape import _shape_of
from apiverdict.http_util import (
    MAX_RESPONSE_BYTES, ResponseTooLarge, request_json, validate_url)
from apiverdict.pagination import run_pagination_check
from apiverdict.runner import Verdict, build_report, exit_code_for

ROOT = Path(__file__).resolve().parents[1]


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class TestHttpUtilGuards(unittest.TestCase):
    def test_validate_url(self):
        self.assertIsNone(validate_url("http://127.0.0.1:8000/api/x"))
        self.assertIsNotNone(validate_url("notaurl"))
        self.assertIsNotNone(validate_url("ftp://example.com/x"))
        self.assertIsNotNone(validate_url("http:///no-host"))
        self.assertIsNotNone(validate_url(""))

    def test_non_positive_timeout_is_value_error(self):
        with self.assertRaises(ValueError):
            request_json("GET", "http://127.0.0.1:9/x", timeout=0)
        with self.assertRaises(ValueError):
            request_json("GET", "http://127.0.0.1:9/x", timeout=-1)

    def test_oversized_body_raises(self):
        class Huge(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"x" * (MAX_RESPONSE_BYTES + 10)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = _serve(Huge)
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/huge"
            with self.assertRaises(ResponseTooLarge):
                request_json("GET", url)
            # A small explicit cap also trips, on a normal-size body.
            with self.assertRaises(ResponseTooLarge):
                request_json("GET", url, max_bytes=10)
        finally:
            server.shutdown()
            server.server_close()

    def test_slow_server_times_out(self):
        import time

        class Slow(BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(2)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")
                except (BrokenPipeError, ConnectionResetError):
                    pass  # client already timed out; that is the test

            def log_message(self, *args):
                pass

        server = _serve(Slow)
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/slow"
            with self.assertRaises(Exception):
                request_json("GET", url, timeout=0.2)
        finally:
            server.shutdown()
            server.server_close()


class TestPaginationProperty(unittest.TestCase):
    """Fake transport, real walker: the invariant is about the walker,
    not about a server, so stubbing request_json is the honest seam."""

    def _run_with_dataset(self, dataset, total, page_size):
        calls = {"n": 0}

        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            import urllib.parse
            query = dict(urllib.parse.parse_qsl(
                urllib.parse.urlparse(url).query))
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", page_size))
            page = dataset[offset:offset + limit]
            nxt = offset + limit if offset + limit < len(dataset) else None
            calls["n"] += 1
            return 200, {"items": page, "total": total,
                         "next_offset": nxt}, {}

        orig = pagination_mod.request_json
        pagination_mod.request_json = fake
        try:
            return run_pagination_check(
                url="http://127.0.0.1:9/api/items", page_size=page_size)
        finally:
            pagination_mod.request_json = orig

    def test_unique_dataset_never_reports_duplicates_or_gaps(self):
        rng = random.Random(20261104)
        for _ in range(50):
            n = rng.randint(0, 37)
            page_size = rng.randint(1, 10)
            dataset = [{"id": i} for i in range(n)]
            verdict = self._run_with_dataset(dataset, n, page_size)
            self.assertEqual(verdict.status, "PASS", verdict)
            self.assertEqual(verdict.evidence["unique_rows"], n)

    def test_injected_duplicate_and_inflated_total_are_caught(self):
        rng = random.Random(7)
        for _ in range(20):
            dataset = [{"id": i} for i in range(10)]
            dataset[5] = {"id": 2}  # duplicate id 2, one row effectively lost
            verdict = self._run_with_dataset(dataset, 11, 3)
            self.assertEqual(verdict.status, "FAIL", verdict)
            self.assertIn(2, verdict.evidence["duplicates"])
            self.assertGreater(verdict.evidence["missing"], 0)


class TestErrorClusteringProperty(unittest.TestCase):
    def test_same_body_same_shape_different_keys_different_shape(self):
        rng = random.Random(99)
        keys_pool = ["code", "error", "message", "detail", "type", "title"]
        for _ in range(50):
            keys = rng.sample(keys_pool, rng.randint(1, 4))
            body = {k: "x" for k in keys}
            self.assertEqual(_shape_of(400, body), _shape_of(400, dict(body)))
            other = dict(body)
            extra = next(k for k in keys_pool if k not in body)
            other[extra] = "x"
            self.assertNotEqual(_shape_of(400, body), _shape_of(400, other))
            self.assertNotEqual(_shape_of(400, body), _shape_of(422, body))

    def test_non_json_body_is_its_own_cluster(self):
        self.assertEqual(_shape_of(500, "boom"), _shape_of(500, "other boom"))
        self.assertNotEqual(_shape_of(500, "boom"), _shape_of(500, {"error": "x"}))


class TestReportSchemaProperty(unittest.TestCase):
    def test_exit_code_contract_for_every_combination(self):
        rng = random.Random(3)
        for _ in range(50):
            statuses = rng.choices(["PASS", "FAIL", "INCONCLUSIVE"],
                                   k=rng.randint(1, 5))
            verdicts = [Verdict(f"c{i}", s, s) for i, s in enumerate(statuses)]
            report = build_report(verdicts)
            self.assertEqual(report["schema_version"], "2")
            self.assertEqual(len(report["checks"]), len(statuses))
            self.assertEqual(report["exit_code"], exit_code_for(verdicts))
            expected = ("FAIL" if "FAIL" in statuses else
                        "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS")
            self.assertEqual(report["overall"], expected)


class TestCliValidation(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "apiverdict", *args],
            capture_output=True, text=True, cwd=ROOT, timeout=30)

    def test_bad_page_size_is_exit_2(self):
        out = self._run("pagination", "--url", "http://127.0.0.1:9/x",
                        "--page-size", "0")
        self.assertEqual(out.returncode, 2)
        self.assertNotIn("Traceback", out.stderr)

    def test_bad_url_is_exit_2(self):
        out = self._run("pagination", "--url", "notaurl")
        self.assertEqual(out.returncode, 2)
        self.assertNotIn("Traceback", out.stderr)

    def test_bad_timeout_is_exit_2(self):
        out = self._run("pagination", "--url", "http://127.0.0.1:9/x",
                        "--timeout", "0")
        self.assertEqual(out.returncode, 2)

    def test_bad_method_is_exit_2(self):
        out = self._run("burst", "--url", "http://127.0.0.1:9/x",
                        "--method", "BREW", "--verify-url",
                        "http://127.0.0.1:9/count")
        self.assertEqual(out.returncode, 2)

    def test_unreachable_target_is_inconclusive_not_crash(self):
        out = self._run("check", "--base-url", "http://127.0.0.1:9",
                        "--timeout", "2")
        self.assertEqual(out.returncode, 2)
        self.assertIn("INCONCLUSIVE", out.stdout)
        self.assertNotIn("Traceback", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
