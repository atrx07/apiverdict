"""Case-study fixture — a second, more lifelike LOCAL API.

This is NOT a real API and must never be presented as one. It is a
deliberately buggy localhost shop (bookings, products, payments,
settings, webhooks, admin reports) so the full v2 suite can be run
against something richer than the v1 demo fixture, with a documented
list of exactly what each check caught.

Seeded bugs in "naive" mode (all fixed in "fixed" mode):
  1. burst:       duplicate bookings for the same ref (no uniqueness).
  2. pagination:  page 2 of /api/products repeats page 1, AND the
                  second full walk returns rows in reverse order.
  3. error-shape: /api/bookings returns RFC 7807 problem-details,
                  /api/payments returns a plain 422 shape (mixed).
  4. auth-matrix: /api/admin-report leaks to any role.
  5. rate-limit:  /api/search never limits.
  6. idempotency: /api/payments ignores Idempotency-Key.
  7. cache:       /api/settings GET after POST serves the old value.
  8. webhook:     /api/webhooks/receiver accepts tampered bodies,
                  stale replays, and double-applies duplicates.

Run it with:  python -m apiverdict casestudy [--mode naive|fixed|both]
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    """Fixture server with a listen backlog that survives its own
    burst check: the stdlib default request_queue_size is 5, so a
    10-thread burst plus parallel checks overflowed the backlog
    and one connection reset — a fixture artefact, not a burst
    finding. 128 is ample for a localhost demo server."""
    request_queue_size = 128
    daemon_threads = True

from .auth_matrix import run_auth_matrix_check
from .burst import run_burst_check
from .cache import run_cache_check
from .error_shape import run_error_shape_check
from .idempotency import run_idempotency_check
from .pagination import run_pagination_check
from .rate_limit import run_rate_limit_check
from .runner import Verdict, build_report, time_verdict
from .webhook import run_webhook_check

PRODUCTS = [{"id": i, "name": f"product-{i}"} for i in range(1, 25)]
WEBHOOK_SECRET = "case-study-secret"


class CaseState:
    def __init__(self, mode: str):
        if mode not in ("naive", "fixed"):
            raise ValueError("mode must be 'naive' or 'fixed'")
        self.mode = mode
        self.lock = threading.RLock()
        self.bookings: dict[str, int] = {}
        self.naive_rows = 0
        self.payments: dict[str, int] = {}  # order -> payment count
        self.idem: dict[str, int] = {}  # Idempotency-Key -> payment id
        self.rate_count = 0
        self.product_walks = 0
        self.settings_actual = {"theme": "light"}
        self.settings_served = {"theme": "light"}
        self.webhook_applied: dict[str, int] = {}

    def book(self, ref: str) -> tuple[int, dict]:
        with self.lock:
            if self.mode == "fixed" and ref in self.bookings:
                return 409, {"error": "already_booked", "code": "CONFLICT"}
            if self.mode == "naive":
                self.naive_rows += 1
                self.bookings[f"{ref}#{self.naive_rows}"] = self.naive_rows
                return 201, {"booking_id": self.naive_rows, "ref": ref}
            bid = len(self.bookings) + 1
            self.bookings[ref] = bid
            return 201, {"booking_id": bid, "ref": ref}

    def count_bookings(self, ref: str) -> int:
        with self.lock:
            if self.mode == "fixed":
                return 1 if ref in self.bookings else 0
            return sum(1 for k in self.bookings if k.split("#")[0] == ref)


def _verify_webhook_sig(body: dict, headers) -> bool:
    sig = headers.get("X-Signature-256", "")
    ts = headers.get("X-Timestamp", "")
    eid = headers.get("X-Event-Id", "")
    msg = f"{ts}.{eid}.{json.dumps(body, sort_keys=True)}".encode()
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def make_handler(state: CaseState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status, payload, extra=None):
            body = json.dumps(payload).encode()
            self.send_response(status)
            # Problem-details bodies are served with their RFC 9457
            # media type; everything else stays application/json.
            # (The strict error-shape check now requires this, and a
            # reference fixture that claims problem details should
            # actually send them.)
            is_problem = (isinstance(payload, dict) and
                          {"type", "title", "status", "detail"} <= set(payload))
            self.send_header("Content-Type", "application/problem+json"
                             if is_problem else "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode()) if raw else {}
            except ValueError:
                data = {}
            return data if isinstance(data, dict) else {}

        def do_POST(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            body = self._read_json()
            if parsed.path == "/api/bookings" and "ref" in body:
                status, payload = state.book(str(body["ref"]))
                self._send(status, payload)
                return
            if parsed.path == "/api/bookings":
                # Error-shape target, naive = RFC 7807, fixed = RFC 7807 too
                # (fixed mode unifies BOTH endpoints on this shape).
                self._send(400, {"type": "https://example.invalid/bad-request",
                                 "title": "Bad Request", "status": 400,
                                 "detail": "invalid payload"})
                return
            if parsed.path == "/api/payments" and "order" in body:
                order = str(body["order"])
                key = self.headers.get("Idempotency-Key")
                with state.lock:
                    if state.mode == "fixed" and key and key in state.idem:
                        self._send(200, {"payment_id": state.idem[key], "order": order,
                                         "idempotent_replay": True})
                        return
                    pid = len(state.idem) + state.payments.get(order, 0) + 1 \
                        if state.mode == "naive" else len(state.idem) + 1
                    # Naive: every send counts, even with the same key.
                    state.payments[order] = state.payments.get(order, 0) + 1
                    if key:
                        state.idem[key] = pid
                    self._send(201, {"payment_id": pid, "order": order})
                return
            if parsed.path == "/api/payments":
                if state.mode == "fixed":
                    self._send(400, {"type": "https://example.invalid/bad-request",
                                     "title": "Bad Request", "status": 400,
                                     "detail": "invalid payload"})
                else:
                    self._send(422, {"message": "validation failed",
                                     "detail": ["bad payload"]})
                return
            if parsed.path == "/api/settings":
                with state.lock:
                    state.settings_actual = {"theme": body.get("theme", "dark")}
                    if state.mode == "fixed":
                        state.settings_served = dict(state.settings_actual)
                    # naive: served stays at the old value (stale cache)
                self._send(200, {"ok": True})
                return
            if parsed.path == "/api/webhooks/receiver":
                eid = self.headers.get("X-Event-Id", "")
                ts_raw = self.headers.get("X-Timestamp", "0")
                try:
                    ts = int(ts_raw)
                except ValueError:
                    ts = 0
                if state.mode == "fixed":
                    if not _verify_webhook_sig(body, self.headers):
                        self._send(401, {"error": "bad_signature"})
                        return
                    if abs(int(time.time()) - ts) > 300:
                        self._send(401, {"error": "stale_timestamp"})
                        return
                    with state.lock:
                        if eid in state.webhook_applied:
                            self._send(200, {"ok": True, "duplicate": True})
                            return
                        state.webhook_applied[eid] = 1
                    self._send(200, {"ok": True})
                    return
                # naive: accept everything, apply every delivery
                with state.lock:
                    state.webhook_applied[eid] = state.webhook_applied.get(eid, 0) + 1
                self._send(200, {"ok": True})
                return
            self._send(404, {"error": "not_found"})

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            if parsed.path == "/api/bookings/count":
                self._send(200, {"count": state.count_bookings(query.get("ref", ""))})
                return
            if parsed.path == "/api/payments/count":
                self._send(200, {"count": state.payments.get(query.get("order", ""), 0)})
                return
            if parsed.path == "/api/webhooks/count":
                self._send(200, {"count": state.webhook_applied.get(
                    query.get("event_id", ""), 0)})
                return
            if parsed.path == "/api/products":
                offset = int(query.get("offset", "0"))
                limit = int(query.get("limit", "8"))
                if offset == 0:
                    with state.lock:
                        state.product_walks += 1
                        walk_no = state.product_walks
                else:
                    walk_no = state.product_walks
                items = PRODUCTS[offset:offset + limit]
                if state.mode == "naive" and offset == 8:
                    items = PRODUCTS[0:8]
                if state.mode == "naive" and walk_no >= 2:
                    # Second walk returns rows reversed: order instability.
                    # (Only the walk-level order matters; page slices are
                    # still computed from the reversed list.)
                    rev = list(reversed(PRODUCTS))
                    items = rev[offset:offset + limit]
                    if offset == 8:
                        items = rev[0:8]
                nxt = offset + limit if offset + limit < len(PRODUCTS) else None
                self._send(200, {"items": items, "total": len(PRODUCTS),
                                 "next_offset": nxt})
                return
            if parsed.path == "/api/admin-report":
                role = self.headers.get("X-Role", "")
                if state.mode == "fixed" and role != "admin":
                    self._send(403, {"error": "forbidden"})
                else:
                    self._send(200, {"ok": True})
                return
            if parsed.path == "/api/search":
                with state.lock:
                    state.rate_count += 1
                    count = state.rate_count
                if state.mode == "fixed" and count > 5:
                    self._send(429, {"error": "rate_limited"}, {"Retry-After": "60"})
                else:
                    self._send(200, {"ok": True})
                return
            if parsed.path == "/api/settings":
                with state.lock:
                    self._send(200, dict(state.settings_served))
                return
            self._send(404, {"error": "not_found"})

    return Handler


def create_case_server(mode: str, host: str = "127.0.0.1", port: int = 0):
    state = CaseState(mode)
    server = _Server((host, port), make_handler(state))
    server.daemon_threads = True
    return server


def run_case_checks(base_url: str) -> list[Verdict]:
    """All eight v2 checks against the case-study server."""
    burst = time_verdict(lambda: run_burst_check(
        url=f"{base_url}/api/bookings", payload={"ref": "case-{round}"},
        n=10, rounds=2,
        verify_url=f"{base_url}/api/bookings/count?ref=case-{{round}}",
        expect_delta=1))
    pagination = time_verdict(lambda: run_pagination_check(url=f"{base_url}/api/products", page_size=8))
    errors = time_verdict(lambda: run_error_shape_check(
        targets=[{"method": "POST", "url": f"{base_url}/api/bookings"},
                 {"method": "POST", "url": f"{base_url}/api/payments"}],
        invalid_payload={"__invalid__": True}))
    auth = time_verdict(lambda: run_auth_matrix_check(
        roles=[{"name": "admin", "headers": {"X-Role": "admin"}},
               {"name": "user", "headers": {"X-Role": "user"}},
               {"name": "anonymous", "headers": {}}],
        targets=[{"method": "GET", "url": f"{base_url}/api/admin-report",
                  "allowed_roles": ["admin"]}]))
    rate = time_verdict(lambda: run_rate_limit_check(url=f"{base_url}/api/search", requests=20))
    idem = time_verdict(lambda: run_idempotency_check(
        url=f"{base_url}/api/payments", payload={"order": "ord-case-1"},
        verify_url=f"{base_url}/api/payments/count?order=ord-case-1"))
    webhook = time_verdict(lambda: run_webhook_check(
        url=f"{base_url}/api/webhooks/receiver", secret=WEBHOOK_SECRET,
        event_id="evt-case-1",
        verify_url=f"{base_url}/api/webhooks/count?event_id=evt-case-1"))
    cache = time_verdict(lambda: run_cache_check(
        read_url=f"{base_url}/api/settings",
        write_payload={"theme": "dark"}))
    return [burst, pagination, errors, auth, rate, idem, webhook, cache]


def run_casestudy(mode: str) -> dict:
    server = create_case_server(mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        report = build_report(run_case_checks(base_url))
        report["fixture_mode"] = mode
        report["case_study"] = True
        return report
    finally:
        server.shutdown()
        server.server_close()
