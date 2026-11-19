"""Remediation hints: every failed check's summary line names the fix
direction. Hints are plain-text only — the JSON report schema is
unchanged. Inconclusive lines carry no hint (their cause is a
verification problem, not a behaviour fix)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.checks import CHECKS
from apiverdict.runner import (
    REMEDIATION, Verdict, build_report, remediation_hint, summarize)


class TestRemediationHints(unittest.TestCase):
    def test_every_registered_check_has_a_hint(self):
        self.assertEqual(set(REMEDIATION), set(CHECKS),
                         "a new check must ship with a remediation hint")
        for name, hint in REMEDIATION.items():
            self.assertTrue(hint.strip(), name)
            self.assertGreater(len(hint), 20, name)

    def test_fail_line_carries_hint_inconclusive_does_not(self):
        mixed = summarize(build_report([
            Verdict("burst", "FAIL", "duplicates"),
            Verdict("pagination", "INCONCLUSIVE", "unreachable"),
            Verdict("cache", "PASS", "ok")]))
        fail_line = [ln for ln in mixed.splitlines()
                     if ln.startswith("  ! burst:")][0]
        self.assertIn("->", fail_line)
        self.assertIn("unique constraint", fail_line)
        inconclusive_line = [ln for ln in mixed.splitlines()
                             if ln.startswith("  ! pagination:")][0]
        self.assertNotIn("->", inconclusive_line)

    def test_unknown_check_gets_generic_hint(self):
        self.assertIn("documentation",
                      remediation_hint("some-plugin-check"))

    def test_pass_only_report_unchanged(self):
        self.assertEqual(
            summarize(build_report([Verdict("a", "PASS", "ok")])),
            "All 1 check(s) passed.")

    def test_hint_text_is_not_fixture_specific(self):
        # Hints must name the fix direction for a real API, not the
        # bundled fixture — no hint may mention the fixture.
        for name, hint in REMEDIATION.items():
            self.assertNotIn("fixture", hint.lower(), name)


if __name__ == "__main__":
    unittest.main()
