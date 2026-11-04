"""rate-limit behaviour check — NOT a load test.

Sends N identical requests SEQUENTIALLY (deliberately: this is about
the limiting behaviour, not throughput — k6 already does throughput)
and reports where limiting kicks in and whether it stays consistent.

  * No 429 at all, when limiting is expected => FAIL ("no limit observed").
  * First 429 at request K, then a 200 afterwards => FAIL ("inconsistent:
    limit lifted mid-run"), because a limit that flips back on is not
    a limit a client can rely on.
  * First 429 at K, all later requests 429 => PASS, with K and the
    Retry-After presence in evidence.
  * expect_limit=False inverts the first case: any 429 is a FAIL finding
    (an endpoint documented as unlimited that limits is a behaviour bug).

A 5xx anywhere, or a transport failure, => INCONCLUSIVE: a crashing
server is not "rate limited", and must not read as either PASS or FAIL
on this check.
"""

from __future__ import annotations

from collections import Counter
from email.utils import parsedate_to_datetime

from .http_util import mutating_guard, request_json
from .runner import Verdict


def _retry_after_valid(value: str) -> bool:
    """RFC 9110: Retry-After is delay-seconds (an integer) or an
    HTTP-date. Anything else is not a value a client can honour."""
    try:
        int(value)
        return True
    except (ValueError, TypeError):
        pass
    try:
        return parsedate_to_datetime(value) is not None
    except (ValueError, TypeError, OverflowError):
        return False


def run_rate_limit_check(
    url: str,
    method: str = "GET",
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    requests: int = 30,
    expect_limit: bool = True,
    timeout: float = 10.0,
    allow_remote: bool = False,
) -> Verdict:
    if requests < 2:
        return Verdict("rate-limit", "INCONCLUSIVE",
                       "need at least 2 requests to observe limiting", {})
    # Same class as auth-matrix (cycle 211): suite kwargs bypass
    # the CLI's method validation, so a typo'd verb would be
    # sent N times and misread (or 501 into a misleading
    # INCONCLUSIVE). Refuse it up front, naming the method.
    method = str(method).upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE",
                      "HEAD", "OPTIONS", "QUERY"):
        return Verdict("rate-limit", "INCONCLUSIVE",
                       f"unsupported HTTP method {method!r}", {})
    # GET/HEAD reads are not gated; a mutating method fired N times
    # at a remote target is a write burst, so it gets the guard.
    if str(method).upper() not in ("GET", "HEAD", "OPTIONS", "QUERY"):
        refused = mutating_guard(url, allow_remote)
        if refused:
            return Verdict("rate-limit", "INCONCLUSIVE", refused, {"url": url})
    statuses: list[int] = []
    retry_after_seen = False
    retry_after_invalid: list[str] = []
    # Standard limit headers, evidence only: when a run FAILs, the CI
    # log should say what the limit even was, without a second probe.
    limit_headers: dict = {}
    _LIMIT_HEADER_NAMES = ("X-RateLimit-Limit", "X-RateLimit-Remaining",
                           "X-RateLimit-Reset",
                           # IETF draft-ietf-httpapi-ratelimit-headers:
                           # the standard names without the X- prefix,
                           # plus the policy header when present, plus
                           # the draft's current combined form.
                           "RateLimit-Limit", "RateLimit-Remaining",
                           "RateLimit-Reset", "RateLimit-Policy",
                           "RateLimit")
    for _ in range(requests):
        try:
            status, _body, hdrs = request_json(
                method, url, payload, headers, timeout=timeout)
        except Exception as exc:
            return Verdict("rate-limit", "INCONCLUSIVE",
                           f"request failed: {exc}", {"url": url})
        statuses.append(status)
        if not limit_headers:
            lowered = {k.lower(): v for k, v in hdrs.items()}
            for name in _LIMIT_HEADER_NAMES:
                if name.lower() in lowered:
                    limit_headers[name] = lowered[name.lower()]
        if status == 429:
            for k, v in hdrs.items():
                if k.lower() == "retry-after":
                    retry_after_seen = True
                    if not _retry_after_valid(str(v)) and str(v) not in retry_after_invalid:
                        retry_after_invalid.append(str(v))
        if status >= 500:
            return Verdict("rate-limit", "INCONCLUSIVE",
                           f"server error HTTP {status} mid-run: not a "
                           "rate-limit verdict", {"url": url, "statuses": statuses})

    first_limited = next((i + 1 for i, s in enumerate(statuses) if s == 429), None)
    evidence = {
        "url": url, "requests": requests,
        "status_counts": dict(Counter(statuses)),
        "first_limited_at": first_limited,
        "limited_count": statuses.count(429),
        "retry_after_present": retry_after_seen,
        "retry_after_invalid": retry_after_invalid,
        "rate_limit_headers": limit_headers,
    }
    if not expect_limit:
        if first_limited is not None:
            return Verdict("rate-limit", "FAIL",
                           f"unexpected limiting: first 429 at request "
                           f"{first_limited} on an endpoint expected unlimited",
                           evidence)
        return Verdict("rate-limit", "PASS",
                       f"no limiting observed across {requests} requests, as expected",
                       evidence)
    if first_limited is None:
        return Verdict("rate-limit", "FAIL",
                       f"no limiting observed across {requests} requests",
                       evidence)
    after = statuses[first_limited:]  # 0-based slice: requests after the first 429
    if any(s != 429 for s in after):
        return Verdict("rate-limit", "FAIL",
                       f"inconsistent limiting: first 429 at request "
                       f"{first_limited}, but later requests returned "
                       f"non-429 statuses {sorted(set(after))}",
                       evidence)
    if retry_after_invalid:
        return Verdict("rate-limit", "FAIL",
                       f"consistent limiting, but Retry-After value(s) "
                       f"are not parseable as delay-seconds or an "
                       f"HTTP-date: {retry_after_invalid}",
                       evidence)
    return Verdict("rate-limit", "PASS",
                   f"consistent limiting: first 429 at request "
                   f"{first_limited} of {requests}, stayed limited, "
                   f"Retry-After present: {retry_after_seen}",
                   evidence)
