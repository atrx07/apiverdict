"""Error-leak + cache-etag checks (milestone 8)."""

import hashlib
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.cache_etag import run_cache_etag_check
from apiverdict.error_leak import run_error_leak_check


def _server(mode, kind):
    state = {"served": {"theme": "light"}, "actual": {"theme": "light"}}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status, payload, extra=None):
            body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _read(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw.decode()) if raw else {}
            except ValueError:
                return {}

        def do_POST(self):  # noqa: N802
            body = self._read()
            if kind == "leak":
                if mode == "naive":
                    self._send(500, "Traceback (most recent call last):\n"
                                    '  File "/home/app/server.py", line 42\n'
                                    "ValueError: bad payload")
                else:
                    self._send(400, {"error": "invalid", "code": "BAD_REQUEST"})
                return
            state["actual"] = {"theme": body.get("theme", "dark")}
            if mode == "fixed":
                state["served"] = dict(state["actual"])
            self._send(200, {"ok": True})

        def do_GET(self):  # noqa: N802
            if kind == "leak":
                self._send(400, {"error": "invalid"})
                return
            served = state["served"]
            etag = '"' + hashlib.sha256(
                json.dumps(served, sort_keys=True).encode()).hexdigest()[:12] + '"'
            if mode == "fixed":
                if self.headers.get("If-None-Match") == etag:
                    self._send(304, "")
                    return
                self._send(200, served, {"ETag": etag})
            else:
                # naive: always the same ETag, ignores If-None-Match
                self._send(200, served, {"ETag": '"naive-etag"'})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class TestErrorLeak(unittest.TestCase):
    def test_naive_leaks_traceback_and_path(self):
        server, base = _server("naive", "leak")
        try:
            v = run_error_leak_check(
                targets=[{"method": "POST", "url": base}],
                invalid_payload={"__invalid__": True})
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(v.status, "FAIL")
        self.assertIn("traceback", v.summary)

    def test_fixed_clean_passes(self):
        server, base = _server("fixed", "leak")
        try:
            v = run_error_leak_check(
                targets=[{"method": "POST", "url": base}],
                invalid_payload={"__invalid__": True})
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(v.status, "PASS")

    def test_no_endpoints_inconclusive(self):
        v = run_error_leak_check(targets=[])
        self.assertEqual(v.status, "INCONCLUSIVE")


class TestCacheEtag(unittest.TestCase):
    def test_fixed_passes(self):
        server, base = _server("fixed", "etag")
        try:
            v = run_cache_etag_check(read_url=base, write_payload={"theme": "dark"})
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(v.status, "PASS")

    def test_naive_fails(self):
        server, base = _server("naive", "etag")
        try:
            v = run_cache_etag_check(read_url=base, write_payload={"theme": "dark"})
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(v.status, "FAIL")
        self.assertIn("If-None-Match", v.summary)

    def test_remote_write_refused(self):
        v = run_cache_etag_check(
            read_url="https://staging.example.invalid/api/s",
            write_url="https://staging.example.invalid/api/s")
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()


class TestErrorLeakHeaders(unittest.TestCase):
    def test_traceback_in_header_fails(self):
        import apiverdict.error_leak as el
        orig = el.request_json
        el.request_json = lambda *a, **kw: (
            500, {"error": "boom"},
            {"X-Debug-Trace": "Traceback (most recent call last): ..."})
        try:
            v = el.run_error_leak_check(
                [{"method": "POST", "url": "http://127.0.0.1/api/book"}])
        finally:
            el.request_json = orig
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertIn("X-Debug-Trace", v.summary)

    def test_clean_headers_pass(self):
        import apiverdict.error_leak as el
        orig = el.request_json
        el.request_json = lambda *a, **kw: (
            400, {"error": "bad input"},
            {"Content-Type": "application/json", "X-Request-Id": "abc-123"})
        try:
            v = el.run_error_leak_check(
                [{"method": "POST", "url": "http://127.0.0.1/api/book"}])
        finally:
            el.request_json = orig
        self.assertEqual(v.status, "PASS", v.summary)


class TestErrorLeakMethodValidation(unittest.TestCase):
    def test_typo_method_is_inconclusive(self):
        import apiverdict.error_leak as mod
        orig = mod.request_json
        calls = []
        mod.request_json = lambda *a, **kw: (calls.append(a), (400, {}, {}))[1]
        try:
            v = mod.run_error_leak_check(
                targets=[{"method": "PST", "url": "http://127.0.0.1:9/api/x"}])
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)
        self.assertIn("PST", v.summary)
        self.assertEqual(calls, [])
