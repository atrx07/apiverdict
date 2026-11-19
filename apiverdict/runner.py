"""Unified verdict / report format and CI exit codes.

Statuses:
  PASS          — the check ran and the behaviour held.
  FAIL          — the check ran and found a behaviour violation.
  INCONCLUSIVE  — the check could not verify (target unreachable,
                  verify command failed, non-numeric output, ...).
                  This is NEVER mapped to a passing exit code.

Exit codes:
  0 — every check PASSED.
  1 — at least one check FAILED (takes precedence over inconclusive).
  2 — no failures, but at least one check was INCONCLUSIVE.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class Verdict:
    check: str
    status: str  # PASS | FAIL | INCONCLUSIVE
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def exit_code_for(verdicts: list[Verdict]) -> int:
    """Map verdicts to a CI exit code. Never green on a skipped check —
    and never green on NO checks: an empty verdict list is
    INCONCLUSIVE (exit 2), because a report that judged nothing
    must not pass a build."""
    if not verdicts:
        return 2
    statuses = {v.status for v in verdicts}
    if "FAIL" in statuses:
        return 1
    if "INCONCLUSIVE" in statuses:
        return 2
    return 0


def overall_status(verdicts: list[Verdict]) -> str:
    if not verdicts:
        return "INCONCLUSIVE"
    if any(v.status == "FAIL" for v in verdicts):
        return "FAIL"
    if any(v.status == "INCONCLUSIVE" for v in verdicts):
        return "INCONCLUSIVE"
    return "PASS"


def time_verdict(fn: Callable[[], Verdict]) -> Verdict:
    """Run one check and stamp its evidence with duration_ms.

    Timing lives in evidence, not as a Verdict field, because the
    per-check report keys are locked (check/status/summary/evidence)
    and a new field would break consumers that pin them. A check that
    raises is INCONCLUSIVE here too — a crashed check is not a pass,
    and it must not take the whole run down with it.
    """
    start = time.perf_counter()
    try:
        verdict = fn()
    except Exception as exc:  # a check's own crash is data, not a crash
        verdict = Verdict("unknown", "INCONCLUSIVE",
                          f"check raised: {exc}", {})
    if not isinstance(verdict, Verdict):
        # A plugin check returning None (or anything but a Verdict)
        # would otherwise crash on .evidence below — and a crashed
        # runner is worse than an inconclusive check.
        verdict = Verdict("unknown", "INCONCLUSIVE",
                          f"check returned an invalid verdict: "
                          f"{type(verdict).__name__}", {})
    if verdict.status not in ("PASS", "FAIL", "INCONCLUSIVE"):
        # An unknown status string would otherwise fall through
        # exit_code_for to exit 0 — green on garbage. Refuse it.
        verdict = Verdict(verdict.check, "INCONCLUSIVE",
                          f"check returned unknown status "
                          f"{verdict.status!r}", verdict.evidence)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    verdict.evidence = {**verdict.evidence, "duration_ms": elapsed_ms}
    return verdict


def run_parallel(named: list[tuple[str, Callable[[], Verdict]]]) -> list[Verdict]:
    """Run checks concurrently, returned in submission order.

    Output order is deterministic even though completion order is not:
    futures are awaited in the order they were submitted, so a report
    from a parallel run diffs cleanly against a sequential run. Use
    this only for checks against independent endpoints — two checks
    writing to the same resource in parallel would be testing each
    other, not the API.
    """
    if not named:
        return []
    with ThreadPoolExecutor(max_workers=len(named)) as pool:
        futures = [pool.submit(time_verdict, fn) for _name, fn in named]
        verdicts = []
        for (name, _fn), future in zip(named, futures):
            verdict = future.result()
            if verdict.check == "unknown":
                verdict = Verdict(name, verdict.status, verdict.summary,
                                  verdict.evidence)
            verdicts.append(verdict)
        return verdicts


# One-line remediation hint per check, shown only for FAIL lines in
# summarize(). Plain text only — the JSON report schema is unchanged,
# so consumers that pin it are unaffected. A hint names the fix
# direction, not the fixture; an unknown check gets the generic one.
REMEDIATION = {
    "burst": "Enforce a unique constraint or idempotency key on the dedup field, so a second identical write is a no-op or a 409.",
    "pagination": "Give the list a stable sort (e.g. ORDER BY id) and page from it; a walk must return every row exactly once.",
    "pagination-concurrent": "Use cursor pagination anchored on the last row seen; offset pages drift when rows are inserted mid-walk.",
    "pagination-consistency": "Compute the total from the same query as the rows, so every page size agrees on sequence and total.",
    "error-shape": "Emit every error from one central handler so all endpoints share one shape.",
    "error-variants": "Route every invalid payload kind through the same validation path; a malformed body must 4xx, never 2xx/5xx.",
    "error-leak": "Strip tracebacks, file paths and SQL from error bodies; log them server-side instead.",
    "auth-matrix": "Deny by default: every endpoint must check the caller's role, and an unknown role gets 401/403.",
    "rate-limit": "Enforce the limit from a shared counter (not per-process) and return 429 with Retry-After once it is hit.",
    "idempotency": "Store the key with the completed response, atomically (e.g. INSERT ... ON CONFLICT), and replay it on retry.",
    "idempotency-variants": "A reused key with a different payload must return the original result or a 4xx conflict — never create a second resource.",
    "idempotency-scope": "Scope keys per operation (tenant + endpoint + key); a key for one endpoint must not replay on another.",
    "idempotency-failed-retry": "Only record an idempotency key once the operation succeeds; a failed attempt must leave the key free for a corrected retry.",
    "webhook": "Verify the HMAC signature, reject stale timestamps, and dedupe by event id before applying the event.",
    "cache": "Invalidate or update the cached entry on write; a read after a write must see the write.",
    "cache-etag": "Emit an ETag that changes on every write and honour If-None-Match with 304.",
    "spec-conformance": "Fix the implementation or the spec so they agree — a response the spec does not describe is a contract break.",
    "head-consistency": "Make HEAD return the same status and headers as GET (minus the body).",
    "cors": "Return Access-Control-Allow-Origin and Allow-Methods covering the requested method on preflight.",
    "delete-replay": "Make DELETE idempotent: a replay must not delete a second row or error with a 5xx.",
    "patch-preserves": "Apply only the fields present in the PATCH body; leave every other field untouched.",
    "detail-superset": "The detail response must include every field the list row carries, with the same types.",
    "method-guards": "Return 405 with an Allow header listing only the methods the endpoint actually supports.",
    "cache-privacy": "Never serve a cached response built for one caller to another; vary the cache key by caller.",
    "auth-schemes": "Accept the token only under its own scheme; reject it under Basic, Digest, Token, and bare.",
    "redirect": "Keep redirect chains short, acyclic, and never downgrade https to http.",
    "filter": "Apply the filter predicate to every row before paging; a row that fails the filter must never appear.",
    "if-match": "Compare If-Match against the current ETag and reject stale values with 412/409 without changing state.",
}

_GENERIC_HINT = ("See the check's documentation for what it proves and "
                 "what it does not prove.")


def remediation_hint(check_name: str) -> str:
    """The next-step hint for a failed check (generic fallback for
    checks not in the map, e.g. a plugin check)."""
    return REMEDIATION.get(check_name, _GENERIC_HINT)


def summarize(report: dict[str, Any]) -> str:
    """One plain-language failure summary for a report.

    The per-check lines above already carry the detail; this is the
    line a CI reader scans first: what failed, what could not verify,
    and what that means for the exit code. Failed lines carry a
    remediation hint; inconclusive lines do not (their cause is a
    verification problem, not a behaviour fix, so a behaviour hint
    would misdiagnose them).
    """
    checks = report["checks"]
    failed = [c for c in checks if c["status"] == "FAIL"]
    inconclusive = [c for c in checks if c["status"] == "INCONCLUSIVE"]
    passed = len(checks) - len(failed) - len(inconclusive)
    if not failed and not inconclusive:
        return f"All {len(checks)} check(s) passed."
    parts = []
    if failed:
        parts.append(f"{len(failed)} failed: {', '.join(c['check'] for c in failed)}")
    if inconclusive:
        parts.append(f"{len(inconclusive)} could not verify: "
                     f"{', '.join(c['check'] for c in inconclusive)}")
    lines = ["; ".join(parts) + f" ({passed} passed)."]
    for check in failed:
        lines.append(f"  ! {check['check']}: {check['summary']} "
                     f"-> {remediation_hint(check['check'])}")
    for check in inconclusive:
        lines.append(f"  ! {check['check']}: {check['summary']}")
    return "\n".join(lines)


def build_report(verdicts: list[Verdict], tool: str = "apiverdict") -> dict[str, Any]:
    # schema_version: v1 reports had no version field (treat as "1").
    # v2 adds check names and evidence fields, so reports are marked "2".
    # Consumers should read overall/checks/exit_code, which are unchanged.
    return {
        "tool": tool,
        "schema_version": "2",
        "overall": overall_status(verdicts),
        "exit_code": exit_code_for(verdicts),
        "checks": [v.to_dict() for v in verdicts],
    }


def _display_value(value: Any) -> str:
    """Truncate long evidence lists for readable CLI output. The JSON
    report (print_report with --json) is never truncated — this is
    display only, so a 10,000-duplicate list does not flood a CI log
    while the full list is still in the JSON."""
    if isinstance(value, list) and len(value) > 10:
        return f"{value[:10]!r} ... (+{len(value) - 10} more)"
    text = repr(value) if not isinstance(value, str) else value
    if len(text) > 300:
        return text[:300] + " ..."
    return text


def format_report(report: dict[str, Any]) -> str:
    lines = [f"apiverdict — overall: {report['overall']} (exit {report['exit_code']})"]
    for check in report["checks"]:
        lines.append(f"  [{check['status']}] {check['check']}: {check['summary']}")
        for key, value in check["evidence"].items():
            lines.append(f"      {key}: {_display_value(value)}")
    return "\n".join(lines)


def print_report(report: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        # default=str, like the JUnit writer: a plugin check's
        # non-JSON evidence (a set, bytes) must not crash the
        # report print after the check itself ran fine.
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_report(report))
        print(summarize(report))
