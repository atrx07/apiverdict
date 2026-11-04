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
  POST /api/variant  (error-variants target: naive answers each invalid
                       payload kind with a different error shape;
                       fixed answers all three with one shape)
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
  GET  /api/items-link     -> same 30 items, next page announced ONLY
      via Link: <url>; rel="next" (naive repeats page 1 as page 2).
  GET  /api/items-link-relative -> same, but the Link header
      carries a RELATIVE url (</api/items-link-relative?...>).
  GET  /api/admin          -> auth-matrix target; fixed denies any
      role other than X-Role: admin with 403, naive returns 200 to all.
  GET  /api/rate-limited   -> rate-limit target; fixed returns 429
      with Retry-After after 5 requests, naive never limits.
  POST /api/book with Idempotency-Key -> idempotency target; fixed
      replays return the original booking with no new row, naive
      creates a new row per send.
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


class _Server(ThreadingHTTPServer):
    """Fixture server with a listen backlog that survives its own
    burst check: the stdlib default request_queue_size is 5, so a
    10-thread burst plus parallel checks overflowed the backlog
    and one connection reset — a fixture artefact, not a burst
    finding. 128 is ample for a localhost demo server."""
    request_queue_size = 128
    daemon_threads = True

ITEMS = [{"id": i, "name": f"item-{i}"} for i in range(1, 31)]


class FixtureState:
    def __init__(self, mode: str):
        if mode not in ("naive", "fixed"):
            raise ValueError("mode must be 'naive' or 'fixed'")
        self.mode = mode
        self.lock = threading.RLock()
        self.bookings: dict[str, int] = {}  # slot -> booking id counter
        self.idem_keys: dict[str, int] = {}  # Idempotency-Key -> booking id
        self.rate_count = 0  # requests to /api/rate-limited
        # Feed for the pagination-concurrent check: new rows prepend
        # (newest first), so an offset reader mid-walk sees drift while
        # a cursor reader anchored on the last row seen does not.
        self.feed: list[dict] = [{"id": i, "name": f"feed-{i}"} for i in range(1, 21)]
        self.feed_next = 21

    def reset(self) -> None:
        with self.lock:
            self.bookings.clear()
            self.idem_keys.clear()
            self.rate_count = 0
            self._naive_rows = 0
            self.feed = [{"id": i, "name": f"feed-{i}"} for i in range(1, 21)]
            self.feed_next = 21

    def book_idempotent(self, slot: str, key: str) -> tuple[int, dict]:
        """Fixed mode honours the key; naive mode ignores it (the bug)."""
        with self.lock:
            if self.mode == "fixed" and key in self.idem_keys:
                return 200, {"booking_id": self.idem_keys[key], "slot": slot,
                             "idempotent_replay": True}
            status, payload = self.book(slot)
            if self.mode == "fixed" and status == 201:
                self.idem_keys[key] = payload["booking_id"]
            return status, payload

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

        def _send(self, status: int, payload: object,
                  extra_headers: dict[str, str] | None = None) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, val in (extra_headers or {}).items():
                self.send_header(key, val)
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
            if parsed.path == "/api/feed":
                with state.lock:
                    item = {"id": state.feed_next,
                            "name": body.get("name", f"feed-{state.feed_next}")}
                    state.feed_next += 1
                    state.feed.insert(0, item)  # newest first
                self._send(201, item)
                return
            if parsed.path == "/api/book" and "slot" in body:
                idem_key = self.headers.get("Idempotency-Key")
                if idem_key:
                    status, payload = state.book_idempotent(str(body["slot"]), idem_key)
                else:
                    status, payload = state.book(str(body["slot"]))
                self._send(status, payload)
                return
            if parsed.path == "/api/variant":
                if state.mode == "fixed":
                    self._send(400, {"error": "invalid", "code": "BAD_REQUEST"})
                elif "__invalid__" in body:
                    self._send(400, {"error": "invalid", "code": "BAD_REQUEST"})
                elif "amount" in body and not isinstance(body["amount"], (int, float)):
                    self._send(422, {"message": "validation failed",
                                     "detail": ["bad type"]})
                elif "unknown_field" in body:
                    self._send(400, {"detail": "unknown field"})
                else:
                    self._send(400, {"error": "invalid", "code": "BAD_REQUEST"})
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
            if parsed.path == "/api/items-link-relative":
                # Same as /api/items-link, but the Link header
                # carries a RELATIVE URL — the form real APIs
                # commonly emit, and the form urljoin must
                # resolve against the page just fetched.
                offset = int(query.get("offset", "0"))
                limit = int(query.get("limit", "10"))
                if state.mode == "naive" and offset == 10:
                    page = ITEMS[0:10]
                else:
                    page = ITEMS[offset:offset + limit]
                nxt = offset + limit if offset + limit < len(ITEMS) else None
                extra = {}
                if nxt is not None:
                    extra["Link"] = (f'</api/items-link-relative'
                                     f'?offset={nxt}&limit={limit}>; rel="next"')
                self._send(200, {"items": page, "total": len(ITEMS)}, extra)
                return
            if parsed.path == "/api/items-link":
                # Link-header pagination: body carries items + total only;
                # the next page is announced ONLY via Link: rel="next".
                offset = int(query.get("offset", "0"))
                limit = int(query.get("limit", "10"))
                if state.mode == "naive" and offset == 10:
                    page = ITEMS[0:10]  # same deliberate fault as offset style
                else:
                    page = ITEMS[offset:offset + limit]
                nxt = offset + limit if offset + limit < len(ITEMS) else None
                extra = {}
                if nxt is not None:
                    host = self.headers.get("Host", "127.0.0.1")
                    extra["Link"] = (f'<http://{host}/api/items-link'
                                     f'?offset={nxt}&limit={limit}>; rel="next"')
                self._send(200, {"items": page, "total": len(ITEMS)}, extra)
                return
            if parsed.path == "/api/admin":
                # Auth-matrix target: fixed denies non-admins, naive leaks.
                role = self.headers.get("X-Role", "")
                if state.mode == "fixed" and role != "admin":
                    self._send(403, {"error": "forbidden", "code": "FORBIDDEN"})
                else:
                    self._send(200, {"ok": True, "role": role or "anonymous"})
                return
            if parsed.path == "/api/rate-limited":
                # Rate-limit target: fixed limits after 5, naive never limits.
                with state.lock:
                    state.rate_count += 1
                    count = state.rate_count
                if state.mode == "fixed" and count > 5:
                    self._send(429, {"error": "rate_limited", "code": "RATE_LIMITED"},
                               {"Retry-After": "60"})
                else:
                    self._send(200, {"ok": True, "n": count})
                return
            if parsed.path == "/api/feed":
                limit = int(query.get("limit", "10"))
                with state.lock:
                    feed = list(state.feed)
                if "cursor" in query:
                    cursor = query["cursor"]
                    if cursor in ("0", ""):
                        start = 0
                    else:
                        try:
                            pos = next(i for i, r in enumerate(feed)
                                       if str(r["id"]) == cursor)
                            start = pos + 1
                        except StopIteration:
                            start = len(feed)
                    page = feed[start:start + limit]
                    nxt = str(page[-1]["id"]) if page and start + limit < len(feed) else None
                    self._send(200, {"items": page, "total": len(feed),
                                     "next_cursor": nxt})
                    return
                offset = int(query.get("offset", "0"))
                page = feed[offset:offset + limit]
                nxt = offset + limit if offset + limit < len(feed) else None
                self._send(200, {"items": page, "total": len(feed),
                                 "next_offset": nxt})
                return
            if parsed.path.startswith("/api/item/"):
                # Single-item endpoint for spec-conformance path-param
                # tests: /api/item/{id} -> nested object response.
                try:
                    item_id = int(parsed.path.rsplit("/", 1)[-1])
                except ValueError:
                    self._send(404, {"error": "not_found", "code": "NOT_FOUND"})
                    return
                match = next((i for i in ITEMS if i["id"] == item_id), None)
                if match is None:
                    self._send(404, {"error": "not_found", "code": "NOT_FOUND"})
                    return
                self._send(200, {"id": match["id"], "name": match["name"],
                                 "detail": {"code": "OK", "count": 1}})
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
                if state.mode == "fixed":
                    # Filter support for the filter check: any
                    # non-paging query param filters rows by
                    # exact match (json-server style). Naive
                    # ignores filters entirely — the fault the
                    # filter check catches.
                    filters = {k: v for k, v in query.items()
                               if k not in ("offset", "limit", "page",
                                            "size", "cursor")}
                    if filters:
                        pool = [row for row in ITEMS
                                if all(str(row.get(k)) == v
                                       for k, v in filters.items())]
                        page = pool[offset:offset + limit]
                        nxt = (offset + limit
                               if offset + limit < len(pool) else None)
                        self._send(200, {"items": page, "total": len(pool),
                                         "next_offset": nxt})
                        return
                self._send(200, _items_page(state, offset, limit))
                return
            self._send(404, {"error": "not_found", "code": "NOT_FOUND"})

    return Handler


def create_server(mode: str, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Create (not yet serving) a fixture server. port 0 = ephemeral."""
    state = FixtureState(mode)
    server = _Server((host, port), make_handler(state))
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
