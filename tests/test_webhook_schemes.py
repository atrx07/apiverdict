"""Webhook signing schemes: stripe and github sign the exact bytes
sent (real handlers verify raw bytes), unlike the apiverdict scheme
which signs the sorted parsed body. A fake handler per scheme proves
the check speaks each scheme end to end, including duplicate and
tamper behaviour."""

import hashlib
import hmac
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.webhook import run_webhook_check, sign

SECRET = "whsec-test"


def _make_server(scheme):
    state = {"applied": set()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _read(self):
            n = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(n)

        def do_POST(self):
            raw = self._read()
            try:
                body = json.loads(raw)
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            if scheme == "stripe":
                sig = self.headers.get("Stripe-Signature", "")
                parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
                t = parts.get("t", "")
                msg = f"{t}.{raw.decode()}".encode()
                want = hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()
                if parts.get("v1") != want or abs(int(time.time()) - int(t)) > 300:
                    self.send_response(401)
                    self.end_headers()
                    return
                eid = body.get("id")
            else:  # github
                sig = self.headers.get("X-Hub-Signature-256", "")
                want = "sha256=" + hmac.new(
                    SECRET.encode(), raw, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig, want):
                    self.send_response(401)
                    self.end_headers()
                    return
                eid = self.headers.get("X-GitHub-Delivery")
            state["applied"].add(eid)
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            if urlparse(self.path).path == "/count":
                payload = json.dumps({"count": len(state["applied"])}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class TestWebhookSchemes(unittest.TestCase):
    def test_sign_vectors(self):
        body = {"type": "test.event", "amount": 100}
        stripe = sign(SECRET, 1700000000, "evt-1", body, "stripe")
        want = hmac.new(SECRET.encode(),
                         b"1700000000." + json.dumps(dict(body, id="evt-1")).encode(),
                         hashlib.sha256).hexdigest()
        self.assertEqual(stripe, "t=1700000000,v1=" + want)
        gh = sign(SECRET, 1700000000, "evt-1", body, "github")
        self.assertEqual(gh, "sha256=" + hmac.new(
            SECRET.encode(), json.dumps(body).encode(),
            hashlib.sha256).hexdigest())
        with self.assertRaises(ValueError):
            sign(SECRET, 1, "e", body, "nope")

    def test_stripe_scheme_passes(self):
        server = _make_server("stripe")
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            verdict = run_webhook_check(
                url=f"{base}/hook", secret=SECRET, scheme="stripe",
                verify_url=f"{base}/count")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(verdict.status, "PASS", verdict)

    def test_github_scheme_passes_and_skips_timestamp_probes(self):
        server = _make_server("github")
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            verdict = run_webhook_check(
                url=f"{base}/hook", secret=SECRET, scheme="github",
                verify_url=f"{base}/count")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertIn("skipped", verdict.evidence["timestamp_probes"])

    def test_unknown_scheme_is_inconclusive(self):
        verdict = run_webhook_check(
            url="http://127.0.0.1:9/hook", secret=SECRET, scheme="nope")
        self.assertEqual(verdict.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()


class TestDuplicate409(unittest.TestCase):
    def test_409_duplicate_is_recognised_not_failed(self):
        import apiverdict.webhook as mod
        seen = set()

        def fake_send(url, secret, event_id, body, timestamp, tamper=False,
                      headers=None, scheme="apiverdict"):
            if tamper:
                return 401, {}, {}
            if event_id in seen:
                return 409, {}, {}
            seen.add(event_id)
            return 200, {}, {}

        orig = mod._send
        mod._send = fake_send
        try:
            verdict = run_webhook_check(
                url="http://127.0.0.1:9/hook", secret=SECRET,
                enforce_timestamp=False)
        finally:
            mod._send = orig
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["duplicate_status"], 409)
