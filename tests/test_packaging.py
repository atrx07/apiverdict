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
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn(f'version = "{__version__}"', text)
        self.assertIn('apiverdict = "apiverdict.__main__:main"', text)
        self.assertEqual(__version__, "0.4.0")

    def test_cli_version_flag(self):
        out = subprocess.run(
            [sys.executable, "-m", "apiverdict", "--version"],
            capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertIn("apiverdict 0.4.0", out.stdout + out.stderr)

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
