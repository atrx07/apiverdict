"""If-Match check — optimistic concurrency must actually be enforced.

A server that sends an ETag but ignores If-Match on writes is lying
about versioning: two clients that read the same version can both
write, and the second silently overwrites the first — the lost
update. cache-etag judges If-None-Match (reads); this check judges
If-Match (writes), the question cache-etag does not ask.

Steps:
  1. GET the resource; if there is no ETag, INCONCLUSIVE (the
     endpoint does not claim ETag support — not a fail).
  2. Write with If-Match: <etag>; expect 2xx. A 412 here is a FAIL
     (the server rejects its own current validator); any other
     non-2xx is INCONCLUSIVE (a validation/auth failure cannot
     judge preconditions).
  3. GET again; the ETag must have changed. An unchanged ETag
     means the write was a no-op, which cannot judge staleness:
     INCONCLUSIVE.
  4. Write again with If-Match: <old etag> (now stale); expect
     412, or 409 as a conflict refusal. A 2xx here is a FAIL
     (the stale precondition was applied). Any other status is
     INCONCLUSIVE (cannot tell enforcement from another failure).
  5. GET a final time; the ETag must still equal the step-3
     value. A moved ETag means the rejected stale write changed
     state anyway: FAIL.

Weak ETags (W/"...") pass through verbatim: RFC 9110 compares
them weakly, and the check judges enforcement, not validator
strength. Mutating: writes go to write_url (default read_url)
and need --allow-remote off localhost, like every mutating check.
"""

from __future__ import annotations

from .http_util import mutating_guard, request_json
from .runner import Verdict

_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")
# 412 is the RFC 9110 precondition-failed status; 409 is accepted
# as a conflict refusal (the server refused, which is the point).
_ENFORCED_STATUSES = (412, 409)


def run_if_match_check(
    read_url: str,
    write_method: str = "PATCH",
    write_url: str | None = None,
    write_payload: object | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    allow_remote: bool = False,
) -> Verdict:
    write_url = write_url or read_url
    # A read is not a write: a GET/HEAD "write" that changes nothing
    # would misreport as a precondition finding. Refuse it outright.
    write_method = str(write_method).upper()
    if write_method not in _MUTATING_METHODS:
        return Verdict("if-match", "INCONCLUSIVE",
                       f"write method must mutate (POST/PUT/PATCH/DELETE), "
                       f"got {write_method!r}",
                       {"read_url": read_url, "write_url": write_url})
    refused = mutating_guard(write_url, allow_remote)
    if refused:
        return Verdict("if-match", "INCONCLUSIVE", refused,
                       {"read_url": read_url, "write_url": write_url})
    try:
        status1, _body1, hdrs1 = request_json("GET", read_url, headers=headers,
                                              timeout=timeout)
    except Exception as exc:
        return Verdict("if-match", "INCONCLUSIVE",
                       f"initial read failed: {exc}", {"read_url": read_url})
    etag1 = hdrs1.get("ETag") or hdrs1.get("etag")
    evidence = {"read_url": read_url, "write_url": write_url,
                "read_status": status1, "etag_before": etag1}
    if status1 != 200:
        return Verdict("if-match", "INCONCLUSIVE",
                       f"initial read returned HTTP {status1}", evidence)
    if not etag1:
        return Verdict("if-match", "INCONCLUSIVE",
                       "no ETag on initial read: endpoint does not claim "
                       "ETag support", evidence)
    current_headers = dict(headers or {})
    current_headers["If-Match"] = etag1
    try:
        wstatus, _wb, _wh = request_json(write_method, write_url, write_payload,
                                          current_headers, timeout=timeout)
        status2, _body2, hdrs2 = request_json("GET", read_url, headers=headers,
                                               timeout=timeout)
    except Exception as exc:
        return Verdict("if-match", "INCONCLUSIVE",
                       f"request failed mid-check: {exc}", evidence)
    etag2 = hdrs2.get("ETag") or hdrs2.get("etag")
    evidence.update({"current_write_status": wstatus,
                     "read_status_after": status2, "etag_after": etag2})
    if wstatus == 412:
        return Verdict("if-match", "FAIL",
                       "current If-Match rejected (HTTP 412): the server "
                       "rejects its own current validator", evidence)
    if not (200 <= wstatus < 300):
        return Verdict("if-match", "INCONCLUSIVE",
                       f"current-precondition write failed (HTTP {wstatus}); "
                       "cannot judge preconditions", evidence)
    if status2 != 200:
        return Verdict("if-match", "INCONCLUSIVE",
                       f"read after write returned HTTP {status2}", evidence)
    if etag2 == etag1:
        return Verdict("if-match", "INCONCLUSIVE",
                       "ETag unchanged after a successful write: the write "
                       "was a no-op, which cannot judge staleness", evidence)
    stale_headers = dict(headers or {})
    stale_headers["If-Match"] = etag1
    try:
        sstatus, _sb, _sh = request_json(write_method, write_url, write_payload,
                                          stale_headers, timeout=timeout)
        status3, _body3, hdrs3 = request_json("GET", read_url, headers=headers,
                                               timeout=timeout)
    except Exception as exc:
        return Verdict("if-match", "INCONCLUSIVE",
                       f"request failed mid-check: {exc}", evidence)
    etag3 = hdrs3.get("ETag") or hdrs3.get("etag")
    evidence.update({"stale_write_status": sstatus,
                     "read_status_final": status3, "etag_final": etag3})
    if 200 <= sstatus < 300:
        return Verdict("if-match", "FAIL",
                       f"stale If-Match accepted (HTTP {sstatus}): no "
                       "lost-update protection", evidence)
    if sstatus not in _ENFORCED_STATUSES:
        return Verdict("if-match", "INCONCLUSIVE",
                       f"stale write returned HTTP {sstatus}: cannot tell "
                       "enforcement from another failure", evidence)
    if status3 != 200:
        return Verdict("if-match", "INCONCLUSIVE",
                       f"final read returned HTTP {status3}", evidence)
    if etag3 != etag2:
        return Verdict("if-match", "FAIL",
                       "stale write rejected by status but state changed "
                       "anyway (ETag moved after the rejected write)", evidence)
    return Verdict("if-match", "PASS",
                   "preconditions enforced: current If-Match applied, "
                   "stale If-Match rejected without moving state",
                   evidence)
