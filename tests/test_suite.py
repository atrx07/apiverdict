"""Suite runner: config validation fails fast, and a full suite
against a fresh fixed fixture passes in config order, sequentially
and in parallel."""

import json
import subprocess
import sys
import tempfile
import os
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.suite import load_suite, run_suite
from fixture_util import FixtureServer

ROOT = Path(__file__).resolve().parents[1]


def _config_for(base):
    return {"checks": [
        {"check": "burst", "url": f"{base}/api/book",
         "payload": {"slot": "suite-{round}"}, "n": 10, "rounds": 2,
         "verify_url": f"{base}/api/count?slot=suite-{{round}}",
         "expect_delta": 1},
        {"check": "pagination", "url": f"{base}/api/items", "page_size": 10},
        {"check": "pagination-consistency", "url": f"{base}/api/items",
         "page_size_a": 10, "page_size_b": 5},
        {"check": "error-shape", "targets": [
            {"method": "POST", "url": f"{base}/api/book"},
            {"method": "POST", "url": f"{base}/api/order"}],
         "invalid_payload": {"__invalid__": True}},
        {"check": "error-variants", "url": f"{base}/api/variant"},
        {"check": "auth-matrix",
         "roles": [{"name": "admin", "headers": {"X-Role": "admin"}},
                   {"name": "user", "headers": {"X-Role": "user"}},
                   {"name": "anonymous", "headers": {}}],
         "targets": [{"method": "GET", "url": f"{base}/api/admin",
                      "allowed_roles": ["admin"]}]},
        {"check": "rate-limit", "url": f"{base}/api/rate-limited",
         "requests": 20},
        {"check": "idempotency", "url": f"{base}/api/book",
         "payload": {"slot": "suite-idem"},
         "verify_url": f"{base}/api/count?slot=suite-idem"},
    ]}


def _write_config(config):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(config, fh)
    fh.close()
    return fh.name


class TestSuiteValidation(unittest.TestCase):
    def test_unknown_check_is_error(self):
        path = _write_config({"checks": [{"check": "nope"}]})
        with self.assertRaises(ValueError) as ctx:
            load_suite(path)
        self.assertIn("unknown check", str(ctx.exception))

    def test_unknown_kwarg_is_error(self):
        path = _write_config({"checks": [
            {"check": "pagination", "url": "http://127.0.0.1:9/x",
             "pageSize": 10}]})
        with self.assertRaises(ValueError) as ctx:
            load_suite(path)
        self.assertIn("pageSize", str(ctx.exception))

    def test_missing_check_name_is_error(self):
        path = _write_config({"checks": [{"url": "http://127.0.0.1:9/x"}]})
        with self.assertRaises(ValueError):
            load_suite(path)

    def test_webhook_missing_secret_env_is_error(self):
        import os
        os.environ.pop("APIVERDICT_SUITE_TEST_SECRET", None)
        path = _write_config({"checks": [
            {"check": "webhook", "url": "http://127.0.0.1:9/hook",
             "secret_env": "APIVERDICT_SUITE_TEST_SECRET"}]})
        with self.assertRaises(ValueError) as ctx:
            load_suite(path)
        self.assertIn("APIVERDICT_SUITE_TEST_SECRET", str(ctx.exception))


class TestSuiteRun(unittest.TestCase):
    def test_fixed_suite_passes_in_config_order(self):
        with FixtureServer("fixed") as base:
            verdicts = run_suite(_write_config(_config_for(base)))
        self.assertEqual([v.check for v in verdicts], [
            "burst", "pagination", "pagination-consistency",
            "error-shape", "error-variants", "auth-matrix",
            "rate-limit", "idempotency"])
        self.assertTrue(all(v.status == "PASS" for v in verdicts), verdicts)

    def test_fixed_suite_passes_in_parallel(self):
        with FixtureServer("fixed") as base:
            verdicts = run_suite(_write_config(_config_for(base)),
                                 parallel=True)
        self.assertEqual([v.check for v in verdicts], [
            "burst", "pagination", "pagination-consistency",
            "error-shape", "error-variants", "auth-matrix",
            "rate-limit", "idempotency"])
        self.assertTrue(all(v.status == "PASS" for v in verdicts), verdicts)

    def test_cli_bad_config_is_exit_2(self):
        out = subprocess.run(
            [sys.executable, "-m", "apiverdict", "suite",
             "--config", _write_config({"checks": [{"check": "nope"}]})],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(out.returncode, 2)
        self.assertIn("unknown check", out.stderr)


if __name__ == "__main__":
    unittest.main()


class TestSuiteDryRun(unittest.TestCase):
    def test_dry_run_lists_without_running(self):
        # A suite pointing at a dead URL: a real run would report
        # INCONCLUSIVE, but --dry-run must exit 0 having run nothing.
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "suite.json"
            cfg.write_text(json.dumps({"checks": [
                {"check": "pagination",
                 "url": "http://127.0.0.1:9/api/items"}]}))
            out = subprocess.run(
                [sys.executable, "-m", "apiverdict", "suite",
                 "--config", str(cfg), "--dry-run"],
                capture_output=True, text=True, cwd=Path(__file__).parents[1],
                timeout=30)
        self.assertEqual(out.returncode, 0, out)
        self.assertEqual(out.stdout.strip(), "pagination")


class TestSuiteLevelAuth(unittest.TestCase):
    def test_config_headers_applied(self):
        import json
        import os
        import tempfile
        from pathlib import Path

        import apiverdict.pagination as pagination_mod
        from apiverdict.suite import run_suite

        seen = {}
        orig = pagination_mod.request_json

        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            seen["headers"] = dict(headers or {})
            return 200, {"items": [], "total": 0}, {}

        pagination_mod.request_json = fake
        os.environ["APIVERDICT_TEST_TOKEN"] = "tok-123"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                (tmp / "cfg.json").write_text(json.dumps({
                    "environments": {"local": {"auth": {
                        "type": "bearer", "env_var": "APIVERDICT_TEST_TOKEN"}}}}))
                (tmp / "suite.json").write_text(json.dumps({
                    "config": "cfg.json", "env": "local", "checks": [
                        {"check": "pagination",
                         "url": "http://127.0.0.1:9/api/items",
                         "page_size": 10}]}))
                verdicts = run_suite(tmp / "suite.json")
        finally:
            pagination_mod.request_json = orig
            del os.environ["APIVERDICT_TEST_TOKEN"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(seen["headers"].get("Authorization"),
                         "Bearer tok-123")

    def test_entry_headers_win_over_suite_headers(self):
        import json
        import os
        import tempfile
        from pathlib import Path

        import apiverdict.pagination as pagination_mod
        from apiverdict.suite import run_suite

        seen = {}
        orig = pagination_mod.request_json

        def fake(method, url, payload=None, headers=None, timeout=10.0, **kw):
            seen["headers"] = dict(headers or {})
            return 200, {"items": [], "total": 0}, {}

        pagination_mod.request_json = fake
        os.environ["APIVERDICT_TEST_TOKEN"] = "tok-123"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                (tmp / "cfg.json").write_text(json.dumps({
                    "environments": {"local": {"auth": {
                        "type": "bearer", "env_var": "APIVERDICT_TEST_TOKEN"}}}}))
                (tmp / "suite.json").write_text(json.dumps({
                    "config": "cfg.json", "env": "local", "checks": [
                        {"check": "pagination",
                         "url": "http://127.0.0.1:9/api/items",
                         "page_size": 10,
                         "headers": {"X-Custom": "yes"}}]}))
                run_suite(tmp / "suite.json")
        finally:
            pagination_mod.request_json = orig
            del os.environ["APIVERDICT_TEST_TOKEN"]
        self.assertEqual(seen["headers"], {"X-Custom": "yes"})


class TestEmptySuite(unittest.TestCase):
    def test_empty_verdicts_are_inconclusive_not_pass(self):
        from apiverdict.runner import exit_code_for, overall_status
        self.assertEqual(exit_code_for([]), 2)
        self.assertEqual(overall_status([]), "INCONCLUSIVE")

    def test_empty_suite_config_is_rejected(self):
        import json
        import tempfile
        from apiverdict.suite import load_suite
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"checks": []}, fh)
            path = fh.name
        try:
            with self.assertRaises(ValueError) as ctx:
                load_suite(path)
            self.assertIn("no checks", str(ctx.exception))
        finally:
            import os
            os.unlink(path)


class TestSuiteMissingRequired(unittest.TestCase):
    def test_missing_url_fails_fast(self):
        from apiverdict.suite import load_suite
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"checks": [{"check": "pagination"}]}, f)
            path = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                load_suite(path)
            self.assertIn("url", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_supplied_required_loads(self):
        from apiverdict.suite import load_suite
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"checks": [{"check": "pagination",
                                   "url": "http://127.0.0.1:9/items"}]}, f)
            path = f.name
        try:
            callables = load_suite(path)
            self.assertEqual([name for name, _ in callables], ["pagination"])
        finally:
            os.unlink(path)


class TestSuiteSpecDiffWaivers(unittest.TestCase):
    def test_suite_waiver_file_passes(self):
        import json
        import tempfile
        from pathlib import Path

        from apiverdict.suite import run_suite

        old = {"openapi": "3.0.0",
               "paths": {"/gone": {"get": {"responses": {
                   "200": {"description": "ok"}}}}}}
        new = {"openapi": "3.0.0", "paths": {}}
        waivers = [{"rule": "path-removed", "location": "/gone",
                    "reason": "intentional"}]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "old.json").write_text(json.dumps(old))
            (tmp / "new.json").write_text(json.dumps(new))
            (tmp / "waivers.json").write_text(json.dumps(waivers))
            (tmp / "suite.json").write_text(json.dumps({"checks": [
                {"check": "spec-diff", "old_spec": "old.json",
                 "new_spec": "new.json", "waiver_file": "waivers.json"}]}))
            verdicts = run_suite(tmp / "suite.json")
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].status, "PASS", verdicts[0].summary)
        self.assertEqual(verdicts[0].evidence["waived_count"], 1)

    def test_suite_waiver_non_list_fails_fast(self):
        import json
        import tempfile
        from pathlib import Path

        from apiverdict.suite import load_suite

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "old.json").write_text(json.dumps({"openapi": "3.0.0"}))
            (tmp / "new.json").write_text(json.dumps({"openapi": "3.0.0"}))
            (tmp / "waivers.json").write_text(json.dumps({"rule": "x"}))
            (tmp / "suite.json").write_text(json.dumps({"checks": [
                {"check": "spec-diff", "old_spec": "old.json",
                 "new_spec": "new.json", "waiver_file": "waivers.json"}]}))
            with self.assertRaises(ValueError):
                load_suite(tmp / "suite.json")


class TestSuiteIfMatchEntry(unittest.TestCase):
    def test_if_match_entry_loads(self):
        path = _write_config({"checks": [
            {"check": "if-match", "read_url": "http://127.0.0.1:9/api/s",
             "write_payload": {"theme": "dark"}}]})
        try:
            callables = load_suite(path)
        finally:
            os.unlink(path)
        self.assertEqual([name for name, _ in callables], ["if-match"])

    def test_if_match_missing_read_url_fails_fast(self):
        path = _write_config({"checks": [{"check": "if-match"}]})
        try:
            with self.assertRaises(ValueError) as ctx:
                load_suite(path)
        finally:
            os.unlink(path)
        self.assertIn("missing required", str(ctx.exception))


class TestSuiteFilterEntry(unittest.TestCase):
    def test_suite_filter_entry_passes(self):
        import json
        import tempfile
        from pathlib import Path

        from fixture_util import FixtureServer
        from apiverdict.suite import run_suite

        with FixtureServer("fixed") as base:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = Path(tmp) / "suite.json"
                cfg.write_text(json.dumps({"checks": [
                    {"check": "filter", "url": f"{base}/api/items",
                     "field": "id", "value": "1"}]}))
                verdicts = run_suite(cfg)
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].status, "PASS", verdicts[0].summary)
