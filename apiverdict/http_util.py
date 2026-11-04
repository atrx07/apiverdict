"""Small stdlib HTTP helpers shared by the checks.

No dependencies, no retries (a retry would mask exactly the behaviour
these checks are trying to observe), short timeouts.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def is_localhost(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


def request_json(
    method: str,
    url: str,
    payload: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any, dict[str, str]]:
    """Return (status, parsed body or raw text, headers).

    HTTP error statuses are returned, not raised, because a 4xx/5xx is
    data for these checks, not a transport failure. Only transport
    errors (connection refused, timeout, DNS) raise.
    """
    data = None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, _parse(body), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, _parse(body), dict(exc.headers or {})


def _parse(body: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return body


def parse_link_next(headers: dict[str, str]) -> str | None:
    """Extract the rel="next" URL from a Link header, if present."""
    link = headers.get("Link") or headers.get("link") or ""
    for part in link.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) >= 2 and 'rel="next"' in segments[1]:
            url = segments[0].strip()
            if url.startswith("<") and url.endswith(">"):
                return url[1:-1]
    return None
