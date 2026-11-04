"""Webhook + cache tests with small in-test servers (not the fixture).

The webhook server in "fixed" mode validates HMAC + timestamp and
dedups by event id; "naive" accepts everything and applies every
delivery. The cache server in "fixed" mode serves the written value;
"naive" keeps serving the old value after a write.
"""

import hashlib
import hmac
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.cache import run_cache_check
from apiverdict.webhook import run_webhook_check

SECRET = "test-webhook-secret"


def _make_server(mode, kind):
    state = {"applied": {}, "served": {"theme": "light"}, "actual": {"theme": "light"}}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
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
            if kind == "webhook":
                eid = self.headers.get("X-Event-Id", "")
                if mode == "fixed":
                    ts = self.headers.get("X-Timestamp", "")
                    msg = f"{ts}.{eid}.{json.dumps(body, sort_keys=True)}".encode()
                    exp = "sha256=" + hmac.new(
                        SECRET.encode(), msg, hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(self.headers.get("X-Signature-256", ""), exp):
                        self._send(401, {"error": "bad_signature"})
                        return
                    try:
                        if abs(int(time.time()) - int(ts)) > 300:
                            self._send(401, {"error": "stale"})
                            return
                    except ValueError:
                        self._send(401, {"error": "bad_ts"})
                        return
                    if eid in state["applied"]:
                        self._send(200, {"ok": True, "duplicate": True})
                        return
                    state["applied"][eid] = 1
                    self._send(200, {"ok": True})
                    return
                state["applied"][eid] = state["applied"].get(eid, 0) + 1
                self._send(200, {"ok": True})
                return
            # cache write
            state["actual"] = {"theme": body.get("theme", "dark")}
            if mode == "fixed":
                state["served"] = dict(state["actual"])
            self._send(200, {"ok": True})

        def do_GET(self):  # noqa: N802
            query = dict(parse_qsl(urlparse(self.path).query))
            if kind == "webhook":
                self._send(200, {"count": state["applied"].get(query.get("event_id", ""), 0)})
            else:
                self._send(200, dict(state["served"]))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class TestWebhook(unittest.TestCase):
    def test_fixed_passes_with_verify(self):
        server, base = _make_server("fixed", "webhook")
        try:
            verdict = run_webhook_check(
                url=base, secret=SECRET, event_id="evt-1",
                verify_url=f"{base}/count?event_id=evt-1")
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "PASS")

    def test_naive_fails(self):
        server, base = _make_server("naive", "webhook")
        try:
            verdict = run_webhook_check(
                url=base, secret=SECRET, event_id="evt-1",
                verify_url=f"{base}/count?event_id=evt-1")
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "FAIL")
        self.assertIn("tampered", verdict.summary)

class TestWebhookFutureTimestamp(unittest.TestCase):
    def test_future_dated_event_rejected_by_fixed(self):
        server, base = _make_server("fixed", "webhook")
        try:
            verdict = run_webhook_check(
                url=base, secret=SECRET, event_id="evt-future",
                verify_url=f"{base}/count?event_id=evt-future")
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(verdict.evidence["future_status"], 401)

    def test_future_dated_event_flagged_on_naive(self):
        server, base = _make_server("naive", "webhook")
        try:
            verdict = run_webhook_check(
                url=base, secret=SECRET, event_id="evt-future-naive",
                verify_url=f"{base}/count?event_id=evt-future-naive")
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "FAIL")
        self.assertIn("future-dated", verdict.summary)


class TestCache(unittest.TestCase):
    def test_fixed_passes(self):
        server, base = _make_server("fixed", "cache")
        try:
            verdict = run_cache_check(read_url=base, write_payload={"theme": "dark"})
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "PASS")

    def test_naive_stale_fails(self):
        server, base = _make_server("naive", "cache")
        try:
            verdict = run_cache_check(read_url=base, write_payload={"theme": "dark"})
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(verdict.status, "FAIL")


if __name__ == "__main__":
    unittest.main()


class TestDottedCompareField(unittest.TestCase):
    def test_nested_version_detected(self):
        import apiverdict.cache as mod
        state = {"version": 1}
        orig = mod.request_json

        def fake(method, url, payload=None, headers=None, timeout=10.0):
            if method == "GET":
                return 200, {"meta": {"version": state["version"]}}, {}
            state["version"] += 1
            return 200, {}, {}

        mod.request_json = fake
        try:
            verdict = mod.run_cache_check(
                read_url="http://127.0.0.1:9/x",
                compare_field="meta.version")
        finally:
            mod.request_json = orig
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["before"], 1)
        self.assertEqual(verdict.evidence["after"], 2)


class TestCacheWriteMethodValidation(unittest.TestCase):
    def test_get_write_is_inconclusive(self):
        from apiverdict.cache import run_cache_check
        from apiverdict.cache_etag import run_cache_etag_check
        for fn, name in ((run_cache_check, "cache"),
                         (run_cache_etag_check, "cache-etag")):
            v = fn("http://127.0.0.1:9/read", write_method="GET",
                   write_url="http://127.0.0.1:9/write", allow_remote=True)
            self.assertEqual(v.status, "INCONCLUSIVE", name)
            self.assertIn("GET", v.summary)


class TestCacheWriteMethodCliValidation(unittest.TestCase):
    def test_get_write_method_exits_2(self):
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "apiverdict", "cache",
             "--read-url", "http://127.0.0.1:9/read",
             "--write-method", "GET"],
            capture_output=True, text=True, cwd=".")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("write-method", proc.stderr)
