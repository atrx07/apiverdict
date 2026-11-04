"""CLI webhook flags: --timestamp-tolerance and
--no-enforce-timestamp must reach run_webhook_check."""
import hashlib
import hmac
import json
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = "test-secret"


def _make_server(enforce):
    seen = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            sig = self.headers.get("X-Signature-256", "")
            ts = self.headers.get("X-Timestamp", "")
            eid = self.headers.get("X-Event-Id", "")
            msg = f"{ts}.{eid}.{json.dumps(body, sort_keys=True)}".encode()
            expected = "sha256=" + hmac.new(
                SECRET.encode(), msg, hashlib.sha256).hexdigest()
            status = 200
            if not hmac.compare_digest(sig, expected):
                status = 401
            elif enforce:
                try:
                    if abs(int(ts) - int(time.time())) > 300:
                        status = 401
                except ValueError:
                    status = 401
            if status == 200:
                seen.add(eid)
            payload = json.dumps({"ok": True}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _run_cli(port, extra):
    proc = subprocess.run(
        [sys.executable, "-m", "apiverdict", "webhook",
         "--url", f"http://127.0.0.1:{port}/hook",
         "--secret", SECRET, *extra],
        capture_output=True, text=True, cwd=".", timeout=60)
    return proc


class TestWebhookCliFlags(unittest.TestCase):
    def test_enforcing_handler_passes_with_defaults(self):
        server = _make_server(enforce=True)
        try:
            proc = _run_cli(server.server_address[1], [])
        finally:
            server.shutdown()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_non_enforcing_handler_fails_by_default(self):
        server = _make_server(enforce=False)
        try:
            proc = _run_cli(server.server_address[1], [])
        finally:
            server.shutdown()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("stale replay accepted", proc.stdout)

    def test_no_enforce_flag_passes_non_enforcing_handler(self):
        server = _make_server(enforce=False)
        try:
            proc = _run_cli(server.server_address[1],
                            ["--no-enforce-timestamp"])
        finally:
            server.shutdown()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()


class TestWebhookToleranceValidation(unittest.TestCase):
    def test_negative_tolerance_exits_2_before_network(self):
        proc = subprocess.run(
            [sys.executable, "-m", "apiverdict", "webhook",
             "--url", "http://127.0.0.1:9/hook", "--secret", "x",
             "--timestamp-tolerance", "-5"],
            capture_output=True, text=True, cwd=".", timeout=30)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("timestamp-tolerance", proc.stderr)


class TestWebhookBodyFlag(unittest.TestCase):
    def test_non_object_body_is_inconclusive(self):
        import apiverdict.webhook as wh
        v = wh.run_webhook_check("http://127.0.0.1:9/hook", secret="x",
                                 body=["not", "an", "object"])
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("JSON object", v.summary)

    def test_body_flag_parses(self):
        proc = subprocess.run(
            [sys.executable, "-m", "apiverdict", "webhook", "--help"],
            capture_output=True, text=True, cwd=".")
        self.assertIn("--body", proc.stdout)


class TestWebhookSchemeFlag(unittest.TestCase):
    def test_scheme_stripe_reaches_check(self):
        # Fake Stripe-scheme handler: verifies t=,v1= over
        # f"{ts}.{body_json}" with the event id as body id.
        # Without the dispatch fix, the CLI would sign the
        # apiverdict scheme and this handler would 401 everything.
        import json as _json
        import threading as _th
        from http.server import BaseHTTPRequestHandler as _B, HTTPServer as _S

        class H(_B):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                body = _json.loads(raw or b"{}")
                sig = self.headers.get("Stripe-Signature", "")
                ts = sig.split("t=")[1].split(",")[0] if "t=" in sig else ""
                msg = f"{ts}.{raw.decode()}".encode()
                exp = "v1=" + hmac.new(
                    SECRET.encode(), msg, hashlib.sha256).hexdigest()
                status = 200 if f"v1={exp[3:]}" in sig or exp in sig else 401
                if body.get("tampered"):
                    status = 401
                p = _json.dumps({"ok": True}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(p)))
                self.end_headers()
                self.wfile.write(p)

            def do_GET(self):
                p = _json.dumps({"count": 1}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(p)))
                self.end_headers()
                self.wfile.write(p)

        srv = _S(("127.0.0.1", 0), H)
        _th.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "apiverdict", "webhook",
                 "--url", f"http://127.0.0.1:{srv.server_address[1]}/hook",
                 "--secret", SECRET, "--scheme", "stripe",
                 "--no-enforce-timestamp",
                 "--verify-url",
                 f"http://127.0.0.1:{srv.server_address[1]}/count"],
                capture_output=True, text=True, cwd=".", timeout=60)
        finally:
            srv.shutdown()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestWebhookToleranceGuard(unittest.TestCase):
    def test_negative_tolerance_is_inconclusive(self):
        import apiverdict.webhook as wh
        v = wh.run_webhook_check("http://127.0.0.1:9/hook", secret="x",
                                 timestamp_tolerance_s=-5)
        self.assertEqual(v.status, "INCONCLUSIVE")


class TestSecretPrecedence(unittest.TestCase):
    def test_both_secret_sources_exit_2(self):
        import subprocess
        import sys
        from pathlib import Path
        root = Path(__file__).parents[1]
        for cmd in (["webhook", "--url", "http://127.0.0.1:9/hook",
                     "--secret", "x", "--secret-env", "NOPE"],
                    ["auth-schemes", "--url", "http://127.0.0.1:9/me",
                     "--token", "x", "--token-env", "NOPE"]):
            out = subprocess.run([sys.executable, "-m", "apiverdict"] + cmd,
                                 capture_output=True, text=True, cwd=root,
                                 timeout=30)
            self.assertEqual(out.returncode, 2, out)
            self.assertIn("exactly one", out.stderr)
