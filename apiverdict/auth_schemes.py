"""Auth-scheme check — a token must work under its own scheme only.

A server that extracts the credential without checking the
scheme prefix accepts a Bearer token sent as Basic (or Digest,
or bare) — a confused-deputy bypass: any component that holds
the token in another form can replay it. Rules:

  * Baseline: the token under its correct scheme must succeed
    (2xx). Anything else is INCONCLUSIVE — without a working
    baseline, confusion cannot be judged (wrong token, wrong
    endpoint, or a denied baseline all look the same).
  * Probes: the same token under each wrong scheme (Basic,
    Digest, Token, and bare with no scheme) must NOT succeed.
    A 2xx under any wrong scheme is a FAIL naming the scheme.
  * A transport failure on any probe is INCONCLUSIVE.

Read-only: GET only, so no remote guard is needed. The token
itself never appears in evidence (only the scheme names and
statuses) — same rule as the webhook secret.
"""

from __future__ import annotations

from .http_util import request_json
from .runner import Verdict

_WRONG_SCHEMES = ("Basic", "Digest", "Token", "")


def run_auth_schemes_check(
    url: str,
    token: str,
    scheme: str = "Bearer",
    timeout: float = 10.0,
) -> Verdict:
    if not token:
        return Verdict("auth-schemes", "INCONCLUSIVE",
                       "no token supplied", {})
    try:
        baseline, _b, _h = request_json(
            "GET", url, headers={"Authorization": f"{scheme} {token}"},
            timeout=timeout)
    except Exception as exc:
        return Verdict("auth-schemes", "INCONCLUSIVE",
                       f"baseline request failed: {exc}", {})
    if not (200 <= baseline < 300):
        return Verdict("auth-schemes", "INCONCLUSIVE",
                       f"baseline ({scheme} scheme) returned HTTP "
                       f"{baseline}: cannot judge confusion without a "
                       f"working baseline", {})
    problems = []
    details = []
    for wrong in _WRONG_SCHEMES:
        header = f"{wrong} {token}" if wrong else token
        try:
            status, _b2, _h2 = request_json(
                "GET", url, headers={"Authorization": header},
                timeout=timeout)
        except Exception as exc:
            return Verdict("auth-schemes", "INCONCLUSIVE",
                           f"probe request failed: {exc}", {})
        label = wrong or "(bare token)"
        details.append({"scheme": label, "status": status})
        if 200 <= status < 300:
            problems.append(f"token accepted under wrong scheme "
                            f"{label} (HTTP {status})")
    evidence = {"url": url, "scheme": scheme, "baseline": baseline,
                "probes": details}
    if problems:
        return Verdict("auth-schemes", "FAIL", "; ".join(problems),
                       evidence)
    return Verdict("auth-schemes", "PASS",
                   f"token works only under {scheme}; all "
                   f"{len(_WRONG_SCHEMES)} wrong-scheme probes denied",
                   evidence)


__all__ = ["run_auth_schemes_check"]
