import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict import __version__
from apiverdict.runner import Verdict, build_report

ROOT = Path(__file__).resolve().parents[1]


class TestPackaging(unittest.TestCase):
    def test_version_matches_pyproject(self):
        # The version is asserted only against __version__ (derived,
        # not hardcoded) so a bump never needs a test edit — the
        # count-lock test below is what pins the release contents.
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn(f'version = "{__version__}"', text)
        self.assertIn('apiverdict = "apiverdict.__main__:main"', text)

    def test_cli_version_flag(self):
        out = subprocess.run(
            [sys.executable, "-m", "apiverdict", "--version"],
            capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertIn(f"apiverdict {__version__}", out.stdout + out.stderr)

    def test_check_count_is_locked(self):
        # The 0.4.0 "27 vs 28" confusion was a miscount: the CLI
        # list's 28th colon-bearing line was the "Static commands"
        # footer, not a check. The registry is the source of truth;
        # pyproject, README and the CLI list must all agree with it,
        # so a future check (or a dropped one) cannot silently
        # drift the advertised count again.
        from apiverdict.checks import CHECK_DESCRIPTIONS, CHECKS
        self.assertEqual(len(CHECKS), len(CHECK_DESCRIPTIONS))
        self.assertEqual(set(CHECKS), set(CHECK_DESCRIPTIONS))
        self.assertEqual(len(set(CHECKS)), len(CHECKS),
                         "registry must not list a check twice")
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn(f"{len(CHECKS)} invariant checks", pyproject)
        readme = (ROOT / "README.md").read_text()
        self.assertIn(f"{len(CHECKS)} live checks", readme)
        out = subprocess.run(
            [sys.executable, "-m", "apiverdict", "list"],
            capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        listed = [line.split(":", 1)[0] for line in out.stdout.splitlines()
                  if ": " in line and not line.startswith("Static")]
        self.assertEqual(sorted(listed), sorted(CHECKS))

    def test_report_schema_version(self):
        report = build_report([Verdict("burst", "PASS", "ok")])
        self.assertEqual(report["schema_version"], "2")
        self.assertEqual(report["overall"], "PASS")

    def test_no_stale_working_name_docstrings(self):
        for path in [ROOT / "apiverdict" / "__init__.py",
                     ROOT / "apiverdict" / "__main__.py"]:
            self.assertNotIn("working name", path.read_text())


if __name__ == "__main__":
    unittest.main()
