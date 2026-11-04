"""JUnit XML output (milestone 6)."""

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.junit import report_to_junit, reports_to_junit
from apiverdict.runner import Verdict, build_report


class TestJunit(unittest.TestCase):
    def test_mapping_pass_fail_skipped(self):
        report = build_report([
            Verdict("burst", "PASS", "ok"),
            Verdict("pagination", "FAIL", "duplicates < & > in summary"),
            Verdict("cache", "INCONCLUSIVE", "could not verify"),
        ])
        root = ET.fromstring(report_to_junit(report))
        suite = root.find("testsuite")
        self.assertEqual(suite.get("tests"), "3")
        self.assertEqual(suite.get("failures"), "1")
        self.assertEqual(suite.get("skipped"), "1")
        cases = {c.get("name"): c for c in suite.findall("testcase")}
        self.assertIsNotNone(cases["pagination"].find("failure"))
        self.assertIsNotNone(cases["cache"].find("skipped"))
        self.assertIsNone(cases["burst"].find("failure"))
        # XML escaping must survive a summary with < & >.
        self.assertIn("duplicates", cases["pagination"].find("failure").get("message"))

    def test_multi_report_suites(self):
        r1 = build_report([Verdict("burst", "FAIL", "bad")])
        r2 = build_report([Verdict("burst", "PASS", "ok")])
        root = ET.fromstring(reports_to_junit([("demo-naive", r1), ("demo-fixed", r2)]))
        self.assertEqual(len(root.findall("testsuite")), 2)


if __name__ == "__main__":
    unittest.main()
