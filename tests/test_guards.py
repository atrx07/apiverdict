"""Mutating-check remote guards + webhook --secret-env (milestone 1).

Only burst had a remote guard in v2; idempotency, webhook and cache
create resources too, so they must refuse a non-localhost target
without --allow-remote, and must never need a secret on the CLI.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.cache import run_cache_check
from apiverdict.idempotency import run_idempotency_check
from apiverdict.webhook import run_webhook_check

REMOTE = "https://staging.example.invalid/api/x"
ROOT = Path(__file__).resolve().parents[1]


class TestMutatingGuards(unittest.TestCase):
    def test_idempotency_refuses_remote(self):
        v = run_idempotency_check(url=REMOTE, verify_url=REMOTE + "/count")
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("allow-remote", v.summary)

    def test_idempotency_allows_remote_with_flag_no_network(self):
        # With the flag, the guard passes and the check then fails on
        # transport (example.invalid is unresolvable) — INCONCLUSIVE,
        # not a guard refusal. That distinction is the point.
        v = run_idempotency_check(url=REMOTE, verify_url=REMOTE + "/count",
                                  allow_remote=True)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertNotIn("allow-remote", v.summary)

    def test_webhook_refuses_remote(self):
        v = run_webhook_check(url=REMOTE, secret="s")
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("allow-remote", v.summary)

    def test_cache_refuses_remote_write(self):
        v = run_cache_check(read_url="https://staging.example.invalid/api/s",
                            write_url=REMOTE)
        self.assertEqual(v.status, "INCONCLUSIVE")
        self.assertIn("allow-remote", v.summary)


class TestWebhookSecretEnv(unittest.TestCase):
    def test_missing_secret_env_is_exit_2_not_crash(self):
        env = dict(os.environ)
        env.pop("APIVERDICT_TEST_WEBHOOK_SECRET", None)
        out = subprocess.run(
            [sys.executable, "-m", "apiverdict", "webhook",
             "--url", "http://127.0.0.1:9/x",
             "--secret-env", "APIVERDICT_TEST_WEBHOOK_SECRET"],
            capture_output=True, text=True, cwd=ROOT, env=env, timeout=30)
        self.assertEqual(out.returncode, 2)
        self.assertNotIn("APIVERDICT_TEST_WEBHOOK_SECRET", out.stdout)

    def test_no_secret_at_all_is_exit_2(self):
        out = subprocess.run(
            [sys.executable, "-m", "apiverdict", "webhook",
             "--url", "http://127.0.0.1:9/x"],
            capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
