"""error-shape check — cluster error responses, report inconsistency.

RFC 7807 note: a response whose keys include type, title, status and
detail is a "problem details" response (RFC 7807). The check records
per-endpoint RFC 7807 usage in evidence, and flags MIXED usage (some
endpoints problem-details, others not) as a FAIL finding even if the
raw key sets happen to coincide — half-adopting a standard is exactly
the kind of inconsistency a client has to special-case.

Sends an invalid payload to each supplied endpoint and clusters the
responses by (status, sorted top-level JSON keys, error-field name).
Error-field name is the first of "error", "code", "error_code" present,
else "message", else None. Non-JSON bodies form their own cluster.

A single cluster = consistent error shape (PASS, even if the shape is
ugly — this check judges consistency, not taste). More than one cluster
= FAIL, with each cluster and its endpoints listed. An endpoint that
accepts the invalid payload with a 2xx is a separate FAIL finding:
invalid input must not succeed. So is a 5xx: invalid input must not
crash the server, so a 5xx on a probe FAILs even when every endpoint
crashes with the same shape — consistency must not mask a crash.
"""

from __future__ import annotations

import json
import urllib.parse
from collections import defaultdict

from .http_util import request_json
from .runner import Verdict

_ERROR_FIELDS = ("error", "code", "error_code", "message")


def _is_rfc7807(body) -> bool:
    if not isinstance(body, dict):
        return False
    return {"type", "title", "status", "detail"} <= set(body.keys())


def _header(headers: dict, name: str) -> str | None:
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return value
    return None


def _rfc9457_strict_problems(status: int, body, content_type=None) -> list[str]:
    """RFC 9457 (obsoletes RFC 7807, same shape) strictness, beyond
    mere presence of the four members: the `status` member must equal
    the HTTP status, `type` must be an absolute URI or "about:blank",
    `title` must be a non-empty string, and the media type must be
    application/problem+json (a problem body served as plain
    application/json is not RFC 9457, whatever its fields say).
    These are cheap, stdlib checks; they do not validate the
    problem-type registry or the `errors` extension array's
    contents."""
    problems = []
    if not isinstance(body, dict):
        return ["not a problem-details object"]
    if content_type is not None:
        media = content_type.split(";")[0].strip().lower()
        if media != "application/problem+json":
            problems.append(f"Content-Type {content_type!r} is not "
                            "application/problem+json")
    if body.get("status") != status:
        problems.append(f"problem-details status member {body.get('status')!r} "
                        f"!= HTTP status {status}")
    type_val = body.get("type")
    if not isinstance(type_val, str) or (
            type_val != "about:blank"
            and not urllib.parse.urlparse(type_val).scheme):
        problems.append(f"problem-details type {type_val!r} is not an "
                        "absolute URI or 'about:blank'")
    if not isinstance(body.get("title"), str) or not body.get("title"):
        problems.append("problem-details title is missing or empty")
    return problems


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
    headers: dict[str, str] | None = None,
    require_rfc7807: bool = False,
    require_rfc9457_strict: bool = False,
) -> Verdict:
    """targets: list of {"method": str, "url": str, "payload": object|None}.

    If a target has no payload, invalid_payload is used.
    """
    if not targets:
        return Verdict("error-shape", "INCONCLUSIVE",
                       "no endpoints supplied", {})
    # Suite targets bypass the CLI's endpoint parsing, so a
    # typo'd verb would be sent and its 501 misread as a
    # server crash (5xx FAIL). Validate every target up
    # front, before any probe is sent.
    for target in targets:
        method = str(target.get("method", "POST")).upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE",
                          "HEAD", "OPTIONS", "QUERY"):
            return Verdict("error-shape", "INCONCLUSIVE",
                           f"unsupported HTTP method {method!r} in target "
                           f"{target.get('url')!r}", {})
    clusters: dict[tuple, list[str]] = defaultdict(list)
    accepted: list[str] = []
    crashed: list[str] = []
    details: list[dict] = []
    strict_problems: list[str] = []
    for target in targets:
        method = str(target.get("method", "POST")).upper()
        url = target["url"]
        payload = target.get("payload", invalid_payload)
        merged = dict(headers or {})
        merged.update(target.get("headers") or {})
        try:
            status, body, resp_headers = request_json(method, url, payload, merged, timeout=timeout)
        except Exception as exc:
            return Verdict("error-shape", "INCONCLUSIVE",
                           f"request to {url} failed: {exc}", {"url": url})
        label = f"{method} {url}"
        if 200 <= status < 300:
            accepted.append(label)
        if status >= 500:
            crashed.append(label)
        shape = _shape_of(status, body)
        clusters[shape].append(label)
        details.append({"endpoint": label, "status": status, "shape": shape,
                        "rfc7807": _is_rfc7807(body)})
        if require_rfc9457_strict and _is_rfc7807(body):
            media_type = _header(resp_headers, "Content-Type")
            strict_problems.extend(
                f"{label}: {p}" for p in _rfc9457_strict_problems(
                    status, body,
                    media_type if media_type is not None else ""))

    rfc_count = sum(1 for d in details if d["rfc7807"])
    evidence = {
        "endpoints": len(targets),
        "clusters": [
            {"shape": shape, "endpoints": endpoints}
            for shape, endpoints in clusters.items()
        ],
        "accepted_invalid": accepted,
        "server_errors": crashed,
        "details": details,
        "rfc7807_endpoints": rfc_count,
        "rfc9457_strict_problems": strict_problems,
    }
    problems = []
    if accepted:
        problems.append(f"{len(accepted)} endpoint(s) accepted invalid input (2xx)")
    if crashed:
        problems.append(f"{len(crashed)} endpoint(s) returned 5xx on invalid "
                        f"input (validation must not crash the server): "
                        f"{', '.join(crashed)}")
    if 0 < rfc_count < len(targets):
        problems.append(f"mixed RFC 7807 usage: {rfc_count} of {len(targets)} "
                        "endpoint(s) return problem-details errors, others do not")
    if require_rfc7807 and rfc_count < len(targets):
        problems.append(f"RFC 7807 required but only {rfc_count} of "
                        f"{len(targets)} endpoint(s) return problem-details errors")
    if require_rfc9457_strict and rfc_count < len(targets):
        problems.append(f"RFC 9457 required but only {rfc_count} of "
                        f"{len(targets)} endpoint(s) return problem-details errors")
    if strict_problems:
        problems.append("RFC 9457 strict problems: " + "; ".join(strict_problems))
    if len(clusters) > 1:
        problems.append(f"{len(clusters)} distinct error shapes across "
                        f"{len(targets)} endpoint(s)")
    if problems:
        return Verdict("error-shape", "FAIL", "; ".join(problems), evidence)
    shape = next(iter(clusters))
    return Verdict("error-shape", "PASS",
                   f"all {len(targets)} endpoint(s) share one error shape: {shape}",
                   evidence)
