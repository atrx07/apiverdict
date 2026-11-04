"""auth-matrix check — role x endpoint, in one deterministic sweep.

An agent in chat can poke one endpoint as one user. It cannot cheaply
prove, for every role x every endpoint, that the matrix is exactly as
intended — that is what this check is for.

Input:
  roles:   [{"name": "admin", "headers": {...}}, {"name": "user", ...},
            {"name": "anonymous", "headers": {}}]
  targets: [{"method": "GET", "url": "...", "allowed_roles": ["admin"]},
            {"method": "POST", "url": "...", "allowed_roles": ["admin"],
             "payload": {...}},  # optional: POST/PUT/PATCH targets need
            ...]                  # a body, or every role 400s and the
                                  # matrix reads as "unexpected" for
                                  # reasons that have nothing to do
                                  # with authorization.

Classification per request: 2xx = allowed, 401/403 = denied, anything
else = unexpected (recorded, and a FAIL — a 500 on an anonymous probe is
not "denied", it is a leak of a different kind of information).

FAIL findings, in severity order:
  * LEAK: a role NOT in allowed_roles got a 2xx.
  * DENIED-ALLOWED: a role in allowed_roles got 401/403.
  * UNEXPECTED: any other status.
Transport failure on any cell => INCONCLUSIVE (a matrix with a hole
must not read as a pass).
"""

from __future__ import annotations

from .http_util import mutating_guard, request_json
from .runner import Verdict


def _classify(status: int) -> str:
    if 200 <= status < 300:
        return "allowed"
    if status in (401, 403):
        return "denied"
    return f"unexpected:{status}"


def run_auth_matrix_check(
    roles: list[dict],
    targets: list[dict],
    timeout: float = 10.0,
    allow_remote: bool = False,
) -> Verdict:
    if not roles or not targets:
        return Verdict("auth-matrix", "INCONCLUSIVE",
                       "need at least one role and one target", {})
    # Targets arrive as JSON (CLI --targets, suite entries), so they
    # bypass the CLI's method validation: a typo'd verb would
    # otherwise be sent to the server and misread as a finding.
    # Same class: an allowed_roles entry naming no defined role
    # is almost certainly a typo, and without this check it
    # misdiagnoses as a server leak (the allowed role's 2xx
    # reads as "not in allowed_roles"). Library layer refuses
    # bad runtime values as INCONCLUSIVE.
    known_roles = {r.get("name") for r in roles}
    for target in targets:
        unknown = sorted(set(target.get("allowed_roles") or []) - known_roles)
        if unknown:
            return Verdict("auth-matrix", "INCONCLUSIVE",
                           f"target {target.get('url')!r} allows unknown "
                           f"role(s): {', '.join(unknown)}", {})
    for target in targets:
        method = str(target.get("method", "GET")).upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE",
                          "HEAD", "OPTIONS", "QUERY"):
            return Verdict("auth-matrix", "INCONCLUSIVE",
                           f"unsupported HTTP method {method!r} in target "
                           f"{target.get('url')!r}", {})
        # A POST target with a payload writes once per role: the
        # same remote guard as every other writer. GET targets stay
        # ungated (the documented read-only case).
        if method not in ("GET", "HEAD", "OPTIONS", "QUERY"):
            refused = mutating_guard(target.get("url", ""), allow_remote)
            if refused:
                return Verdict("auth-matrix", "INCONCLUSIVE", refused,
                               {"url": target.get("url")})
    cells: list[dict] = []
    leaks, denied_allowed, unexpected = [], [], []
    for target in targets:
        method = str(target.get("method", "GET")).upper()
        url = target["url"]
        allowed = set(target.get("allowed_roles") or [])
        payload = target.get("payload")
        for role in roles:
            name = role["name"]
            headers = role.get("headers") or {}
            try:
                status, _body, _hdrs = request_json(
                    method, url, payload, headers=headers, timeout=timeout)
            except Exception as exc:
                return Verdict("auth-matrix", "INCONCLUSIVE",
                               f"request failed for role {name!r} on "
                               f"{method} {url}: {exc}",
                               {"role": name, "url": url})
            cls = _classify(status)
            cells.append({"role": name, "method": method, "url": url,
                          "status": status, "result": cls})
            if cls == "allowed" and name not in allowed:
                leaks.append(f"{name} reached {method} {url} (HTTP {status})")
            elif cls == "denied" and name in allowed:
                denied_allowed.append(
                    f"{name} denied on {method} {url} (HTTP {status})")
            elif cls.startswith("unexpected"):
                unexpected.append(
                    f"{name} on {method} {url} -> HTTP {status}")

    evidence = {"roles": [r["name"] for r in roles],
                "targets": len(targets), "cells": len(cells),
                "leaks": leaks, "denied_allowed": denied_allowed,
                "unexpected": unexpected, "matrix": cells}
    problems = []
    if leaks:
        problems.append(f"{len(leaks)} privilege leak(s): " + "; ".join(leaks))
    if denied_allowed:
        problems.append(f"{len(denied_allowed)} allowed role(s) denied: "
                        + "; ".join(denied_allowed))
    if unexpected:
        problems.append(f"{len(unexpected)} unexpected status(es): "
                        + "; ".join(unexpected))
    if problems:
        return Verdict("auth-matrix", "FAIL", "; ".join(problems), evidence)
    return Verdict(
        "auth-matrix", "PASS",
        f"matrix clean: {len(cells)} role x endpoint cells, "
        f"{len(targets)} endpoint(s), no leaks, no denied-allowed",
        evidence)
