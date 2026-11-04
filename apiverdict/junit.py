"""JUnit XML output for CI systems that parse JUnit, not JSON.

Mapping (documented, because it is a judgement call, not obvious):
  PASS         -> plain <testcase> (no child element)
  FAIL         -> <failure message="summary"> with evidence as text
  INCONCLUSIVE -> <skipped message="summary"> — a check that could
                  not verify is not a failure, and must not be
                  reported as one; the apiverdict exit code (2) is
                  still the authoritative CI signal, and a CI job
                  that only reads JUnit must combine it with the
                  exit code, not treat "no failures" as a pass.

Evidence is truncated to 2000 characters in the XML. Evidence never
contains credential values by design (auth evidence names env vars,
webhook secrets are never in evidence), but truncation also keeps
a 10,000-duplicate list out of a CI log. Use --json for full evidence.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET


def _suite_into(suites: ET.Element, name: str, report: dict) -> None:
    suite = ET.SubElement(suites, "testsuite", {
        "name": name,
        "tests": str(len(report["checks"])),
        "failures": str(sum(1 for c in report["checks"] if c["status"] == "FAIL")),
        "skipped": str(sum(1 for c in report["checks"] if c["status"] == "INCONCLUSIVE")),
        "errors": "0",
    })
    for check in report["checks"]:
        attrs = {
            "classname": "apiverdict",
            "name": check["check"],
        }
        duration = check["evidence"].get("duration_ms")
        if isinstance(duration, (int, float)):
            attrs["time"] = f"{duration / 1000:.3f}"
        case = ET.SubElement(suite, "testcase", attrs)
        if check["status"] == "FAIL":
            failure = ET.SubElement(case, "failure", {"message": check["summary"]})
            failure.text = json.dumps(check["evidence"], indent=2, default=str)[:2000]
        elif check["status"] == "INCONCLUSIVE":
            ET.SubElement(case, "skipped", {"message": check["summary"]})


def report_to_junit(report: dict) -> str:
    suites = ET.Element("testsuites")
    _suite_into(suites, "apiverdict", report)
    return ET.tostring(suites, encoding="unicode", xml_declaration=True)


def reports_to_junit(named_reports: list[tuple[str, dict]]) -> str:
    """Multiple reports (e.g. demo naive + fixed) as multiple
    <testsuite> elements under one <testsuites> root."""
    suites = ET.Element("testsuites")
    for name, report in named_reports:
        _suite_into(suites, name, report)
    return ET.tostring(suites, encoding="unicode", xml_declaration=True)
