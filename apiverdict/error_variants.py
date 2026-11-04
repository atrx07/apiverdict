"""Error-variants check — one endpoint, many invalid payloads.

The error-shape check varies the endpoint and holds the payload
constant. This check does the other axis: it holds the endpoint
constant and varies the invalid payload — a missing-field payload,
a wrong-type payload, and an unknown-field payload — and requires
one error shape across all of them.

Why this is a separate bug class: an endpoint that returns
400 {"code", "error"} for a missing field but 422 {"detail"} for
a wrong type forces clients to write one error handler per
failure kind, and the failure kind is exactly what the client
least wants to branch on. A single shape across endpoints but
not across payload kinds is still inconsistent; a single shape
across payload kinds but not endpoints is still inconsistent.
Both axes need a check.

A 2xx on any invalid payload is a FAIL finding, as in
error-shape: invalid input must not succeed. A transport failure
on any payload is INCONCLUSIVE, not a shape judgement.
Additionally, a syntactically malformed JSON body is probed
(always, not opt-in): it must be rejected with a 4xx, never
accepted (2xx) or crashed on (5xx). Its shape is exempt from
the clustering — only the status class is judged — because
framework parser errors legitimately differ from app
validation shapes.
"""

from __future__ import annotations

from collections import defaultdict

from .error_shape import _shape_of
from .http_util import request_json
from .runner import Verdict

DEFAULT_PAYLOADS = (
    {"__invalid__": True},
    {"amount": "not-a-number"},
    {"unknown_field": True},
)


def run_error_variants_check(
    url: str,
    method: str = "POST",
    payloads=None,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> Verdict:
    # Suite/CLI-bypass method validation, same class as
    # cycles 211-213: a typo'd verb must not be probed.
    method = str(method).upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE",
                      "HEAD", "OPTIONS", "QUERY"):
        return Verdict("error-variants", "INCONCLUSIVE",
                       f"unsupported HTTP method {method!r}", {"url": url})
    payloads = list(payloads) if payloads is not None else list(DEFAULT_PAYLOADS)
    if not payloads:
        return Verdict("error-variants", "INCONCLUSIVE",
                       "no payloads supplied", {"url": url})
    if len(payloads) < 2:
        # One payload cannot disagree with itself: a single-kind
        # run would PASS trivially, proving nothing about shape
        # consistency across payload kinds.
        return Verdict("error-variants", "INCONCLUSIVE",
                       "need at least 2 payload kinds to compare shapes",
                       {"url": url})
    clusters: dict[tuple, list[str]] = defaultdict(list)
    accepted: list[str] = []
    crashed: list[str] = []
    details: list[dict] = []
    for index, payload in enumerate(payloads):
        try:
            status, body, _hdrs = request_json(
                method, url, payload, headers, timeout=timeout)
        except Exception as exc:
            return Verdict("error-variants", "INCONCLUSIVE",
                           f"request {index} to {url} failed: {exc}",
                           {"url": url})
        label = f"payload {index}"
        if 200 <= status < 300:
            accepted.append(label)
        if status >= 500:
            crashed.append(label)
        shape = _shape_of(status, body)
        clusters[shape].append(label)
        details.append({"payload": label, "status": status, "shape": shape})
    evidence = {
        "url": url, "method": method,
        "clusters": [{"shape": shape, "payloads": labels}
                     for shape, labels in clusters.items()],
        "accepted_invalid": accepted, "server_errors": crashed,
        "details": details,
    }
    problems = []
    if accepted:
        problems.append(f"invalid input accepted (2xx) for {accepted}")
    if crashed:
        problems.append(f"5xx on invalid input for {crashed} (validation "
                        f"must not crash the server)")
    if len(clusters) > 1:
        problems.append(f"{len(clusters)} distinct error shapes across "
                        f"{len(payloads)} payload kind(s)")
    # Parser probe, shape-exempt: a syntactically malformed JSON body
    # must be rejected (4xx), never accepted (2xx) or crashed on
    # (5xx). Its shape is NOT clustered — framework parser errors
    # legitimately differ from app validation shapes, so only the
    # status class is judged. A transport failure or an odd status
    # here is INCONCLUSIVE when nothing else already failed: a
    # found bug dominates an unjudgeable probe.
    try:
        mstatus, _mbody, _mh = request_json(
            method, url, None, headers, timeout=timeout,
            raw=b"{invalid json")
    except Exception as exc:
        if problems:
            return Verdict("error-variants", "FAIL", "; ".join(problems), evidence)
        evidence["malformed_status"] = None
        return Verdict("error-variants", "INCONCLUSIVE",
                       f"malformed-JSON probe failed: {exc}", evidence)
    evidence["malformed_status"] = mstatus
    if 200 <= mstatus < 300:
        problems.append("malformed JSON accepted (2xx): the parser must "
                        "reject unparseable bodies")
    elif mstatus >= 500:
        problems.append(f"5xx on malformed JSON (HTTP {mstatus}): a parser "
                        f"must not crash the server")
    elif not (400 <= mstatus < 500):
        if problems:
            return Verdict("error-variants", "FAIL", "; ".join(problems), evidence)
        return Verdict("error-variants", "INCONCLUSIVE",
                       f"malformed JSON returned HTTP {mstatus}: cannot "
                       f"tell rejection from another outcome", evidence)
    if problems:
        return Verdict("error-variants", "FAIL", "; ".join(problems), evidence)
    return Verdict("error-variants", "PASS",
                   f"all {len(payloads)} payload kind(s) share one error shape; "
                   f"malformed JSON rejected (HTTP {mstatus})",
                   evidence)
