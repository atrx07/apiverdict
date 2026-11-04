"""Backlog 5 runner UX: parallel execution (deterministic order),
per-check timing in evidence, and failure summaries. Exit-code
contract is unchanged — these tests assert that too."""

import sys
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.demo import run_all_checks_parallel
from apiverdict.junit import report_to_junit
from apiverdict.runner import (
    Verdict, build_report, run_parallel, summarize, time_verdict)


class TestParallel(unittest.TestCase):
    def test_output_order_is_submission_order_not_completion(self):
        def slow():
            time.sleep(0.2)
            return Verdict("slow", "PASS", "slow done")

        def fast():
            return Verdict("fast", "PASS", "fast done")

        verdicts = run_parallel([("slow", slow), ("fast", fast)])
        self.assertEqual([v.check for v in verdicts], ["slow", "fast"])
        self.assertTrue(all(v.status == "PASS" for v in verdicts))

    def test_raising_check_becomes_inconclusive_not_crash(self):
        def boom():
            raise RuntimeError("kaboom")

        verdicts = run_parallel([("boom", boom),
                                 ("ok", lambda: Verdict("ok", "PASS", "fine"))])
        self.assertEqual(verdicts[0].check, "boom")
        self.assertEqual(verdicts[0].status, "INCONCLUSIVE")
        self.assertEqual(verdicts[1].status, "PASS")
        report = build_report(verdicts)
        self.assertEqual(report["exit_code"], 2)  # never green on skipped

    def test_parallel_trio_matches_sequential_trio(self):
        # Against a real fixture server is covered by test_runner_demo;
        # here the contract is just that parallel returns the trio in
        # order with timing stamped, using a dead target (all
        # INCONCLUSIVE, but present and ordered).
        verdicts = run_all_checks_parallel("http://127.0.0.1:9", timeout=2)
        self.assertEqual([v.check for v in verdicts],
                         ["burst", "pagination", "error-shape"])
        self.assertTrue(all("duration_ms" in v.evidence for v in verdicts))


class TestTimingAndSummary(unittest.TestCase):
    def test_time_verdict_stamps_duration_and_keeps_status(self):
        verdict = time_verdict(lambda: Verdict("x", "FAIL", "bad", {"a": 1}))
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(verdict.evidence["a"], 1)
        self.assertIsInstance(verdict.evidence["duration_ms"], float)
        self.assertGreaterEqual(verdict.evidence["duration_ms"], 0)

    def test_summarize_pass_fail_inconclusive(self):
        self.assertEqual(
            summarize(build_report([Verdict("a", "PASS", "ok")])),
            "All 1 check(s) passed.")
        mixed = summarize(build_report([
            Verdict("burst", "FAIL", "duplicates"),
            Verdict("pagination", "INCONCLUSIVE", "unreachable"),
            Verdict("cache", "PASS", "ok")]))
        self.assertIn("1 failed: burst", mixed)
        self.assertIn("1 could not verify: pagination", mixed)
        self.assertIn("! burst: duplicates", mixed)

    def test_junit_carries_time_when_duration_present(self):
        report = build_report([time_verdict(
            lambda: Verdict("burst", "PASS", "ok"))])
        root = ET.fromstring(report_to_junit(report))
        case = root.find("testsuite").find("testcase")
        self.assertIsNotNone(case.get("time"))


if __name__ == "__main__":
    unittest.main()


class TestRunnerInvalidVerdict(unittest.TestCase):
    def test_none_return_is_inconclusive_not_crash(self):
        from apiverdict.runner import time_verdict, run_parallel
        v = time_verdict(lambda: None)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("invalid verdict", v.summary)
        self.assertIn("duration_ms", v.evidence)
        vs = run_parallel([("weird", lambda: None)])
        self.assertEqual(vs[0].check, "weird")
        self.assertEqual(vs[0].status, "INCONCLUSIVE")


class TestRunnerUnknownStatus(unittest.TestCase):
    def test_unknown_status_is_inconclusive_not_pass(self):
        from apiverdict.runner import time_verdict, build_report, Verdict
        v = time_verdict(lambda: Verdict("weird", "MAYBE", "?", {}))
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("MAYBE", v.summary)
        report = build_report([v])
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["overall"], "INCONCLUSIVE")


class TestReportJsonRobustness(unittest.TestCase):
    def test_non_json_evidence_prints(self):
        from apiverdict.runner import build_report, print_report, Verdict
        import io
        import contextlib
        report = build_report([Verdict("weird", "PASS", "ok",
                                       {"tags": {"a", "b"}})])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_report(report, as_json=True)
        self.assertIn("weird", buf.getvalue())
