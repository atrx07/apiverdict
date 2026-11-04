"""Cache-privacy check — one user's response must not serve another.

A cache keyed by URL alone (missing the caller scope, or a
missing Vary: Authorization) serves user A's response to user
B. This check fetches the same URL as two different callers
and requires the responses to DIFFER — but only because the
user declares the endpoint user-specific (via running this
check at all): an endpoint that legitimately returns the same
public data to everyone will fail it, and that is a
wrong-check-for-that-endpoint user error, not a server bug.

  * Both fetches must succeed (2xx). A denied second caller
    is INCONCLUSIVE here, not a pass: denials are the
    auth-matrix check's finding, and this check judges
    response equality, which needs two responses.
  * Bodies are compared after stripping --ignore-field
    top-level keys (timestamps, request ids) from dict bodies,
    the same volatile-field convention as the replay
    comparisons. Non-dict bodies compare as-is.
  * Identical stripped bodies -> FAIL (possible cache key
    missing caller scope). Differing bodies -> PASS.

Read-only: GET + GET, so no remote guard is needed.
"""

from __future__ import annotations

from .http_util import request_json
from .runner import Verdict


def run_cache_privacy_check(
    url: str,
    headers_a: dict[str, str] | None = None,
    headers_b: dict[str, str] | None = None,
    ignore_fields: list[str] | None = None,
    timeout: float = 10.0,
) -> Verdict:
    try:
        status_a, body_a, _h = request_json(
            "GET", url, headers=headers_a, timeout=timeout)
        status_b, body_b, _h2 = request_json(
            "GET", url, headers=headers_b, timeout=timeout)
    except Exception as exc:
        return Verdict("cache-privacy", "INCONCLUSIVE",
                       f"fetch failed: {exc}", {})
    if not (200 <= status_a < 300) or not (200 <= status_b < 300):
        return Verdict("cache-privacy", "INCONCLUSIVE",
                       f"both callers must succeed to compare "
                       f"(A: HTTP {status_a}, B: HTTP {status_b})", {})
    ignored = set(ignore_fields or [])

    def _stripped(body):
        if isinstance(body, dict):
            return {k: v for k, v in body.items() if k not in ignored}
        return body

    stripped_a, stripped_b = _stripped(body_a), _stripped(body_b)
    evidence = {"url": url, "status_a": status_a,
                "status_b": status_b, "identical": stripped_a == stripped_b}
    if stripped_a == stripped_b:
        return Verdict("cache-privacy", "FAIL",
                       "identical responses for two different callers "
                       "on a user-specific endpoint (possible cache "
                       "key missing caller scope)", evidence)
    return Verdict("cache-privacy", "PASS",
                   "responses differ per caller (no cross-user cache "
                   "leak)", evidence)


__all__ = ["run_cache_privacy_check"]
