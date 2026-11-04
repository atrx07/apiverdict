"""Bundled localhost fixture server (naive and fixed modes).

Localhost only. No auth, no real data. The server exists so the demo
and the tests can show a FAIL and a PASS in seconds.

Endpoints:
  POST /api/book
      Body {"slot": "..."} -> booking creation.
        naive: always creates a new booking, even for a booked slot.
        fixed: second booking for the same slot returns 409, no new row.
      Any other body -> validation-error response (used by error-shape):
        naive: /api/book returns 400 {"error","code"};
               /api/order returns 422 {"message","detail"} (different shape).
        fixed: both return 400 {"error":"invalid","code":"BAD_REQUEST"}.
  POST /api/order
      Always treated as a validation-error endpoint in this fixture
      (it is here for error-shape, not for booking).
  GET  /api/count?slot=X   -> {"count": N} bookings for that slot.
  GET  /api/count-all      -> {"count": N} total bookings.
  GET  /api/items?offset=N&limit=M
      30 items, ids 1..30.
        naive: offset 10 returns ids 1..10 again (duplicates), so the
               walk collects 20 unique rows against total 30.
        fixed: correct slices, next_offset None at the end.
      Also honours ?page=N&size=M (fixed only; naive uses the same
      offset fault through page mapping) and ?cursor= (cursor = offset).
  POST /api/reset  -> clears bookings (tests / demo isolation only).

State is guarded by a lock: in fixed mode the lock plus the slot check
is what makes the burst pass; in naive mode there is deliberately no
uniqueness check, so a simultaneous burst creates duplicates.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ITEMS = [{"id": i, "name": f"item-{i}"} for i in range(1, 31)]


class FixtureState:
    def __init__(self, mode: str):
        if mode not in ("naive", "fixed"):
            raise ValueError("mode must be 'naive' or 'fixed'")
        self.mode = mode
        self.lock = threading.Lock()
        self.bookings: dict[str, int] = {}  # slot -> booking id counter

    def reset(self) -> None:
        with self.lock:
            self.bookings.clear()
            self._naive_rows = 0

    def book(self, slot: str) -> tuple[int, dict]:
        with self.lock:
            if self.mode == "fixed" and slot in self.bookings:
                return 409, {"error": "already_booked", "code": "CONFLICT",
                             "slot": slot}
            # Naive: every call mints a fresh row, even for the same slot.
            if self.mode == "naive":
                booking_id = getattr(self, "_naive_rows", 0) + 1
                self._naive_rows = booking_id
                self.bookings[f"{slot}#{booking_id}"] = booking_id
                return 201, {"booking_id": booking_id, "slot": slot}
            booking_id = len(self.bookings) + 1
            self.bookings[slot] = booking_id
            return 201, {"booking_id": booking_id, "slot": slot}

    def count_for_slot(self, slot: str) -> int:
        with self.lock:
            if self.mode == "fixed":
                return 1 if slot in self.bookings else 0
            return sum(1 for key in self.bookings if key.split("#")[0] == slot)

    def count_all(self) -> int:
        with self.lock:
            return len(self.bookings)


def _items_page(state: FixtureState, offset: int, limit: int) -> dict:
    if state.mode == "naive" and offset == 10:
        page = ITEMS[0:10]  # deliberate fault: page 2 repeats page 1
    else:
        page = ITEMS[offset:offset + limit]
    nxt = offset + limit if offset + limit < len(ITEMS) else None
    return {"items": page, "total": len(ITEMS), "next_offset": nxt}


def make_handler(state: FixtureState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep demo output clean
            pass

        def _send(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                data = {}
            return data if isinstance(data, dict) else {}

        def do_POST(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/reset":
                state.reset()
                if hasattr(state, "_naive_rows"):
                    state._naive_rows = 0
                self._send(200, {"ok": True})
                return
            body = self._read_json()
            if parsed.path == "/api/book" and "slot" in body:
                status, payload = state.book(str(body["slot"]))
                self._send(status, payload)
                return
            if parsed.path in ("/api/book", "/api/order"):
                if state.mode == "fixed":
                    self._send(400, {"error": "invalid", "code": "BAD_REQUEST"})
                else:
                    if parsed.path == "/api/book":
                        self._send(400, {"error": "invalid", "code": "BAD_REQUEST"})
                    else:
                        self._send(422, {"message": "validation failed",
                                         "detail": ["bad payload"]})
                return
            self._send(404, {"error": "not_found", "code": "NOT_FOUND"})

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            if parsed.path == "/api/count":
                self._send(200, {"count": state.count_for_slot(query.get("slot", ""))})
                return
            if parsed.path == "/api/count-all":
                self._send(200, {"count": state.count_all()})
                return
            if parsed.path == "/api/items":
                if "cursor" in query:
                    try:
                        offset = int(query["cursor"])
                    except ValueError:
                        offset = 0
                    limit = int(query.get("limit", "10"))
                    page = _items_page(state, offset, limit)
                    nxt = page["next_offset"]
                    page = {"items": page["items"], "total": page["total"],
                            "next_cursor": str(nxt) if nxt is not None else None}
                    self._send(200, page)
                    return
                if "page" in query:
                    try:
                        page_no = int(query["page"])
                    except ValueError:
                        page_no = 1
                    size = int(query.get("size", "10"))
                    offset = (page_no - 1) * size
                    page = _items_page(state, offset, size)
                    total_pages = (len(ITEMS) + size - 1) // size
                    self._send(200, {"items": page["items"], "total": page["total"],
                                     "page": page_no, "total_pages": total_pages})
                    return
                offset = int(query.get("offset", "0"))
                limit = int(query.get("limit", "10"))
                self._send(200, _items_page(state, offset, limit))
                return
            self._send(404, {"error": "not_found", "code": "NOT_FOUND"})

    return Handler


def create_server(mode: str, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Create (not yet serving) a fixture server. port 0 = ephemeral."""
    state = FixtureState(mode)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    server.daemon_threads = True
    return server


def serve_forever(mode: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_server(mode, host, port)
    print(f"apiverdict fixture ({mode}) on http://{host}:{server.server_address[1]} "
          "(localhost only, Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
