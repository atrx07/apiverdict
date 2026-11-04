import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.auth_matrix import run_auth_matrix_check
from apiverdict.config import (auth_evidence, get_environment, load_config,
                               redact_headers, resolve_auth, resolve_headers)
from apiverdict.idempotency import run_idempotency_check
from apiverdict.rate_limit import run_rate_limit_check
from fixture_util import FixtureServer

ROLES = [{"name": "admin", "headers": {"X-Role": "admin"}},
         {"name": "user", "headers": {"X-Role": "user"}},
         {"name": "anonymous", "headers": {}}]


def _auth_targets(base):
    return [{"method": "GET", "url": f"{base}/api/admin", "allowed_roles": ["admin"]}]


class TestConfig(unittest.TestCase):
    def setUp(self):
        os.environ["APIVERDICT_TEST_TOKEN"] = "secret-value-123"
        self.tmp = Path(__file__).resolve().parent / "_tmp_test_config.json"
        self.tmp.write_text(json.dumps({
            "default_environment": "local",
            "environments": {
                "local": {"base_url": "http://127.0.0.1:8765",
                          "auth": {"type": "bearer", "env_var": "APIVERDICT_TEST_TOKEN"}},
                "staging": {"base_url": "https://staging.example.invalid",
                            "auth": {"type": "api-key", "env_var": "APIVERDICT_TEST_KEY",
                                      "header": "X-Api-Key"}},
            }}))

    def tearDown(self):
        os.environ.pop("APIVERDICT_TEST_TOKEN", None)
        if self.tmp.exists():
            self.tmp.unlink()

    def test_bearer_from_named_env_var(self):
        config = load_config(self.tmp)
        env = get_environment(config, "local")
        headers = resolve_headers(env)
        self.assertEqual(headers, {"Authorization": "Bearer secret-value-123"})

    def test_missing_env_var_is_error_not_empty_header(self):
        with self.assertRaises(ValueError):
            resolve_auth({"type": "bearer", "env_var": "APIVERDICT_DEFINITELY_UNSET_XYZ"})

    def test_unknown_environment_is_error(self):
        with self.assertRaises(ValueError):
            get_environment(load_config(self.tmp), "nope")

    def test_secret_never_in_evidence_or_redacted_headers(self):
        config = load_config(self.tmp)
        env = get_environment(config, "local")
        evidence = json.dumps(auth_evidence(env))
        redacted = json.dumps(redact_headers(resolve_headers(env)))
        self.assertNotIn("secret-value-123", evidence)
        self.assertNotIn("secret-value-123", redacted)
        self.assertIn("APIVERDICT_TEST_TOKEN", evidence)

    def test_toml_config_loads_on_311_plus(self):
        if sys.version_info < (3, 11):
            self.skipTest("tomllib needs 3.11+")
        toml = Path(__file__).resolve().parent / "_tmp_test_config.toml"
        toml.write_text(
            'default_environment = "local"\n'
            '[environments.local]\nbase_url = "http://127.0.0.1:8765"\n'
            '[environments.local.auth]\ntype = "bearer"\nenv_var = "APIVERDICT_TEST_TOKEN"\n')
        try:
            env = get_environment(load_config(toml))
            self.assertEqual(resolve_headers(env)["Authorization"],
                             "Bearer secret-value-123")
        finally:
            toml.unlink()


class TestAuthMatrix(unittest.TestCase):
    def test_naive_leaks(self):
        with FixtureServer("naive") as base:
            verdict = run_auth_matrix_check(ROLES, _auth_targets(base))
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(len(verdict.evidence["leaks"]), 2)

    def test_fixed_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_auth_matrix_check(ROLES, _auth_targets(base))
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(verdict.evidence["cells"], 3)


class TestRateLimit(unittest.TestCase):
    def test_naive_no_limit_fails(self):
        with FixtureServer("naive") as base:
            verdict = run_rate_limit_check(url=f"{base}/api/rate-limited", requests=20)
        self.assertEqual(verdict.status, "FAIL")
        self.assertIsNone(verdict.evidence["first_limited_at"])

    def test_fixed_consistent_limit_passes(self):
        with FixtureServer("fixed") as base:
            verdict = run_rate_limit_check(url=f"{base}/api/rate-limited", requests=20)
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(verdict.evidence["first_limited_at"], 6)
        self.assertTrue(verdict.evidence["retry_after_present"])


class TestIdempotency(unittest.TestCase):
    def test_naive_ignores_key(self):
        with FixtureServer("naive") as base:
            verdict = run_idempotency_check(
                url=f"{base}/api/book", payload={"slot": "idem-1"},
                verify_url=f"{base}/api/count?slot=idem-1")
        self.assertEqual(verdict.status, "FAIL")
        self.assertEqual(verdict.evidence["delta"], 2)

    def test_fixed_honours_key(self):
        with FixtureServer("fixed") as base:
            verdict = run_idempotency_check(
                url=f"{base}/api/book", payload={"slot": "idem-1"},
                verify_url=f"{base}/api/count?slot=idem-1")
        self.assertEqual(verdict.status, "PASS")
        self.assertEqual(verdict.evidence["delta"], 1)

    def test_no_verify_is_inconclusive(self):
        with FixtureServer("fixed") as base:
            verdict = run_idempotency_check(
                url=f"{base}/api/book", payload={"slot": "idem-1"})
        self.assertEqual(verdict.status, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
