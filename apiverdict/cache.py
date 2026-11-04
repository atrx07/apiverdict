"""cache-invalidation check — stale-after-write.

Read, write, read again. If the second read is identical to the first
after a successful write, the read path is serving stale data.

  * Write succeeds (2xx) but second read == first read => FAIL (stale).
  * Second read differs => PASS.
  * Write fails, or either read fails => INCONCLUSIVE (cannot tell
    whether the cache or the write is at fault, and a guess would be
    a false verdict either way).

Comparison is on the full JSON body by default; callers can pass
`compare_field` to compare only one field (e.g. "version") when the
body carries timestamps or other legitimately changing noise. ETag /
Last-Modified headers are reported in evidence when present, but the
verdict rests on the body, not on header presence — a fresh ETag with
a stale body is still stale.
"""

from __future__ import annotations

from .http_util import mutating_guard, request_json
from .runner import Verdict


def _value(body, field):
    if field and isinstance(body, dict):
        if "." in field:
            # Dotted path ("meta.version"), same convention as
            # pagination --id-field. A missing intermediate yields
            # None, matching the missing top-level-field behaviour.
            current = body
            for part in field.split("."):
                if not isinstance(current, dict) or part not in current:
                    return None
                current = current[part]
            return current
        return body.get(field)
    return body


def run_cache_check(
    read_url: str,
    write_method: str = "POST",
    write_url: str | None = None,
    write_payload: object | None = None,
    headers: dict[str, str] | None = None,
    compare_field: str | None = None,
    timeout: float = 10.0,
    allow_remote: bool = False,
) -> Verdict:
    write_url = write_url or read_url
    # A read is not a write: a GET/HEAD "write" that changes nothing
    # would misreport as a stale-cache finding. Refuse it outright.
    write_method = str(write_method).upper()
    if write_method not in ("POST", "PUT", "PATCH", "DELETE"):
        return Verdict('cache', "INCONCLUSIVE",
                       f"write method must mutate (POST/PUT/PATCH/DELETE), "
                       f"got {write_method!r}",
                       {"read_url": read_url, "write_url": write_url})
    refused = mutating_guard(write_url, allow_remote)
    if refused:
        return Verdict("cache", "INCONCLUSIVE", refused,
                       {"read_url": read_url, "write_url": write_url})
    try:
        status1, body1, hdrs1 = request_json("GET", read_url, headers=headers, timeout=timeout)
        wstatus, _wbody, _wh = request_json(
            write_method, write_url, write_payload, headers, timeout=timeout)
        status2, body2, hdrs2 = request_json("GET", read_url, headers=headers, timeout=timeout)
    except Exception as exc:
        return Verdict("cache", "INCONCLUSIVE", f"request failed: {exc}",
                       {"read_url": read_url})
    evidence = {
        "read_url": read_url, "write_url": write_url,
        "read_status_before": status1, "write_status": wstatus,
        "read_status_after": status2,
        "etag_before": hdrs1.get("ETag"), "etag_after": hdrs2.get("ETag"),
        "before": _value(body1, compare_field),
        "after": _value(body2, compare_field),
    }
    if status1 != 200 or status2 != 200:
        return Verdict("cache", "INCONCLUSIVE",
                       f"read failed (before HTTP {status1}, after HTTP {status2})",
                       evidence)
    if not (200 <= wstatus < 300):
        return Verdict("cache", "INCONCLUSIVE",
                       f"write failed (HTTP {wstatus}); cannot judge staleness",
                       evidence)
    if _value(body1, compare_field) == _value(body2, compare_field):
        return Verdict("cache", "FAIL",
                       "stale-after-write: second read identical to first "
                       "after a successful write",
                       evidence)
    return Verdict("cache", "PASS",
                   "read-after-write reflects the write (no stale cache)",
                   evidence)
