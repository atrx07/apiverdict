"""Auth-matrix POST payload support (milestone 9).

A POST target with no body 400s for every role, which the matrix
would report as "unexpected" — a body problem misread as an auth
problem. With a payload, the matrix judges authorization only.
"""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.auth_matrix import run_auth_matrix_check


def _server():
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

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode()) if raw else {}
            except ValueError:
                body = {}
            if body.get("action") != "promote":
                self._send(400, {"error": "bad_body"})
                return
            if self.headers.get("X-Role") != "admin":
                self._send(403, {"error": "forbidden"})
                return
            self._send(200, {"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


ROLES = [{"name": "admin", "headers": {"X-Role": "admin"}},
         {"name": "user", "headers": {"X-Role": "user"}}]


class TestAuthPayload(unittest.TestCase):
    def test_post_with_payload_judges_auth(self):
        server, base = _server()
        try:
            v = run_auth_matrix_check(
                roles=ROLES,
                targets=[{"method": "POST", "url": base,
                          "allowed_roles": ["admin"],
                          "payload": {"action": "promote"}}])
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(v.status, "PASS")

    def test_post_without_payload_is_unexpected_not_leak(self):
        server, base = _server()
        try:
            v = run_auth_matrix_check(
                roles=ROLES,
                targets=[{"method": "POST", "url": base,
                          "allowed_roles": ["admin"]}])
        finally:
            server.shutdown(); server.server_close()
        self.assertEqual(v.status, "FAIL")
        self.assertIn("unexpected", v.summary)
        self.assertNotIn("leak", v.summary)


if __name__ == "__main__":
    unittest.main()


class TestAuthMatrixMethodValidation(unittest.TestCase):
    def test_typoed_method_is_inconclusive_not_sent(self):
        v = run_auth_matrix_check(
            [{"name": "admin", "headers": {}}],
            [{"method": "FETCH", "url": "http://127.0.0.1:9/x",
             "allowed_roles": ["admin"]}])
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("FETCH", v.summary)

    def test_lowercase_method_accepted(self):
        # Method normalisation happens at send time via request_json;
        # validation must not reject a lowercase "get".
        import apiverdict.auth_matrix as am
        orig = am.request_json
        am.request_json = lambda *a, **kw: (200, {}, {})
        try:
            v = run_auth_matrix_check(
                [{"name": "admin", "headers": {}}],
                [{"method": "get", "url": "http://127.0.0.1/x",
                 "allowed_roles": ["admin"]}])
        finally:
            am.request_json = orig
        self.assertEqual(v.status, "PASS", v.summary)


class TestAuthMatrixRemoteGuard(unittest.TestCase):
    def test_post_target_remote_refused(self):
        v = run_auth_matrix_check(
            [{"name": "admin", "headers": {}}],
            [{"method": "POST", "url": "https://example.com/api/book",
             "allowed_roles": ["admin"]}])
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("allow-remote", v.summary)

    def test_post_target_remote_allowed_bypasses_guard(self):
        # With allow_remote, the guard must not fire; the transport
        # is faked so no real POST leaves the test process.
        import apiverdict.auth_matrix as am
        orig = am.request_json
        am.request_json = lambda *a, **kw: (200, {}, {})
        try:
            v = run_auth_matrix_check(
                [{"name": "admin", "headers": {}}],
                [{"method": "POST", "url": "https://example.com/api/book",
                 "allowed_roles": ["admin"]}],
                timeout=5, allow_remote=True)
        finally:
            am.request_json = orig
        self.assertNotIn("allow-remote", v.summary)
        self.assertEqual(v.status, "PASS", v.summary)


class TestQueryMethod(unittest.TestCase):
    def test_query_endpoint_parses(self):
        from apiverdict.__main__ import _parse_endpoint
        self.assertEqual(
            _parse_endpoint("QUERY:http://127.0.0.1/api/search"),
            {"method": "QUERY", "url": "http://127.0.0.1/api/search"})

    def test_query_target_is_read_only(self):
        import apiverdict.auth_matrix as am
        orig = am.request_json
        am.request_json = lambda *a, **kw: (200, {}, {})
        try:
            v = run_auth_matrix_check(
                [{"name": "admin", "headers": {}}],
                [{"method": "QUERY", "url": "https://example.com/api/search",
                 "allowed_roles": ["admin"]}])
        finally:
            am.request_json = orig
        self.assertNotIn("allow-remote", v.summary)
        self.assertEqual(v.status, "PASS", v.summary)


class TestAuthMatrixUnknownRole(unittest.TestCase):
    def test_unknown_allowed_role_is_inconclusive(self):
        import apiverdict.auth_matrix as mod
        orig = mod.request_json
        calls = []
        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            calls.append(url)
            return 200, {}, {}
        mod.request_json = fake
        try:
            v = mod.run_auth_matrix_check(
                roles=[{"name": "admin", "headers": {}}],
                targets=[{"method": "GET", "url": "http://127.0.0.1:9/api/x",
                          "allowed_roles": ["admn"]}])
        finally:
            mod.request_json = orig
        self.assertEqual(v.status, "INCONCLUSIVE", v.summary)
        self.assertIn("admn", v.summary)
        self.assertEqual(calls, [], "no network call before config validation")
