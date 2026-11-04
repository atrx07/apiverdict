"""Second case-study fixture — a LOCAL public-library API.

This is NOT a real API and must never be presented as one. It is the
shop case study's counterpart in a different domain (catalogue, loans,
holds, fines, librarian reports), so a reader can see the same eight
checks catch the same bug classes in a system that has nothing to do
with payments or bookings.

Seeded bugs in "naive" mode (all fixed in "fixed" mode):
  1. burst:       simultaneous loans of the same book all succeed.
  2. pagination:  page 2 of /api/books repeats page 1, AND the second
                  full walk returns rows in reverse order.
  3. error-shape: /api/loans returns RFC 7807 problem-details,
                  /api/holds returns a plain 422 shape (mixed).
  4. auth-matrix: /api/librarian-report leaks to any role.
  5. rate-limit:  /api/catalogue-search never limits.
  6. idempotency: /api/loans ignores Idempotency-Key.
  7. cache:       /api/availability GET after a loan serves the old
                  availability (stale cache).
  8. webhook:     /api/webhooks/overdue accepts tampered bodies,
                  stale replays, and double-applies duplicates.

Run it with:  python -m apiverdict library [--mode naive|fixed|both]
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

BOOKS = [{"id": i, "title": f"book-{i}", "author": f"author-{i % 5}"}
         for i in range(1, 31)]
WEBHOOK_SECRET = "library-test-secret"


class LibraryState:
    def __init__(self, mode: str):
        if mode not in ("naive", "fixed"):
            raise ValueError("mode must be 'naive' or 'fixed'")
        self.mode = mode
        self.lock = threading.RLock()
        self.loans: dict[str, int] = {}  # book -> loan count
        self.loan_seq = 0
        self.idem: dict[str, int] = {}  # Idempotency-Key -> loan id
        self.rate_count = 0
        self.book_walks = 0
        self.availability_actual = {"book-1": "available"}
        self.availability_served = {"book-1": "available"}
        self.webhook_applied: dict[str, int] = {}

    def loan(self, book: str, key: str | None) -> tuple[int, dict]:
        with self.lock:
            if self.mode == "fixed" and key and key in self.idem:
                return 200, {"loan_id": self.idem[key], "book": book,
                             "idempotent_replay": True}
            if self.mode == "fixed" and book in self.loans:
                return 409, {"error": "already_loaned", "code": "CONFLICT"}
            self.loan_seq += 1
            self.loans[book] = self.loans.get(book, 0) + 1
            if key:
                self.idem[key] = self.loan_seq
            return 201, {"loan_id": self.loan_seq, "book": book}

    def count_loans(self, book: str) -> int:
        with self.lock:
            return self.loans.get(book, 0)


def _verify_webhook_sig(body: dict, headers) -> bool:
    sig = headers.get("X-Signature-256", "")
    ts = headers.get("X-Timestamp", "")
    eid = headers.get("X-Event-Id", "")
    msg = f"{ts}.{eid}.{json.dumps(body, sort_keys=True)}".encode()
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def make_handler(state: LibraryState):
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
            if parsed.path == "/api/loans" and "book" in body:
                status, payload = state.loan(
                    str(body["book"]), self.headers.get("Idempotency-Key"))
                self._send(status, payload)
                return
            if parsed.path == "/api/loans":
                self._send(400, {"type": "https://example.invalid/bad-request",
                                 "title": "Bad Request", "status": 400,
                                 "detail": "invalid payload"})
                return
            if parsed.path == "/api/holds":
                if state.mode == "fixed":
                    self._send(400, {"type": "https://example.invalid/bad-request",
                                     "title": "Bad Request", "status": 400,
                                     "detail": "invalid payload"})
                else:
                    self._send(422, {"message": "validation failed",
                                     "detail": ["bad payload"]})
                return
            if parsed.path == "/api/availability":
                # Cache check's write: loaning book-1 changes availability.
                with state.lock:
                    state.availability_actual = {"book-1": "on-loan"}
                    if state.mode == "fixed":
                        state.availability_served = dict(
                            state.availability_actual)
                self._send(200, {"ok": True})
                return
            if parsed.path == "/api/webhooks/overdue":
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
                with state.lock:
                    state.webhook_applied[eid] = \
                        state.webhook_applied.get(eid, 0) + 1
                self._send(200, {"ok": True})
                return
            self._send(404, {"error": "not_found"})

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            query = dict(urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True))
            if parsed.path == "/api/loans/count":
                self._send(200, {"count": state.count_loans(
                    query.get("book", ""))})
                return
            if parsed.path == "/api/webhooks/count":
                self._send(200, {"count": state.webhook_applied.get(
                    query.get("event_id", ""), 0)})
                return
            if parsed.path == "/api/books":
                offset = int(query.get("offset", "0"))
                limit = int(query.get("limit", "10"))
                if offset == 0:
                    with state.lock:
                        state.book_walks += 1
                        walk_no = state.book_walks
                else:
                    walk_no = state.book_walks
                items = BOOKS[offset:offset + limit]
                if state.mode == "naive" and offset == 10:
                    items = BOOKS[0:10]
                if state.mode == "naive" and walk_no >= 2:
                    rev = list(reversed(BOOKS))
                    items = rev[offset:offset + limit]
                    if offset == 10:
                        items = rev[0:10]
                nxt = offset + limit if offset + limit < len(BOOKS) else None
                self._send(200, {"items": items, "total": len(BOOKS),
                                 "next_offset": nxt})
                return
            if parsed.path == "/api/librarian-report":
                role = self.headers.get("X-Role", "")
                if state.mode == "fixed" and role != "librarian":
                    self._send(403, {"error": "forbidden"})
                else:
                    self._send(200, {"ok": True})
                return
            if parsed.path == "/api/catalogue-search":
                with state.lock:
                    state.rate_count += 1
                    count = state.rate_count
                if state.mode == "fixed" and count > 5:
                    self._send(429, {"error": "rate_limited"},
                               {"Retry-After": "60"})
                else:
                    self._send(200, {"ok": True})
                return
            if parsed.path == "/api/availability":
                with state.lock:
                    self._send(200, dict(state.availability_served))
                return
            self._send(404, {"error": "not_found"})

    return Handler


def create_library_server(mode: str, host: str = "127.0.0.1", port: int = 0):
    state = LibraryState(mode)
    server = _Server((host, port), make_handler(state))
    server.daemon_threads = True
    return server


def run_library_checks(base_url: str) -> list[Verdict]:
    """All eight checks against the library server."""
    burst = time_verdict(lambda: run_burst_check(
        url=f"{base_url}/api/loans", payload={"book": "book-burst-{round}"},
        n=10, rounds=2,
        verify_url=f"{base_url}/api/loans/count?book=book-burst-{{round}}",
        expect_delta=1))
    pagination = time_verdict(lambda: run_pagination_check(url=f"{base_url}/api/books", page_size=10))
    errors = time_verdict(lambda: run_error_shape_check(
        targets=[{"method": "POST", "url": f"{base_url}/api/loans"},
                 {"method": "POST", "url": f"{base_url}/api/holds"}],
        invalid_payload={"__invalid__": True}))
    auth = time_verdict(lambda: run_auth_matrix_check(
        roles=[{"name": "librarian", "headers": {"X-Role": "librarian"}},
               {"name": "member", "headers": {"X-Role": "member"}},
               {"name": "anonymous", "headers": {}}],
        targets=[{"method": "GET", "url": f"{base_url}/api/librarian-report",
                  "allowed_roles": ["librarian"]}]))
    rate = time_verdict(lambda: run_rate_limit_check(
        url=f"{base_url}/api/catalogue-search", requests=20))
    idem = time_verdict(lambda: run_idempotency_check(
        url=f"{base_url}/api/loans", payload={"book": "book-idem-1"},
        verify_url=f"{base_url}/api/loans/count?book=book-idem-1"))
    webhook = time_verdict(lambda: run_webhook_check(
        url=f"{base_url}/api/webhooks/overdue", secret=WEBHOOK_SECRET,
        event_id="evt-library-1",
        verify_url=f"{base_url}/api/webhooks/count?event_id=evt-library-1"))
    cache = time_verdict(lambda: run_cache_check(
        read_url=f"{base_url}/api/availability",
        write_payload={"book": "book-1"}))
    return [burst, pagination, errors, auth, rate, idem, webhook, cache]


def run_library(mode: str) -> dict:
    server = create_library_server(mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        report = build_report(run_library_checks(base_url))
        report["fixture_mode"] = mode
        report["case_study"] = "library"
        return report
    finally:
        server.shutdown()
        server.server_close()
