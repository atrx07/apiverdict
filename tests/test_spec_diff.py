"""spec-diff: breaking changes between two specs must FAIL with the
right rules; purely additive changes must PASS."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apiverdict.spec_diff import run_spec_diff_check
from apiverdict.suite import run_suite


def _old():
    return {
        "openapi": "3.0.0",
        "paths": {
            "/pets": {
                "get": {
                    "parameters": [
                        {"name": "limit", "in": "query", "required": False,
                         "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "name": {"type": "string"},
                                "status": {"type": "string",
                                           "enum": ["active", "archived"]}},
                            "required": ["id"]}}}},
                        "404": {"description": "not found"}}},
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"},
                                       "tag": {"type": "string"}},
                        "required": ["name"]}}}},
                    "responses": {"201": {"description": "created"}}}},
            "/old": {"get": {"responses": {"200": {"description": "ok"}}}},
        },
    }


def _new_breaking():
    spec = _old()
    get = spec["paths"]["/pets"]["get"]
    get["parameters"] = []  # limit removed
    schema = get["responses"]["200"]["content"]["application/json"]["schema"]
    del schema["properties"]["name"]  # response property removed
    schema["properties"]["status"]["enum"] = ["active"]  # enum shrunk
    schema["properties"]["extra"] = {"type": "string"}  # added: non-breaking
    del get["responses"]["404"]  # response removed
    post = spec["paths"]["/pets"]["post"]
    post["parameters"] = [
        {"name": "dry_run", "in": "query", "required": True,
         "schema": {"type": "boolean"}},
        {"name": "verbose", "in": "query", "required": False,
         "schema": {"type": "boolean"}}]
    body = post["requestBody"]["content"]["application/json"]["schema"]
    body["required"] = ["name", "tag"]  # tag now required
    body["properties"]["name"] = {"type": "integer"}  # type changed
    del spec["paths"]["/old"]  # path removed
    spec["paths"]["/new"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    return spec


def _new_additive():
    spec = _old()
    get = spec["paths"]["/pets"]["get"]
    get["parameters"].append(
        {"name": "verbose", "in": "query", "required": False,
         "schema": {"type": "boolean"}})
    schema = get["responses"]["200"]["content"]["application/json"]["schema"]
    schema["properties"]["extra"] = {"type": "string"}
    schema["properties"]["status"]["enum"] = ["active", "archived", "new"]
    spec["paths"]["/new"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    return spec


class TestSpecDiff(unittest.TestCase):
    def test_breaking_pair_fails_with_expected_rules(self):
        verdict = run_spec_diff_check(_old(), _new_breaking())
        self.assertEqual(verdict.status, "FAIL", verdict)
        rules = {b["rule"] for b in verdict.evidence["breaking"]}
        for expected in ("parameter-removed", "response-removed",
                         "property-removed", "enum-shrunk", "required-added",
                         "parameter-added-required", "type-changed",
                         "path-removed"):
            self.assertIn(expected, rules, verdict.evidence["breaking"])
        self.assertIn("parameter-added",
                      {b["rule"] for b in verdict.evidence["nonbreaking"]})

    def test_additive_pair_passes(self):
        verdict = run_spec_diff_check(_old(), _new_additive())
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertEqual(verdict.evidence["breaking_count"], 0)

    def test_ref_resolution(self):
        old = {"paths": {"/p": {"get": {"responses": {
            "200": {"content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/Pet"}}}}}}}},
            "components": {"schemas": {"Pet": {
                "type": "object",
                "properties": {"id": {"type": "integer"},
                               "name": {"type": "string"}}}}}}
        new = json.loads(json.dumps(old))
        del new["components"]["schemas"]["Pet"]["properties"]["name"]
        verdict = run_spec_diff_check(old, new)
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any(b["rule"] == "property-removed"
                            for b in verdict.evidence["breaking"]))

    def test_suite_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "old.json").write_text(json.dumps(_old()))
            (tmp / "new.json").write_text(json.dumps(_new_breaking()))
            (tmp / "suite.json").write_text(json.dumps({"checks": [
                {"check": "spec-diff", "old_spec": "old.json",
                 "new_spec": "new.json"}]}))
            verdicts = run_suite(tmp / "suite.json")
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].status, "FAIL", verdicts[0])
        self.assertEqual(verdicts[0].check, "spec-diff")


if __name__ == "__main__":
    unittest.main()


class TestQueryAndAdditionalOperations(unittest.TestCase):
    def _spec(self, ops):
        return {"openapi": "3.2.0",
                "paths": {"/search": ops}}

    def test_operation_removed_is_breaking(self):
        # QUERY and additionalOperations are both non-standard
        # operation kinds; one test each direction covers both.
        for ops, label in (
                ({"query": {"responses": {"200": {"description": "ok"}}}},
                 "QUERY /search"),
                ({"additionalOperations": {
                    "COPY": {"responses": {"200": {"description": "ok"}}}}},
                 "COPY /search")):
            v = run_spec_diff_check(self._spec(ops), self._spec({}))
            self.assertEqual(v.status, "FAIL", v)
            self.assertIn(("operation-removed", label),
                          {(b["rule"], b["location"]) for b in v.evidence["breaking"]})

    def test_operation_added_is_not_breaking(self):
        for ops, label in (
                ({"query": {"responses": {"200": {"description": "ok"}}}},
                 "QUERY /search"),
                ({"additionalOperations": {
                    "COPY": {"responses": {"200": {"description": "ok"}}}}},
                 "COPY /search")):
            v = run_spec_diff_check(self._spec({}), self._spec(ops))
            self.assertEqual(v.status, "PASS", v)
            self.assertIn(("operation-added", label),
                          {(b["rule"], b["location"]) for b in v.evidence["nonbreaking"]})



    def _pair(self, old_path, new_path, old_param, new_param):
        old = {"paths": {old_path: {"get": {
            "parameters": [old_param],
            "responses": {"200": {"description": "ok"}}}}}}
        new = {"paths": {new_path: {"get": {
            "parameters": [new_param],
            "responses": {"200": {"description": "ok"}}}}}}
        return run_spec_diff_check(old, new)

    def test_pure_rename_passes(self):
        verdict = self._pair(
            "/users/{id}", "/users/{userId}",
            {"name": "id", "in": "path", "required": True,
             "schema": {"type": "string"}},
            {"name": "userId", "in": "path", "required": True,
             "schema": {"type": "string"}})
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertTrue(any(b["rule"] == "path-template-renamed"
                            for b in verdict.evidence["nonbreaking"]))

    def test_renamed_param_type_change_still_fails(self):
        verdict = self._pair(
            "/users/{id}", "/users/{userId}",
            {"name": "id", "in": "path", "required": True,
             "schema": {"type": "string"}},
            {"name": "userId", "in": "path", "required": True,
             "schema": {"type": "integer"}})
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any(b["rule"] == "type-changed"
                            for b in verdict.evidence["breaking"]))


class TestServerDiff(unittest.TestCase):
    def test_removed_server_fails_added_passes(self):
        old = {"servers": [{"url": "https://api.example.com"},
                           {"url": "https://staging.example.com"}],
               "paths": {}}
        new = {"servers": [{"url": "https://api.example.com"},
                           {"url": "https://api2.example.com"}],
               "paths": {}}
        verdict = run_spec_diff_check(old, new)
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any(b["rule"] == "server-removed"
                            for b in verdict.evidence["breaking"]))
        self.assertTrue(any(b["rule"] == "server-added"
                            for b in verdict.evidence["nonbreaking"]))
        verdict2 = run_spec_diff_check(
            {"servers": [{"url": "https://a.example.com"}], "paths": {}},
            {"servers": [{"url": "https://a.example.com"},
                         {"url": "https://b.example.com"}], "paths": {}})
        self.assertEqual(verdict2.status, "PASS", verdict2)


class TestSecurityDiff(unittest.TestCase):
    def test_added_security_fails_removed_passes(self):
        base_op = {"responses": {"200": {"description": "ok"}}}
        old = {"paths": {"/p": {"get": base_op}}, "security": []}
        new = {"paths": {"/p": {"get": dict(base_op, security=[{"ApiKey": []}])}}}
        verdict = run_spec_diff_check(old, new)
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any(b["rule"] == "security-added"
                            for b in verdict.evidence["breaking"]))
        verdict2 = run_spec_diff_check(new, old)
        self.assertEqual(verdict2.status, "PASS", verdict2)
        self.assertTrue(any(b["rule"] == "security-removed"
                            for b in verdict2.evidence["nonbreaking"]))


class TestTypeWidening(unittest.TestCase):
    def test_widening_passes_narrowing_fails(self):
        def pair(old_t, new_t):
            old = {"paths": {"/p": {"get": {"responses": {
                "200": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {"v": {"type": old_t}}}}}}}}}}}
            new = {"paths": {"/p": {"get": {"responses": {
                "200": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {"v": {"type": new_t}}}}}}}}}}}
            return run_spec_diff_check(old, new)
        widened = pair("string", ["string", "null"])
        self.assertEqual(widened.status, "PASS", widened)
        self.assertTrue(any(b["rule"] == "type-widened"
                            for b in widened.evidence["nonbreaking"]))
        narrowed = pair(["string", "null"], "string")
        self.assertEqual(narrowed.status, "FAIL", narrowed)
        changed = pair("string", "integer")
        self.assertEqual(changed.status, "FAIL", changed)


class TestResponseRequiredRemoved(unittest.TestCase):
    def _spec(self, required):
        schema = {"type": "object",
                  "properties": {"id": {"type": "integer"},
                                 "name": {"type": "string"}},
                  "required": required}
        content = {"application/json": {"schema": schema}}
        resp = {"200": {"description": "ok", "content": content}}
        op = {"get": {"responses": resp}}
        return {"openapi": "3.0.0", "paths": {"/api/item": op}}

    def test_response_field_becoming_optional_is_breaking(self):
        verdict = run_spec_diff_check(self._spec(["id", "name"]),
                                      self._spec(["id"]))
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any(b["rule"] == "required-removed"
                            for b in verdict.evidence["breaking"]), verdict)

    def test_request_field_becoming_optional_is_not_breaking(self):
        def spec(required):
            schema = {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": required}
            content = {"application/json": {"schema": schema}}
            op = {"post": {"requestBody": {"content": content},
                           "responses": {"200": {"description": "ok"}}}}
            return {"openapi": "3.0.0", "paths": {"/api/item": op}}

        verdict = run_spec_diff_check(spec(["name"]), spec([]))
        self.assertEqual(verdict.status, "PASS", verdict)


class TestMediaTypeDiff(unittest.TestCase):
    def _spec(self, media):
        content = {m: {"schema": {"type": "object",
                                  "properties": {"id": {"type": "integer"}}}}
                   for m in media}
        op = {"get": {"responses": {"200": {"description": "ok",
                                            "content": content}}}}
        return {"openapi": "3.0.0", "paths": {"/api/item": op}}

    def test_response_media_type_removed_is_breaking(self):
        verdict = run_spec_diff_check(
            self._spec(["application/json"]),
            self._spec(["application/xml"]))
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any(b["rule"] == "response-media-type-removed"
                            for b in verdict.evidence["breaking"]), verdict)

    def test_response_media_type_added_is_not_breaking(self):
        verdict = run_spec_diff_check(
            self._spec(["application/json"]),
            self._spec(["application/json", "application/xml"]))
        self.assertEqual(verdict.status, "PASS", verdict)
        self.assertTrue(any(b["rule"] == "response-media-type-added"
                            for b in verdict.evidence["nonbreaking"]), verdict)


class TestRequestMediaTypeDiff(unittest.TestCase):
    def _spec(self, media):
        content = {m: {"schema": {"type": "object",
                                  "properties": {"name": {"type": "string"}}}}
                   for m in media}
        op = {"post": {"requestBody": {"content": content},
                       "responses": {"200": {"description": "ok"}}}}
        return {"openapi": "3.0.0", "paths": {"/api/item": op}}

    def test_request_media_type_removed_is_breaking(self):
        verdict = run_spec_diff_check(
            self._spec(["application/json"]),
            self._spec(["application/xml"]))
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertTrue(any(b["rule"] == "request-media-type-removed"
                            for b in verdict.evidence["breaking"]), verdict)


class TestOperationServers(unittest.TestCase):
    def _spec(self, servers):
        op = {"responses": {"200": {"description": "ok"}}}
        if servers is not None:
            op["servers"] = [{"url": u} for u in servers]
        return {"openapi": "3.0.0",
                "paths": {"/items": {"get": op}}}

    def test_operation_server_removed_is_breaking(self):
        from apiverdict.spec_diff import run_spec_diff_check
        v = run_spec_diff_check(
            self._spec(["https://a.example.com", "https://b.example.com"]),
            self._spec(["https://a.example.com"]))
        self.assertEqual(v.status, "FAIL")
        self.assertTrue(any(e["rule"] == "operation-server-removed"
                            for e in v.evidence["breaking"]))

    def test_operation_server_added_is_not_breaking(self):
        from apiverdict.spec_diff import run_spec_diff_check
        v = run_spec_diff_check(
            self._spec(["https://a.example.com"]),
            self._spec(["https://a.example.com", "https://b.example.com"]))
        self.assertEqual(v.status, "PASS")
        self.assertTrue(any(e["rule"] == "operation-server-added"
                            for e in v.evidence["nonbreaking"]))

    def test_no_operation_servers_means_no_server_verdict(self):
        from apiverdict.spec_diff import run_spec_diff_check
        v = run_spec_diff_check(self._spec(None), self._spec(None))
        self.assertEqual(v.status, "PASS")
        self.assertFalse(any("server" in e["rule"]
                             for e in v.evidence["breaking"]
                             + v.evidence["nonbreaking"]))


class TestOperationIdChanged(unittest.TestCase):
    def _spec(self, op_id):
        op = {"responses": {"200": {"description": "ok"}}}
        if op_id is not None:
            op["operationId"] = op_id
        return {"openapi": "3.0.0",
                "paths": {"/items": {"get": op}}}

    def test_operation_id_rename_is_breaking(self):
        from apiverdict.spec_diff import run_spec_diff_check
        v = run_spec_diff_check(self._spec("listItems"),
                                self._spec("listAllItems"))
        self.assertEqual(v.status, "FAIL")
        self.assertTrue(any(e["rule"] == "operation-id-changed"
                            for e in v.evidence["breaking"]))

    def test_operation_id_added_or_removed_is_not_breaking(self):
        # Adding an id where none existed (or dropping one) does
        # not rename anything a generated client already calls.
        from apiverdict.spec_diff import run_spec_diff_check
        v = run_spec_diff_check(self._spec(None), self._spec("listItems"))
        self.assertEqual(v.status, "PASS")
        v = run_spec_diff_check(self._spec("listItems"), self._spec(None))
        self.assertEqual(v.status, "PASS")


class TestPathItemRefDiff(unittest.TestCase):
    def test_ref_path_item_breaking_change_found(self):
        from apiverdict.spec_diff import run_spec_diff_check
        item_old = {"get": {"responses": {
            "200": {"description": "ok"},
            "404": {"description": "not found"}}}}
        item_new = {"get": {"responses": {
            "200": {"description": "ok"}}}}
        old = {"openapi": "3.1.0",
               "components": {"pathitems": {"Items": item_old}},
               "paths": {"/items": {"$ref": "#/components/pathitems/Items"}}}
        new = {"openapi": "3.1.0",
               "components": {"pathitems": {"Items": item_new}},
               "paths": {"/items": {"$ref": "#/components/pathitems/Items"}}}
        v = run_spec_diff_check(old, new)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertTrue(any(e["rule"] == "response-removed"
                            for e in v.evidence["breaking"]))


class TestPathItemServers(unittest.TestCase):
    def _spec(self, servers):
        item = {"get": {"responses": {"200": {"description": "ok"}}}}
        if servers is not None:
            item["servers"] = [{"url": u} for u in servers]
        return {"openapi": "3.0.0", "paths": {"/items": item}}

    def test_path_server_removed_fails(self):
        from apiverdict.spec_diff import run_spec_diff_check
        v = run_spec_diff_check(self._spec(["https://a.example", "https://b.example"]),
                                self._spec(["https://a.example"]))
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertTrue(any(b["rule"] == "path-server-removed"
                            for b in v.evidence["breaking"]))

    def test_path_server_added_passes(self):
        from apiverdict.spec_diff import run_spec_diff_check
        v = run_spec_diff_check(self._spec(["https://a.example"]),
                                self._spec(["https://a.example", "https://b.example"]))
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertTrue(any(b["rule"] == "path-server-added"
                            for b in v.evidence["nonbreaking"]))


class TestSpecDiffWaivers(unittest.TestCase):
    def _pair(self):
        old = {"openapi": "3.0.0",
               "paths": {"/users": {"get": {"responses": {
                   "200": {"description": "ok"}}}},
                         "/gone": {"get": {"responses": {
                             "200": {"description": "ok"}}}}}}
        new = {"openapi": "3.0.0",
               "paths": {"/users": {"get": {"responses": {
                   "200": {"description": "ok"}}}}}}
        return old, new

    def test_waived_break_passes_loudly(self):
        from apiverdict.spec_diff import run_spec_diff_check
        old, new = self._pair()
        v = run_spec_diff_check(old, new, waivers=[
            {"rule": "path-removed", "location": "/gone",
             "reason": "intentional v2 cleanup"}])
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertEqual(v.evidence["waived_count"], 1)
        self.assertEqual(v.evidence["waived"][0]["waiver_reason"],
                         "intentional v2 cleanup")
        self.assertIn("waived", v.summary)

    def test_partial_waiver_still_fails(self):
        from apiverdict.spec_diff import run_spec_diff_check
        old, new = self._pair()
        v = run_spec_diff_check(old, new, waivers=[
            {"rule": "path-removed", "location": "/other"}])
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertEqual(v.evidence["breaking_count"], 1)
        self.assertEqual(len(v.evidence["unused_waivers"]), 1)

    def test_malformed_waiver_is_inconclusive(self):
        from apiverdict.spec_diff import run_spec_diff_check
        old, new = self._pair()
        v = run_spec_diff_check(old, new, waivers=[{"rule": "path-removed"}])
        self.assertEqual(v.status, "INCONCLUSIVE")

    def test_no_waivers_unchanged(self):
        from apiverdict.spec_diff import run_spec_diff_check
        old, new = self._pair()
        v = run_spec_diff_check(old, new)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertEqual(v.evidence["waived_count"], 0)


class TestWebhooksDiff(unittest.TestCase):
    def _hook(self):
        return {"post": {
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object",
                "properties": {"id": {"type": "integer"},
                               "name": {"type": "string"}},
                "required": ["id"]}}}},
            "responses": {"200": {"description": "ack"}}}}

    def _old(self):
        return {"openapi": "3.1.0", "paths": {},
                "webhooks": {"petAdded": self._hook()}}

    def test_webhook_operation_removed_fails(self):
        old = self._old()
        new = {"openapi": "3.1.0", "paths": {},
               "webhooks": {"petAdded": {}}}
        v = run_spec_diff_check(old, new)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertTrue(any(c["rule"] == "operation-removed" and
                            c["location"] == "POST webhook petAdded"
                            for c in v.evidence["breaking"]))

    def test_webhook_removed_fails(self):
        old = self._old()
        new = {"openapi": "3.1.0", "paths": {}, "webhooks": {}}
        v = run_spec_diff_check(old, new)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertTrue(any(c["rule"] == "webhook-removed" and
                            c["location"] == "webhook petAdded"
                            for c in v.evidence["breaking"]))

    def test_webhook_added_passes(self):
        old = {"openapi": "3.1.0", "paths": {}, "webhooks": {}}
        new = self._old()
        v = run_spec_diff_check(old, new)
        self.assertEqual(v.status, "PASS", v.summary)
        self.assertTrue(any(c["rule"] == "webhook-added" and
                            c["location"] == "webhook petAdded"
                            for c in v.evidence["nonbreaking"]))

    def test_webhook_property_removed_fails(self):
        old = self._old()
        new = self._old()
        schema = new["webhooks"]["petAdded"]["post"]["requestBody"][
            "content"]["application/json"]["schema"]
        del schema["properties"]["name"]
        v = run_spec_diff_check(old, new)
        self.assertEqual(v.status, "FAIL", v.summary)
        self.assertTrue(any(c["rule"] == "property-removed"
                            for c in v.evidence["breaking"]))

    def test_no_webhooks_unchanged(self):
        spec = {"openapi": "3.1.0", "paths": {}}
        v = run_spec_diff_check(spec, spec)
        self.assertEqual(v.status, "PASS", v.summary)
