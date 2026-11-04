"""Error-leak check — error responses must not leak internals.

Send invalid input to each endpoint and inspect the error body
(as text) for internal details that must never reach a client:

  * Python tracebacks / "File \"...py\"" frames
  * Java stack frames (".java:<line>")
  * SQL syntax excerpts
  * Absolute server filesystem paths (/home/, /usr/, /var/, /opt/)

Patterns are deliberately conservative: a message like "password
is required" is NOT a leak, and a generic "internal server error"
with no detail is NOT a leak either (it is unhelpful, but not a
disclosure). Only concrete internal artefacts fail.

A 2xx response to invalid input is skipped here (that is
error-shape's job — accepted_invalid); a transport failure makes
the whole check INCONCLUSIVE. A non-JSON error body is still
inspected as text, because leaks often live in HTML error pages.
"""

from __future__ import annotations

import json
import re

from .http_util import request_json
from .runner import Verdict

_PATTERNS = [
    (r"traceback \(most recent call last\)", "python traceback"),
    (r"stack trace", "stack trace text"),
    (r'File "[^"]+\.(py|java|js|ts|rb|php)"', "source file path in frame"),
    (r"\.java:\d+", "java stack frame"),
    (r"SQL syntax|near .*syntax error", "sql syntax excerpt"),
    (r"/(home|usr|var|opt)/[a-zA-Z0-9_.-]+", "absolute server path"),
]


def _leaks_in(text: str) -> list[str]:
    found = []
    for pattern, label in _PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append(label)
    return found


def run_error_leak_check(
    targets: list[dict],
    invalid_payload: object | None = None,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> Verdict:
    if not targets:
        return Verdict("error-leak", "INCONCLUSIVE",
                       "no endpoints supplied", {})
    for target in targets:
        method = str(target.get("method", "POST")).upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE",
                          "HEAD", "OPTIONS", "QUERY"):
            return Verdict("error-leak", "INCONCLUSIVE",
                           f"unsupported HTTP method {method!r} in target "
                           f"{target.get('url')!r}", {})
    leaks: list[str] = []
    checked = 0
    for target in targets:
        method = str(target.get("method", "POST")).upper()
        url = target["url"]
        payload = target.get("payload", invalid_payload)
        merged = dict(headers or {})
        merged.update(target.get("headers") or {})
        try:
            status, body, hdrs = request_json(method, url, payload, merged,
                                               timeout=timeout)
        except Exception as exc:
            return Verdict("error-leak", "INCONCLUSIVE",
                           f"request to {url} failed: {exc}", {"url": url})
        if 200 <= status < 300:
            continue
        checked += 1
        text = body if isinstance(body, str) else json.dumps(body, default=str)
        for label in _leaks_in(text):
            leaks.append(f"{method} {url}: {label}")
        # Headers are part of the error response too: a traceback or
        # server path in a debug header leaks just as surely as one
        # in the body. Same conservative patterns, so a normal
        # header (request ids, content types) never matches.
        for name, value in (hdrs or {}).items():
            for label in _leaks_in(str(value)):
                leaks.append(f"{method} {url}: {label} (header {name})")
    evidence = {"endpoints": len(targets), "checked": checked, "leaks": leaks}
    if not checked:
        return Verdict("error-leak", "INCONCLUSIVE",
                       "no error responses to inspect (all endpoints "
                       "accepted invalid input)", evidence)
    if leaks:
        return Verdict("error-leak", "FAIL", "; ".join(leaks), evidence)
    return Verdict("error-leak", "PASS",
                   f"no internal details leaked in {checked} error response(s)",
                   evidence)
