"""DELETE-replay check — DELETE is idempotent by definition.

The idempotency check assumes creations (delta >= 0), so it
cannot express DELETE: a correct second DELETE returns 404
("already gone"), not the stored response, and the state moves
down, not up. This check covers that shape:

  * DELETE the resource once; the counted state must drop by
    exactly 1 (before -> after_first). A 2xx DELETE that moves
    nothing is a FAIL under the tool's verify-source-trust
    convention (same as burst/idempotency delta mismatches):
    the values are named so a misconfigured verify source is
    diagnosable from the report.
  * DELETE the same URL again. Acceptable: 2xx (idempotent
    success) or 404/410 (already gone). Anything else (500,
    403, ...) is a FAIL — a replay must not error.
  * The counted state after the replay must equal the state
    after the first DELETE. A replay that changes state again
    (deleting something else, restoring, ...) is a FAIL.
  * A first DELETE that is not 2xx is INCONCLUSIVE: nothing
    was deleted, so there is no replay to judge (e.g. the
    resource was already gone).

Mutating: guarded by the standard localhost/--allow-remote rule.
"""

from __future__ import annotations

from .burst import _read_state
from .http_util import mutating_guard, request_json
from .runner import Verdict

_GONE = (404, 410)


def run_delete_replay_check(
    url: str,
    headers: dict[str, str] | None = None,
    verify_cmd: str | None = None,
    verify_url: str | None = None,
    timeout: float = 10.0,
    allow_remote: bool = False,
    verify_headers: dict[str, str] | None = None,
) -> Verdict:
    refused = mutating_guard(url, allow_remote)
    if refused:
        return Verdict("delete-replay", "INCONCLUSIVE", refused,
                       {"url": url})
    if verify_cmd is None and verify_url is None:
        return Verdict("delete-replay", "INCONCLUSIVE",
                       "no verify command or verify URL: final state "
                       "cannot be checked", {})
    vheaders = verify_headers if verify_headers is not None else headers
    try:
        before = _read_state(verify_cmd, verify_url, 1,
                             verify_headers=vheaders)
    except Exception as exc:
        return Verdict("delete-replay", "INCONCLUSIVE",
                       f"could not read state before delete: {exc}", {})
    try:
        status_first, _b, _h = request_json(
            "DELETE", url, headers=headers, timeout=timeout)
    except Exception as exc:
        return Verdict("delete-replay", "INCONCLUSIVE",
                       f"first DELETE failed: {exc}", {})
    if not (200 <= status_first < 300):
        return Verdict("delete-replay", "INCONCLUSIVE",
                       f"first DELETE returned HTTP {status_first}: no "
                       f"resource was deleted to replay", {})
    try:
        after_first = _read_state(verify_cmd, verify_url, 1,
                                  verify_headers=vheaders)
        status_replay, _b2, _h2 = request_json(
            "DELETE", url, headers=headers, timeout=timeout)
        after_replay = _read_state(verify_cmd, verify_url, 1,
                                   verify_headers=vheaders)
    except Exception as exc:
        return Verdict("delete-replay", "INCONCLUSIVE",
                       f"could not complete replay: {exc}", {})
    evidence = {"url": url, "before": before,
                "after_first": after_first,
                "after_replay": after_replay,
                "status_first": status_first,
                "status_replay": status_replay}
    problems = []
    if after_first != before - 1:
        problems.append(f"first DELETE did not remove exactly one "
                        f"resource (state {before} -> {after_first})")
    if not (200 <= status_replay < 300) and status_replay not in _GONE:
        problems.append(f"replay DELETE errored (HTTP {status_replay}) "
                        f"instead of succeeding or reporting gone "
                        f"(404/410)")
    if after_replay != after_first:
        problems.append(f"replay DELETE changed state "
                        f"({after_first} -> {after_replay})")
    if problems:
        return Verdict("delete-replay", "FAIL", "; ".join(problems),
                       evidence)
    return Verdict("delete-replay", "PASS",
                   f"DELETE replay is idempotent: state {before} -> "
                   f"{after_first}, replay HTTP {status_replay}, state "
                   f"unchanged", evidence)


__all__ = ["run_delete_replay_check"]
