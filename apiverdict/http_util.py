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

DEFAULT_TIMEOUT = 10.0
# A single response larger than this is not a list page or an error
# body worth parsing: it is a dump, a loop, or a misconfigured endpoint.
# Refusing it (as a transport error, which every check turns into
# INCONCLUSIVE) beats reading an unbounded body into memory.
MAX_RESPONSE_BYTES = 2_000_000


class ResponseTooLarge(ValueError):
    """Response body exceeded MAX_RESPONSE_BYTES."""


def validate_url(url: str, what: str = "url") -> str | None:
    """Return an error string for a malformed http(s) URL, else None.

    The CLI turns this into exit 2; library callers get a plain check
    instead of a confusing urllib traceback from a typo.
    """
    if not isinstance(url, str) or not url.strip():
        return f"{what} is empty"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"{what} {url!r} must use http or https"
    if not parsed.hostname:
        return f"{what} {url!r} has no host"
    return None


def _read_limited(stream, max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ResponseTooLarge(
            f"response body exceeded {max_bytes} bytes")
    return raw.decode("utf-8", "replace")


def is_localhost(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


def mutating_guard(url: str, allow_remote: bool) -> str | None:
    """Refusal reason for a state-changing check against a non-localhost
    target without --allow-remote, else None. Burst already had this
    rule; idempotency, webhook, cache, pagination-concurrent,
    mutating-method rate-limit, and mutating-method auth-matrix
    targets now share it, so no mutating check can silently create
    resources on a remote API by default.
    Read-only checks (pagination, error-shape probes of validation
    endpoints, auth-matrix GETs, spec-conformance GETs) are not gated
    here — they still never write, and their own docs say so."""
    if not is_localhost(url) and not allow_remote:
        return ("refused non-localhost target without --allow-remote "
                "(mutating check: creates resources on purpose)")
    return None


def request_json(
    method: str,
    url: str,
    payload: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_RESPONSE_BYTES,
    raw: bytes | None = None,
) -> tuple[int, Any, dict[str, str]]:
    """Return (status, parsed body or raw text, headers).

    HTTP error statuses are returned, not raised, because a 4xx/5xx is
    data for these checks, not a transport failure. Only transport
    errors (connection refused, timeout, DNS, oversized body) raise.
    A non-positive timeout is a caller bug, raised as ValueError so
    the CLI can turn it into exit 2 instead of hanging or erroring
    obscurely inside urllib.
    """
    if timeout is None or float(timeout) <= 0:
        raise ValueError(f"timeout must be positive, got {timeout!r}")
    data = None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if raw is not None:
        if payload is not None:
            raise ValueError("request_json: raw and payload are exclusive")
        data = raw
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _read_limited(resp, max_bytes)
            return resp.status, _parse(body), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = _read_limited(exc, max_bytes)
        return exc.code, _parse(body), dict(exc.headers or {})


def _parse(body: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return body


def parse_link_next(headers: dict[str, str]) -> str | None:
    """Extract the rel="next" URL from a Link header, if present.

    RFC 8288 allows link-params in any order, so rel="next" is
    accepted in any segment after the URL — not just the first
    param. A server emitting `<url>; title="Next"; rel="next"`
    used to be missed entirely (the walker read end-of-list).
    """
    link = headers.get("Link") or headers.get("link") or ""
    for part in link.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) >= 2 and any(
                seg.replace(" ", "") in ('rel="next"', "rel=next")
                for seg in segments[1:]):
            url = segments[0].strip()
            if url.startswith("<") and url.endswith(">"):
                return url[1:-1]
    return None
