"""Cache ETag check — conditional requests must actually work.

A server that sends an ETag but ignores If-None-Match is lying
about caching: clients re-download every time, and worse, a stale
ETag after a write tells clients nothing changed when it did.

Steps:
  1. GET the resource; if there is no ETag, INCONCLUSIVE (the
     endpoint does not claim ETag support — not a fail).
  2. GET with If-None-Match: <etag>; expect 304.
  3. Write (POST to write_url, default = read_url).
  4. GET again; expect a DIFFERENT ETag (the write changed the
     resource, so the validator must change).
  5. GET with the OLD ETag; expect 200, not 304 (the old validator
     must no longer match).

A failed write or read is INCONCLUSIVE, same rule as the
stale-after-write cache check: a guess would be a false verdict.
"""

from __future__ import annotations

from .http_util import mutating_guard, request_json
from .runner import Verdict


def run_cache_etag_check(
    read_url: str,
    write_method: str = "POST",
    write_url: str | None = None,
    write_payload: object | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    allow_remote: bool = False,
) -> Verdict:
    write_url = write_url or read_url
    # A read is not a write: a GET/HEAD "write" that changes nothing
    # would misreport as a stale-cache finding. Refuse it outright.
    write_method = str(write_method).upper()
    if write_method not in ("POST", "PUT", "PATCH", "DELETE"):
        return Verdict('cache-etag', "INCONCLUSIVE",
                       f"write method must mutate (POST/PUT/PATCH/DELETE), "
                       f"got {write_method!r}",
                       {"read_url": read_url, "write_url": write_url})
    refused = mutating_guard(write_url, allow_remote)
    if refused:
        return Verdict("cache-etag", "INCONCLUSIVE", refused,
                       {"read_url": read_url, "write_url": write_url})
    try:
        status1, _body1, hdrs1 = request_json("GET", read_url, headers=headers,
                                              timeout=timeout)
    except Exception as exc:
        return Verdict("cache-etag", "INCONCLUSIVE",
                       f"initial read failed: {exc}", {"read_url": read_url})
    etag1 = hdrs1.get("ETag") or hdrs1.get("etag")
    evidence = {"read_url": read_url, "write_url": write_url,
                "read_status": status1, "etag_before": etag1}
    if status1 != 200:
        return Verdict("cache-etag", "INCONCLUSIVE",
                       f"initial read returned HTTP {status1}", evidence)
    if not etag1:
        return Verdict("cache-etag", "INCONCLUSIVE",
                       "no ETag on initial read: endpoint does not claim "
                       "ETag support", evidence)
    cond_headers = dict(headers or {})
    cond_headers["If-None-Match"] = etag1
    try:
        status_cond, _b, _h = request_json("GET", read_url, headers=cond_headers,
                                            timeout=timeout)
        wstatus, _wb, _wh = request_json(write_method, write_url, write_payload,
                                          headers, timeout=timeout)
        status2, _body2, hdrs2 = request_json("GET", read_url, headers=headers,
                                               timeout=timeout)
        status_old, _bo, _ho = request_json("GET", read_url,
                                             headers=cond_headers, timeout=timeout)
    except Exception as exc:
        return Verdict("cache-etag", "INCONCLUSIVE",
                       f"request failed mid-check: {exc}", evidence)
    etag2 = hdrs2.get("ETag") or hdrs2.get("etag")
    evidence.update({"conditional_status": status_cond, "write_status": wstatus,
                     "read_status_after": status2, "etag_after": etag2,
                     "old_etag_status_after_write": status_old})
    if not (200 <= wstatus < 300):
        return Verdict("cache-etag", "INCONCLUSIVE",
                       f"write failed (HTTP {wstatus}); cannot judge ETags",
                       evidence)
    problems = []
    if status_cond != 304:
        problems.append(f"If-None-Match ignored: expected 304, got {status_cond}")
    if etag2 == etag1:
        problems.append("ETag unchanged after a successful write")
    if status_old == 304:
        problems.append("old ETag still returns 304 after a write (stale validator)")
    if problems:
        return Verdict("cache-etag", "FAIL", "; ".join(problems), evidence)
    return Verdict("cache-etag", "PASS",
                   "ETags behave: 304 on match, changed after write, "
                   "old ETag no longer matches",
                   evidence)
