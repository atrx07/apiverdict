"""config-check: validates config + auth wiring, never prints
secret values, fails fast (exit 2) on unwired auth."""
import json
import os
import subprocess
import sys
import tempfile
import unittest


def _run(config, env_name, extra_env=None):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(config, fh)
        path = fh.name
    env = dict(os.environ)
    env.pop("APIVERDICT_TOKEN", None)
    env.pop("APIVERDICT_API_KEY", None)
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(
            [sys.executable, "-m", "apiverdict", "config-check",
             "--file", path, "--env", env_name],
            capture_output=True, text=True, cwd=".", env=env)
    finally:
        os.unlink(path)


class TestConfigCheck(unittest.TestCase):
    def test_success_prints_names_not_values(self):
        config = {"environments": {"local": {
            "base_url": "http://127.0.0.1:8765",
            "auth": {"type": "bearer", "env_var": "APIVERDICT_TOKEN"}}}}
        proc = _run(config, "local", {"APIVERDICT_TOKEN": "sekrit-123"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("config OK", proc.stdout)
        self.assertIn("headers: Authorization", proc.stdout)
        self.assertNotIn("sekrit-123", proc.stdout)

    def test_missing_env_var_exits_2(self):
        config = {"environments": {"local": {
            "base_url": "http://127.0.0.1:8765",
            "auth": {"type": "bearer", "env_var": "APIVERDICT_TOKEN"}}}}
        proc = _run(config, "local")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("APIVERDICT_TOKEN", proc.stderr)

    def test_unknown_environment_exits_2(self):
        config = {"environments": {"local": {"base_url": "http://127.0.0.1:8765"}}}
        proc = _run(config, "nope")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown environment", proc.stderr)


if __name__ == "__main__":
    unittest.main()
