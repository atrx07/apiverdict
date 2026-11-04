"""If-Match (optimistic concurrency) check."""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.if_match import run_if_match_check


def _server(mode):
    state = {"served": {"theme": "light"}, "version": 1}

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

        def _etag(self):
            return '"v%d"' % state["version"]

        def do_GET(self):  # noqa: N802
            if mode == "no-etag":
                self._send(200, state["served"])
            else:
                self._send(200, state["served"], {"ETag": self._etag()})

        def do_PATCH(self):  # noqa: N802
            body = self._read()
            current = self._etag()
            if_match = self.headers.get("If-Match")
            if mode == "ignoring":
                # Applies every write, precondition or not.
                state["served"] = {"theme": body.get("theme", "dark")}
                state["version"] += 1
                self._send(200, {"ok": True})
            elif mode == "lying":
                # Rejects the stale write by status, but applies it anyway.
                if if_match != current:
                    state["served"] = {"theme": body.get("theme", "dark")}
                    state["version"] += 1
                    self._send(412, {"error": "precondition failed"})
                else:
                    state["served"] = {"theme": body.get("theme", "dark")}
                    state["version"] += 1
                    self._send(200, {"ok": True})
            elif mode == "rejects-current":
                self._send(412, {"error": "precondition failed"})
            elif mode == "noop":
                self._send(200, {"ok": True})
            elif mode == "conflict":
                if if_match != current:
                    self._send(409, {"error": "conflict"})
                else:
                    state["served"] = {"theme": body.get("theme", "dark")}
                    state["version"] += 1
                    self._send(200, {"ok": True})
            else:  # enforcing
                if if_match != current:
                    self._send(412, {"error": "precondition failed"})
                else:
                    state["served"] = {"theme": body.get("theme", "dark")}
                    state["version"] += 1
                    self._send(200, {"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _run(mode, **kw):
    server, base = _server(mode)
    try:
        return run_if_match_check(read_url=base,
                                  write_payload={"theme": "dark"}, **kw)
    finally:
        server.shutdown()
        server.server_close()


class TestIfMatch(unittest.TestCase):
    def test_enforcing_passes(self):
        v = _run("enforcing")
        self.assertEqual(v.status, "PASS")
        self.assertEqual(v.evidence["stale_write_status"], 412)

    def test_ignoring_fails(self):
        v = _run("ignoring")
        self.assertEqual(v.status, "FAIL")
        self.assertIn("stale If-Match accepted", v.summary)

    def test_lying_fails_on_final_readback(self):
        v = _run("lying")
        self.assertEqual(v.status, "FAIL")
        self.assertIn("state changed anyway", v.summary)

    def test_rejects_current_fails(self):
        v = _run("rejects-current")
        self.assertEqual(v.status, "FAIL")
        self.assertIn("current If-Match rejected", v.summary)

    def test_no_etag_inconclusive(self):
        v = _run("no-etag")
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("no ETag", v.summary)

    def test_noop_write_inconclusive(self):
        v = _run("noop")
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("no-op", v.summary)

    def test_conflict_refusal_passes(self):
        v = _run("conflict")
        self.assertEqual(v.status, "PASS")
        self.assertEqual(v.evidence["stale_write_status"], 409)

    def test_read_method_write_inconclusive(self):
        v = _run("enforcing", write_method="GET")
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("must mutate", v.summary)

    def test_remote_write_refused(self):
        v = run_if_match_check(
            read_url="https://staging.example.invalid/api/s",
            write_url="https://staging.example.invalid/api/s")
        self.assertEqual(v.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
