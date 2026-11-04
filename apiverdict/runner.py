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
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Verdict:
    check: str
    status: str  # PASS | FAIL | INCONCLUSIVE
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def exit_code_for(verdicts: list[Verdict]) -> int:
    """Map verdicts to a CI exit code. Never green on a skipped check."""
    statuses = {v.status for v in verdicts}
    if "FAIL" in statuses:
        return 1
    if "INCONCLUSIVE" in statuses:
        return 2
    return 0


def overall_status(verdicts: list[Verdict]) -> str:
    if any(v.status == "FAIL" for v in verdicts):
        return "FAIL"
    if any(v.status == "INCONCLUSIVE" for v in verdicts):
        return "INCONCLUSIVE"
    return "PASS"


def build_report(verdicts: list[Verdict], tool: str = "apiverdict") -> dict[str, Any]:
    return {
        "tool": tool,
        "overall": overall_status(verdicts),
        "exit_code": exit_code_for(verdicts),
        "checks": [v.to_dict() for v in verdicts],
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [f"apiverdict — overall: {report['overall']} (exit {report['exit_code']})"]
    for check in report["checks"]:
        lines.append(f"  [{check['status']}] {check['check']}: {check['summary']}")
        for key, value in check["evidence"].items():
            lines.append(f"      {key}: {value}")
    return "\n".join(lines)


def print_report(report: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
