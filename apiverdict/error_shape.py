"""error-shape check — cluster error responses, report inconsistency.

Sends an invalid payload to each supplied endpoint and clusters the
responses by (status, sorted top-level JSON keys, error-field name).
Error-field name is the first of "error", "code", "error_code" present,
else "message", else None. Non-JSON bodies form their own cluster.

A single cluster = consistent error shape (PASS, even if the shape is
ugly — this check judges consistency, not taste). More than one cluster
= FAIL, with each cluster and its endpoints listed. An endpoint that
accepts the invalid payload with a 2xx is a separate FAIL finding:
invalid input must not succeed.
"""

from __future__ import annotations

import json
from collections import defaultdict

from .http_util import request_json
from .runner import Verdict

_ERROR_FIELDS = ("error", "code", "error_code", "message")


def _shape_of(status: int, body) -> tuple:
    if not isinstance(body, dict):
        return (status, ("<non-json>",), None)
    keys = tuple(sorted(body.keys()))
    field = next((f for f in _ERROR_FIELDS if f in body), None)
    return (status, keys, field)


def run_error_shape_check(
    targets: list[dict],
    invalid_payload: object | None = None,
    timeout: float = 10.0,
) -> Verdict:
    """targets: list of {"method": str, "url": str, "payload": object|None}.

    If a target has no payload, invalid_payload is used.
    """
    if not targets:
        return Verdict("error-shape", "INCONCLUSIVE",
                       "no endpoints supplied", {})
    clusters: dict[tuple, list[str]] = defaultdict(list)
    accepted: list[str] = []
    details: list[dict] = []
    for target in targets:
        method = target.get("method", "POST")
        url = target["url"]
        payload = target.get("payload", invalid_payload)
        try:
            status, body, _headers = request_json(method, url, payload, timeout=timeout)
        except Exception as exc:
            return Verdict("error-shape", "INCONCLUSIVE",
                           f"request to {url} failed: {exc}", {"url": url})
        label = f"{method} {url}"
        if 200 <= status < 300:
            accepted.append(label)
        shape = _shape_of(status, body)
        clusters[shape].append(label)
        details.append({"endpoint": label, "status": status, "shape": shape})

    evidence = {
        "endpoints": len(targets),
        "clusters": [
            {"shape": shape, "endpoints": endpoints}
            for shape, endpoints in clusters.items()
        ],
        "accepted_invalid": accepted,
        "details": details,
    }
    problems = []
    if accepted:
        problems.append(f"{len(accepted)} endpoint(s) accepted invalid input (2xx)")
    if len(clusters) > 1:
        problems.append(f"{len(clusters)} distinct error shapes across "
                        f"{len(targets)} endpoint(s)")
    if problems:
        return Verdict("error-shape", "FAIL", "; ".join(problems), evidence)
    shape = next(iter(clusters))
    return Verdict("error-shape", "PASS",
                   f"all {len(targets)} endpoint(s) share one error shape: {shape}",
                   evidence)
