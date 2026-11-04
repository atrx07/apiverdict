"""Cycle 4 guards: malformed pagination metadata must not crash the
walker, and unparseable Retry-After values must fail the rate-limit
check (RFC 9110: delay-seconds or an HTTP-date, nothing else)."""

import json
import sys
import threading
import unittest
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apiverdict.pagination as pagination_mod
from apiverdict.pagination import run_pagination_check
from apiverdict.rate_limit import run_rate_limit_check


def _fake_pagination(meta):
    def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
        return 200, {"items": [{"id": 1}], **meta}, {}

    return fake


class TestPaginationMetadataGuards(unittest.TestCase):
    def test_unparseable_total_is_inconclusive_not_crash(self):
        orig = pagination_mod.request_json
        pagination_mod.request_json = _fake_pagination(
            {"total": "soon", "next_offset": None})
        try:
            verdict = run_pagination_check(url="http://127.0.0.1:9/api/items")
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("total is not an integer", verdict.summary)

    def test_digit_string_total_is_coerced_and_judged(self):
        # Cycle 210 overturns cycle 4 for digit strings: "30"
        # means exactly 30, so 1 collected row against total 30
        # is judged (missing 29 -> FAIL), not inconclusive.
        orig = pagination_mod.request_json
        pagination_mod.request_json = _fake_pagination(
            {"total": "30", "next_offset": None})
        try:
            verdict = run_pagination_check(url="http://127.0.0.1:9/api/items")
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "FAIL", verdict.summary)
        self.assertIn("missing", verdict.summary)

    def test_string_next_offset_is_inconclusive_not_crash(self):
        orig = pagination_mod.request_json
        pagination_mod.request_json = _fake_pagination(
            {"total": 1, "next_offset": "soon"})
        try:
            verdict = run_pagination_check(url="http://127.0.0.1:9/api/items")
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("next_offset is not an integer", verdict.summary)

    def test_non_string_cursor_is_inconclusive_not_crash(self):
        orig = pagination_mod.request_json
        pagination_mod.request_json = _fake_pagination(
            {"total": 1, "next_cursor": 42})
        try:
            verdict = run_pagination_check(url="http://127.0.0.1:9/api/items")
        finally:
            pagination_mod.request_json = orig
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("next_cursor is not a string", verdict.summary)


def _rate_server(retry_after):
    state = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            state["n"] += 1
            if state["n"] > 5:
                body = json.dumps({"error": "rate_limited"}).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Retry-After", retry_after)
                self.end_headers()
                self.wfile.write(body)
            else:
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class TestRetryAfterParsing(unittest.TestCase):
    def test_garbage_retry_after_fails(self):
        server, base = _rate_server("soon")
        try:
            verdict = run_rate_limit_check(url=base, requests=10)
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "FAIL")
        self.assertIn("Retry-After", verdict.summary)
        self.assertEqual(verdict.evidence["retry_after_invalid"], ["soon"])

    def test_integer_retry_after_passes(self):
        server, base = _rate_server("60")
        try:
            verdict = run_rate_limit_check(url=base, requests=10)
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "PASS")

    def test_http_date_retry_after_passes(self):
        server, base = _rate_server(formatdate(usegmt=True))
        try:
            verdict = run_rate_limit_check(url=base, requests=10)
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "PASS")


if __name__ == "__main__":
    unittest.main()


class TestRateLimitHeaders(unittest.TestCase):
    def test_limit_headers_in_evidence(self):
        import apiverdict.rate_limit as mod
        orig = mod.request_json
        calls = {"n": 0}

        def fake(method, url, payload=None, headers=None, timeout=10.0):
            calls["n"] += 1
            if calls["n"] <= 5:
                return 200, {}, {"X-RateLimit-Limit": "5",
                                 "X-RateLimit-Remaining": str(5 - calls["n"])}
            return 429, {}, {"Retry-After": "60"}

        mod.request_json = fake
        try:
            verdict = mod.run_rate_limit_check("http://127.0.0.1:9/x",
                                               requests=10)
        finally:
            mod.request_json = orig
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["rate_limit_headers"],
                         {"X-RateLimit-Limit": "5",
                          "X-RateLimit-Remaining": "4"})


class TestPaginationStyleValidation(unittest.TestCase):
    def test_unknown_style_is_inconclusive_not_a_walk(self):
        import apiverdict.pagination as mod
        verdict = mod.run_pagination_check(
            url="http://127.0.0.1:9/x", style="bogus")
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("unknown pagination style", verdict.summary)

    def test_cli_rejects_unknown_style(self):
        import subprocess
        import sys
        from pathlib import Path
        out = subprocess.run(
            [sys.executable, "-m", "apiverdict", "pagination",
             "--url", "http://127.0.0.1:9/x", "--style", "bogus"],
            capture_output=True, text=True, cwd=Path(__file__).parents[1],
            timeout=30)
        self.assertEqual(out.returncode, 2)


class TestMutatingGuards(unittest.TestCase):
    def test_rate_limit_post_remote_refused(self):
        from apiverdict.rate_limit import run_rate_limit_check
        v = run_rate_limit_check("https://example.com/api/search",
                                 method="POST", requests=5)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("allow-remote", v.summary)

    def test_rate_limit_get_remote_not_gated(self):
        # GET reads are not writes: the guard must not fire. The
        # verdict itself may be FAIL (no limiting observed) or
        # INCONCLUSIVE (transport) depending on egress — either
        # proves the guard did not refuse the run.
        from apiverdict.rate_limit import run_rate_limit_check
        v = run_rate_limit_check("https://example.com/api/search",
                                 method="GET", requests=5, timeout=2)
        self.assertNotIn("allow-remote", v.summary)

    def test_concurrent_remote_write_refused(self):
        from apiverdict.pagination_concurrent import (
            run_pagination_concurrent_check)
        v = run_pagination_concurrent_check(
            "http://127.0.0.1:9/api/items",
            write_url="https://example.com/api/book")
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("allow-remote", v.summary)


class TestRateLimitStandardHeaders(unittest.TestCase):
    def test_ietf_headers_recorded(self):
        import apiverdict.rate_limit as mod
        orig = mod.request_json
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            return 200, {}, {"RateLimit-Limit": "100",
                             "RateLimit-Remaining": "99",
                             "RateLimit-Reset": "60"}
        mod.request_json = fake
        try:
            v = mod.run_rate_limit_check("http://127.0.0.1:9/api/x",
                                         requests=3)
        finally:
            mod.request_json = orig
        self.assertEqual(v.evidence["rate_limit_headers"]["RateLimit-Limit"],
                         "100")

    def test_combined_ratelimit_header_recorded(self):
        import apiverdict.rate_limit as mod
        orig = mod.request_json
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            return 200, {}, {"RateLimit": '"default";r=15;t=23',
                             "RateLimit-Policy": '"default";q=100;w=60'}
        mod.request_json = fake
        try:
            v = mod.run_rate_limit_check("http://127.0.0.1:9/api/x",
                                         requests=3)
        finally:
            mod.request_json = orig
        self.assertEqual(v.evidence["rate_limit_headers"]["RateLimit"],
                         '"default";r=15;t=23')
        self.assertEqual(v.evidence["rate_limit_headers"]["RateLimit-Policy"],
                         '"default";q=100;w=60')


class TestRateLimitMethodValidation(unittest.TestCase):
    def test_typo_method_is_inconclusive(self):
        import apiverdict.rate_limit as mod
        orig = mod.request_json
        calls = []
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            calls.append(method)
            return 200, {}, {}
        mod.request_json = fake
        try:
            v = mod.run_rate_limit_check("http://127.0.0.1:9/api/x",
                                         method="FETCH", requests=3)
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)
        self.assertIn("FETCH", v.summary)
        self.assertEqual(calls, [])
