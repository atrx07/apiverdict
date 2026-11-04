"""PATCH-preserves check — PATCH must change only what it is given.

A server that implements PATCH as PUT wipes every field the
client did not send. This check reads an object resource,
PATCHes exactly one field, reads it again, and requires:

  * the patched field equals the new value (a PATCH that
    ignores its input fails too),
  * every other field present before is present after with
    the same value (a wiped or altered field fails, named),
  * keys the server ADDS (timestamps, updated_at, ...) are
    ignored — they are server metadata, not client data the
    PATCH was asked to preserve.

Reads that fail, a non-dict resource, or a non-2xx PATCH are
INCONCLUSIVE: there is nothing to judge, not a failure.
Mutating: guarded by the standard localhost/--allow-remote rule.
"""

from __future__ import annotations

from .http_util import mutating_guard, request_json
from .runner import Verdict


def run_patch_preserves_check(
    url: str,
    field: str,
    value: object = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    allow_remote: bool = False,
) -> Verdict:
    refused = mutating_guard(url, allow_remote)
    if refused:
        return Verdict("patch-preserves", "INCONCLUSIVE", refused,
                       {"url": url})
    try:
        status1, body1, _h = request_json(
            "GET", url, headers=headers, timeout=timeout)
    except Exception as exc:
        return Verdict("patch-preserves", "INCONCLUSIVE",
                       f"initial read failed: {exc}", {})
    if not (200 <= status1 < 300) or not isinstance(body1, dict):
        return Verdict("patch-preserves", "INCONCLUSIVE",
                       f"initial read unusable (HTTP {status1}, "
                       f"{'object' if isinstance(body1, dict) else 'non-object'} "
                       f"body)", {})
    try:
        status_patch, _b, _h2 = request_json(
            "PATCH", url, {field: value}, headers, timeout=timeout)
    except Exception as exc:
        return Verdict("patch-preserves", "INCONCLUSIVE",
                       f"PATCH failed: {exc}", {})
    if not (200 <= status_patch < 300):
        return Verdict("patch-preserves", "INCONCLUSIVE",
                       f"PATCH returned HTTP {status_patch}: nothing to "
                       f"compare", {})
    try:
        status2, body2, _h3 = request_json(
            "GET", url, headers=headers, timeout=timeout)
    except Exception as exc:
        return Verdict("patch-preserves", "INCONCLUSIVE",
                       f"re-read failed: {exc}", {})
    if not (200 <= status2 < 300) or not isinstance(body2, dict):
        return Verdict("patch-preserves", "INCONCLUSIVE",
                       f"re-read unusable (HTTP {status2})", {})
    evidence = {"url": url, "field": field,
                "before": body1.get(field), "after": body2.get(field)}
    problems = []
    if body2.get(field) != value:
        problems.append(f"patched field {field!r} is "
                        f"{body2.get(field)!r}, expected {value!r} "
                        f"(PATCH ignored its input)")
    wiped = [k for k in body1 if k != field and k not in body2]
    changed = [k for k in body1
               if k != field and k in body2 and body2[k] != body1[k]]
    if wiped:
        problems.append(f"PATCH wiped field(s): {', '.join(sorted(wiped))}")
    if changed:
        problems.append(f"PATCH altered field(s) it was not given: "
                        f"{', '.join(sorted(changed))}")
    evidence["wiped"] = wiped
    evidence["changed"] = changed
    if problems:
        return Verdict("patch-preserves", "FAIL", "; ".join(problems),
                       evidence)
    return Verdict("patch-preserves", "PASS",
                   f"PATCH changed only {field!r}; all other fields "
                   f"preserved", evidence)


__all__ = ["run_patch_preserves_check"]
