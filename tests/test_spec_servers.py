"""Spec servers fallback: `openapi` / `spec-check` without --base-url
use the spec's first server URL; a templated or missing server URL
is an exit-2 error, not a guessed URL."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.openapi import spec_base_url
from fixture_util import FixtureServer

ROOT = Path(__file__).resolve().parents[1]


class TestSpecBaseUrl(unittest.TestCase):
    def test_first_server_used(self):
        spec = {"servers": [{"url": "https://staging.example.com/api/"},
                            {"url": "https://other.example.com"}]}
        self.assertEqual(spec_base_url(spec),
                         "https://staging.example.com/api")

    def test_templated_server_not_used(self):
        spec = {"servers": [{"url": "https://{host}.example.com/api"}]}
        self.assertIsNone(spec_base_url(spec))

    def test_missing_servers_is_none(self):
        self.assertIsNone(spec_base_url({"openapi": "3.0.3", "paths": {}}))


class TestSpecServersCli(unittest.TestCase):
    def test_openapi_uses_spec_server_when_no_base_url(self):
        with FixtureServer("fixed") as base:
            spec = {
                "openapi": "3.0.3",
                "servers": [{"url": base}],
                "paths": {"/api/items": {"get": {
                    "parameters": [
                        {"name": "offset", "in": "query",
                         "schema": {"type": "integer"}},
                        {"name": "limit", "in": "query",
                         "schema": {"type": "integer"}}],
                    "responses": {"200": {"content": {"application/json": {
                        "schema": {"type": "object", "properties": {
                            "items": {"type": "array"},
                            "total": {"type": "integer"}}}}}}}}}}}
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", delete=False) as fh:
                json.dump(spec, fh)
                spec_path = fh.name
            out = subprocess.run(
                [sys.executable, "-m", "apiverdict", "openapi",
                 "--spec", spec_path],
                capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)

    def test_openapi_without_base_or_server_is_exit_2(self):
        out = subprocess.run(
            [sys.executable, "-m", "apiverdict", "openapi",
             "--spec", "examples/openapi-sample.json"],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(out.returncode, 2)
        self.assertIn("no server URL", out.stderr)


if __name__ == "__main__":
    unittest.main()
