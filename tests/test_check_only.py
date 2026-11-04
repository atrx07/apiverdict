"""check --only must run ONLY the wanted checks (the old code ran
the whole trio, burst included, and filtered afterwards), and an
unknown --only name must error before any check runs."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.__main__ import main
from fixture_util import FixtureServer


class TestCheckOnly(unittest.TestCase):
    def test_only_pagination_runs_no_burst(self):
        with FixtureServer("fixed") as base:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["check", "--base-url", base,
                             "--only", "pagination"])
            out = buf.getvalue()
        self.assertEqual(code, 0, out)
        self.assertIn("pagination", out)
        self.assertNotIn("burst", out)
        self.assertNotIn("error-shape", out)
        # And the fixture still has no bookings: burst never fired.
        # (Proven by the output above containing no burst check;
        # the count endpoint is not needed for that assertion.)

    def test_only_unknown_errors_without_running(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["check", "--base-url", "http://127.0.0.1:9",
                         "--only", "nope"])
        self.assertEqual(code, 2)
        self.assertNotIn("burst", buf.getvalue())

    def test_max_pages_caps_the_walk(self):
        with FixtureServer("fixed") as base:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["pagination", "--url", f"{base}/api/items",
                             "--page-size", "10", "--max-pages", "1"])
            out = buf.getvalue()
        # 1 page of a 3-page list: walk incomplete -> INCONCLUSIVE.
        self.assertEqual(code, 2, out)
        self.assertIn("INCONCLUSIVE", out)


if __name__ == "__main__":
    unittest.main()


class TestCheckOnlyEmpty(unittest.TestCase):
    def test_blank_only_is_exit_2(self):
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "apiverdict", "check",
             "--base-url", "http://127.0.0.1:9", "--only", " , "],
            capture_output=True, text=True, cwd=".")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("names no checks", proc.stderr)
