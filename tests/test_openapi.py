import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apiverdict.openapi import generate_plan, load_spec, run_openapi_checks
from fixture_util import FixtureServer

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "openapi-sample.json"


class TestOpenAPI(unittest.TestCase):
    def test_plan_discovers_endpoints(self):
        spec = load_spec(SAMPLE)
        plan = generate_plan(spec, "http://127.0.0.1:8765")
        self.assertEqual(len(plan["endpoints"]), 4)
        self.assertEqual(len(plan["detail_candidates"]), 1)
        self.assertEqual(len(plan["pagination"]), 1)
        self.assertEqual(plan["pagination"][0]["url"],
                         "http://127.0.0.1:8765/api/items")
        self.assertEqual(len(plan["error_targets"]), 2)
        self.assertEqual(len(plan["burst_candidates"]), 2)

    def test_yaml_spec_is_refused_plainly(self):
        yaml_path = Path(__file__).resolve().parent / "_tmp_test_spec.yaml"
        yaml_path.write_text("openapi: 3.0.3\npaths: {}\n")
        try:
            with self.assertRaises(ValueError) as ctx:
                load_spec(yaml_path)
            self.assertIn("JSON", str(ctx.exception))
        finally:
            yaml_path.unlink()

    def test_run_against_fixed_fixture_passes(self):
        with FixtureServer("fixed") as base:
            spec = load_spec(SAMPLE)
            verdicts = run_openapi_checks(spec, base)
        self.assertEqual({v.check for v in verdicts}, {"pagination", "error-shape"})
        self.assertTrue(all(v.status == "PASS" for v in verdicts))

    def test_run_against_naive_fixture_fails(self):
        with FixtureServer("naive") as base:
            spec = load_spec(SAMPLE)
            verdicts = run_openapi_checks(spec, base)
        self.assertTrue(all(v.status == "FAIL" for v in verdicts))


if __name__ == "__main__":
    unittest.main()


class TestOpenApiBurstCandidatesListed(unittest.TestCase):
    def test_cli_lists_burst_candidates(self):
        import subprocess
        import sys
        with FixtureServer("fixed") as base:
            out = subprocess.run(
                [sys.executable, "-m", "apiverdict", "openapi",
                 "--spec", str(SAMPLE), "--base-url", base],
                capture_output=True, text=True,
                cwd=Path(__file__).resolve().parents[1], timeout=30)
        self.assertEqual(out.returncode, 0, out)
        self.assertIn("Burst candidates", out.stdout)
        self.assertIn("POST " + base + "/api/book", out.stdout)


class TestOpenapiPlanOnly(unittest.TestCase):
    def test_plan_only_makes_no_network_call(self):
        # Dead base URL on purpose: if plan-only touched the
        # network, it would fail; it only parses the spec.
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "apiverdict", "openapi",
             "--spec", "examples/openapi-sample.json",
             "--base-url", "http://127.0.0.1:9", "--plan-only"],
            capture_output=True, text=True, cwd=".")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pagination: GET http://127.0.0.1:9/api/items", proc.stdout)
        self.assertIn("error-shape: POST http://127.0.0.1:9/api/book", proc.stdout)
        self.assertIn("burst candidate (not auto-run)", proc.stdout)
        self.assertIn("detail-superset candidate (not auto-run)", proc.stdout)
        self.assertIn("http://127.0.0.1:9/api/items -> http://127.0.0.1:9/api/items/{id}", proc.stdout)


class TestPlanTwoXxSchema(unittest.TestCase):
    def test_2xx_only_array_total_is_pagination_candidate(self):
        from apiverdict.openapi import generate_plan
        spec = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
                "paths": {"/api/items": {"get": {"responses": {
                    "2XX": {"description": "ok", "content": {"application/json": {
                        "schema": {"type": "object", "properties": {
                            "items": {"type": "array", "items": {"type": "object"}},
                            "total": {"type": "integer"}}}}}}}}}}}
        plan = generate_plan(spec, "http://127.0.0.1:9")
        self.assertEqual(len(plan["pagination"]), 1)
        self.assertIn("2XX", plan["pagination"][0]["reason"])


class TestPlanQueryTargets(unittest.TestCase):
    def test_query_with_body_is_error_target_not_burst(self):
        from apiverdict.openapi import generate_plan
        spec = {"openapi": "3.2.0", "paths": {"/search": {"query": {
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"]}}}},
            "responses": {"200": {"description": "ok"}}}}}}
        plan = generate_plan(spec, "http://x")
        self.assertIn({"method": "QUERY", "url": "http://x/search"},
                      plan["error_targets"])
        self.assertEqual(plan["burst_candidates"], [])
        self.assertIn({"method": "QUERY", "url": "http://x/search",
                       "operation_id": None}, plan["endpoints"])

    def test_query_without_body_is_no_target(self):
        from apiverdict.openapi import generate_plan
        spec = {"openapi": "3.2.0", "paths": {"/search": {"query": {
            "responses": {"200": {"description": "ok"}}}}}}
        plan = generate_plan(spec, "http://x")
        self.assertEqual(plan["error_targets"], [])



    def test_underscore_page_params_inferred(self):
        from apiverdict.openapi import generate_plan
        spec = {"openapi": "3.0.0", "paths": {"/posts": {"get": {
            "parameters": [{"name": "_page", "in": "query"},
                           {"name": "_limit", "in": "query"}],
            "responses": {"200": {"description": "ok"}}}}}}
        cands = generate_plan(spec, "http://x")["pagination"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["style"], "page")
        self.assertEqual(cands[0]["page_param"], "_page")
        self.assertEqual(cands[0]["size_param"], "_limit")

    def test_run_passes_inferred_params_to_pagination(self):
        import apiverdict.openapi as oa
        from apiverdict.runner import Verdict
        spec = {"openapi": "3.0.0", "paths": {"/posts": {"get": {
            "parameters": [{"name": "_page", "in": "query"},
                           {"name": "_limit", "in": "query"}],
            "responses": {"200": {"description": "ok"}}}}}}
        seen = {}
        orig = oa.run_pagination_check

        def fake(**kw):
            seen.update(kw)
            return Verdict("pagination", "PASS", "ok", {})
        oa.run_pagination_check = fake
        try:
            verdicts = oa.run_openapi_checks(spec, "http://x")
        finally:
            oa.run_pagination_check = orig
        self.assertEqual(verdicts[0].status, "PASS")
        self.assertEqual(seen["style"], "page")
        self.assertEqual(seen["page_param"], "_page")
        self.assertEqual(seen["size_param"], "_limit")


class TestPathItemRefPlan(unittest.TestCase):
    def test_ref_path_item_generates_pagination(self):
        from apiverdict.openapi import generate_plan
        spec = {
            "openapi": "3.1.0",
            "components": {"pathitems": {"Items": {
                "get": {"parameters": [
                    {"name": "offset", "in": "query",
                     "schema": {"type": "integer"}},
                    {"name": "limit", "in": "query",
                     "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "ok"}}}}}},
            "paths": {"/items": {"$ref": "#/components/pathitems/Items"}},
        }
        plan = generate_plan(spec, "http://127.0.0.1:9")
        self.assertEqual(len(plan["pagination"]), 1)
        self.assertIn("/items", plan["pagination"][0]["url"])


class TestDetailCandidates(unittest.TestCase):
    def _spec(self, paths):
        return {"openapi": "3.0.0",
                "paths": {p: {"get": {"responses": {"200": {"description": "ok"}}}}
                          for p in paths}}

    def test_collection_detail_pair_found(self):
        from apiverdict.openapi import generate_plan
        plan = generate_plan(
            self._spec(["/items", "/items/{itemId}", "/other"]),
            "http://127.0.0.1:9")
        self.assertEqual(len(plan["detail_candidates"]), 1)
        cand = plan["detail_candidates"][0]
        self.assertEqual(cand["list_url"], "http://127.0.0.1:9/items")
        self.assertEqual(cand["detail_url"],
                         "http://127.0.0.1:9/items/{itemId}")

    def test_no_pair_no_candidates(self):
        from apiverdict.openapi import generate_plan
        plan = generate_plan(self._spec(["/items", "/orders"]),
                             "http://127.0.0.1:9")
        self.assertEqual(plan["detail_candidates"], [])

    def test_nested_detail_not_paired(self):
        from apiverdict.openapi import generate_plan
        plan = generate_plan(
            self._spec(["/items", "/items/{itemId}/parts"]),
            "http://127.0.0.1:9")
        self.assertEqual(plan["detail_candidates"], [])
