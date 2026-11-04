"""Spec-conformance check — live responses vs the OpenAPI schema.

Contract testing in the narrow, honest sense: for each GET endpoint
in a JSON OpenAPI spec, fetch the endpoint and check the 200
response against its schema (or the 2XX wildcard's, when no
200 schema is declared):

  * every `required` property is present, and every
    `dependentRequired` dependency is present when its
    trigger field is (a partial object where a dependent
    field is missing breaks the same contract),
  * every declared property's value has the declared primitive type
    (string / integer / number / boolean / array / object / null;
    integer rejects booleans, because in JSON `true` is not an
    integer; OpenAPI 3.1 type lists like ["string", "null"] are
    honoured — the value must match at least one listed type),
  * `enum` values are honoured,
  * scalar bounds are honoured (pattern, minimum/maximum with
    both exclusive forms, min/maxLength, min/maxItems,
    min/maxProperties) — deliberately not full JSON Schema:
    no allOf/anyOf/oneOf, no format assertions,
  * nested objects and arrays of objects are checked recursively
    (depth capped at 10; deeper schemas are checked to that depth
    and the cap is noted in evidence, not silently ignored),
  * additionalProperties schemas: every undeclared key's value is
    checked against the additionalProperties schema (and
    additionalProperties: false flags any undeclared key) — an
    object schema with no declared properties is no longer a
    free pass,
  * the 200 response's Content-Type must be one of the media types
    the spec declares for it (parameters like charset ignored) —
    or the 2XX wildcard's media types, when no 200 is declared;
    a declared application/json endpoint answering text/html is a
    contract violation even if the body happens to parse. A missing
    Content-Type is not judged — the body is the contract there.
  * local $refs (#/components/schemas/...) are resolved in-document;
    remote refs are NOT fetched, and an endpoint whose schema is
    only a remote ref is skipped, not judged,
  * a property declared writeOnly that appears in a live
    RESPONSE fails (sensitive-data leak: writeOnly means
    request-only). Response examples are exempt — the
    spec-examples check leaves this rule off, because request
    examples should contain writeOnly fields.
  * documented non-200 responses are judged too when the spec
    declares an error schema for them (exact status, then the
    4XX/5XX wildcard, then `default`): a 404 body violating its
    declared schema fails, exactly like a 200 body does. A
    documented status with no declared schema stays unjudged.
    (The media-type rule above applies to the success response:
    200, or 2XX when no 200 is declared.)
  * templated paths (/users/{userId}) are filled from parameter
    examples/defaults/type placeholders (see spec_util), and required
    query params are filled the same way (a probe without a required
    query param returns 400 on a conformant server, which proves
    nothing). A filled path that 404s, or any endpoint that cannot
    be fetched, is recorded as UNJUDGED and the check continues with
    the other endpoints — one bad endpoint no longer aborts the
    whole check, and a check that judges nothing is INCONCLUSIVE.
  * 3.2 `query` operations are listed as unjudged, never silently
    dropped — judging a QUERY 200 needs a request body the
    checker cannot invent (see the QUERY-example rejection in
    the notes: examples are too unreliable to probe with).

This is still NOT full JSON Schema validation — no format checks,
no allOf/anyOf/oneOf composition, no conditional schemas. (Scalar
bounds — pattern, minimum/maximum, min/maxLength, min/maxItems,
min/maxProperties — ARE honoured; the earlier "no bounds" line was
stale after they were added.) Those would need a real validator; the
scope above is what stdlib can do deterministically in CI, and it
catches the common breaking change: a field renamed, dropped,
retyped, or moved, while the endpoint still returns 200.

Non-GET endpoints are skipped (they need request bodies to be
meaningful), as are GET endpoints with no usable 200 schema. A
transport failure or a non-200 on any checked endpoint is
INCONCLUSIVE, not a pass and not a conformance FAIL — the check
cannot judge a response it never got.
"""

from __future__ import annotations

import re
import urllib.parse

from .http_util import request_json
from .runner import Verdict
from .spec_util import fill_path_params, fill_query_params, resolve_ref

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}

_MAX_DEPTH = 10


def _extract_json_schema(resp, spec) -> dict | None:
    try:
        if spec is not None:
            # A $ref'd response object has no "content" until
            # resolved; without this it read as "no schema" and
            # the endpoint was skipped instead of judged.
            resp = resolve_ref(resp, spec)
        schema = ((resp or {}).get("content", {})
                  .get("application/json", {}).get("schema", {}))
        return schema if isinstance(schema, dict) else None
    except Exception:
        return None


def _schema_of(operation: dict, spec: dict | None = None) -> dict | None:
    responses = operation.get("responses", {}) or {}
    schema = _extract_json_schema(responses.get("200", {}), spec)
    if schema:
        return schema
    # A spec that declares only the 2XX wildcard still promises
    # a success shape: judge the 200 body against it, rather
    # than skipping an endpoint whose contract is declared.
    return _extract_json_schema(responses.get("2XX", {}), spec)


def _schema_of_status(operation: dict, spec: dict | None,
                      status: int) -> dict | None:
    """Declared error schema for a non-200 status: exact code, then
    the 4XX/5XX wildcard, then `default`. A spec that declares no
    error schema yields None (unjudged, as before)."""
    responses = operation.get("responses") or {}
    for key in (str(status), f"{status // 100}XX", "default"):
        resp = responses.get(key)
        if resp is not None:
            schema = _extract_json_schema(resp, spec)
            if schema:
                return schema
    return None


def _judgeable(schema) -> bool:
    """Same gate as the 200 path: a dict with properties, or an
    object/array top type (including 3.1 type lists)."""
    if not isinstance(schema, dict):
        return False
    top = schema.get("type")
    top_is_obj_or_arr = top in ("object", "array") or (
        isinstance(top, list) and ("object" in top or "array" in top))
    return isinstance(schema.get("properties"), dict) or top_is_obj_or_arr


def _judge_body(url, body, schema, problems) -> bool:
    # Live responses only: forbid writeOnly properties (see _check).
    # _judge_body is called only from the live-response paths, so the
    # flag is unconditional here, not another parameter.
    """Check one body against its schema. Returns True when the
    endpoint counts as checked; an empty array body counts as
    skipped (nothing to check), mirroring the 200 path."""
    top = schema.get("type") if isinstance(schema, dict) else None
    if top == "array" or (isinstance(top, list) and "array" in top):
        if not isinstance(body, list):
            problems.append(f"{url}: response is not a JSON array")
            return True
        if not body:
            return False
        _check(body, schema, url, problems, forbid_write_only=True)
        return True
    if not isinstance(body, dict):
        problems.append(f"{url}: response is not a JSON object")
        return True
    _check(body, schema, url, problems, forbid_write_only=True)
    return True


def _content_types(operation: dict, spec: dict | None = None) -> set[str]:
    """Declared media types for the 200 response (lowercased),
    falling back to the 2XX wildcard like _schema_of does."""
    try:
        responses = operation.get("responses", {}) or {}
        resp = responses.get("200") or responses.get("2XX", {})
        if spec is not None:
            resp = resolve_ref(resp, spec)
        content = (resp or {}).get("content", {})
        if not isinstance(content, dict):
            return set()
        return {str(k).lower() for k in content.keys()}
    except Exception:
        return set()


def _check(value, schema, location: str, problems: list[str], depth: int = 0,
           forbid_write_only: bool = False) -> None:
    """Append conformance problems for one value against one schema.

    forbid_write_only is set by the live-response check only: a
    property declared writeOnly that appears in a RESPONSE is a
    leak (writeOnly means "request-only"). spec-examples leaves
    it off, because request examples SHOULD contain writeOnly
    fields — flagging them there would invert the rule."""
    if not isinstance(schema, dict) or depth > _MAX_DEPTH:
        return
    if value is None and schema.get("nullable") is True:
        # OpenAPI 3.0 nullable: null is explicitly permitted
        # alongside the declared type. Without this, a nullable
        # field reading null false-FAILED ("type NoneType, spec
        # says string"). 3.1 dropped nullable in favour of type
        # lists containing "null", already handled below.
        return
    expected = schema.get("type")
    checker = _TYPE_CHECKS.get(expected) if isinstance(expected, str) else None
    if checker and not checker(value):
        problems.append(f"{location}: type {type(value).__name__}, spec says {expected}")
        return
    if isinstance(expected, list):
        # OpenAPI 3.1 type lists (e.g. ["string", "null"]): the value
        # must match at least one listed type. Without this, a
        # list-typed field passed vacuously — no type was ever
        # checked, so a retyped field sailed through as conforming.
        # Property/item sub-checks below still run off the value's
        # actual shape (properties/items presence), not the list.
        checkers = [_TYPE_CHECKS[t] for t in expected if t in _TYPE_CHECKS]
        if checkers and not any(c(value) for c in checkers):
            problems.append(f"{location}: type {type(value).__name__}, "
                            f"spec says one of {expected}")
            return
    if "enum" in schema and isinstance(schema["enum"], list):
        if value not in schema["enum"]:
            problems.append(f"{location}: value {value!r} not in enum {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        problems.append(f"{location}: value {value!r} != const {schema['const']!r}")
    # Scalar bounds — deliberately NOT full JSON Schema (no
    # allOf/anyOf/oneOf, no format assertions, no $dynamicRef).
    # A declared bound that live data breaks is a contract
    # violation: a negative price where minimum is 0, an empty
    # name where minLength is 1. Value-based dispatch, so
    # 3.1 type-list values get bounds too. An uncompilable
    # pattern judges nothing (a spec bug, not a live one).
    if isinstance(value, str):
        min_len, max_len = schema.get("minLength"), schema.get("maxLength")
        if isinstance(min_len, int) and len(value) < min_len:
            problems.append(f"{location}: length {len(value)} below minLength {min_len}")
        if isinstance(max_len, int) and len(value) > max_len:
            problems.append(f"{location}: length {len(value)} above maxLength {max_len}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    problems.append(f"{location}: value {value!r} does not match pattern {pattern!r}")
            except re.error:
                pass
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        excl_min, excl_max = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            problems.append(f"{location}: value {value} below minimum {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            problems.append(f"{location}: value {value} above maximum {maximum}")
        if excl_min is True and isinstance(minimum, (int, float)) and value <= minimum:
            problems.append(f"{location}: value {value} not above exclusiveMinimum {minimum}")
        elif isinstance(excl_min, (int, float)) and value <= excl_min:
            problems.append(f"{location}: value {value} not above exclusiveMinimum {excl_min}")
        if excl_max is True and isinstance(maximum, (int, float)) and value >= maximum:
            problems.append(f"{location}: value {value} not below exclusiveMaximum {maximum}")
        elif isinstance(excl_max, (int, float)) and value >= excl_max:
            problems.append(f"{location}: value {value} not below exclusiveMaximum {excl_max}")
    if isinstance(value, list):
        min_items, max_items = schema.get("minItems"), schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            problems.append(f"{location}: {len(value)} items below minItems {min_items}")
        if isinstance(max_items, int) and len(value) > max_items:
            problems.append(f"{location}: {len(value)} items above maxItems {max_items}")
    if isinstance(value, dict):
        min_props, max_props = schema.get("minProperties"), schema.get("maxProperties")
        if isinstance(min_props, int) and len(value) < min_props:
            problems.append(f"{location}: {len(value)} properties below minProperties {min_props}")
        if isinstance(max_props, int) and len(value) > max_props:
            problems.append(f"{location}: {len(value)} properties above maxProperties {max_props}")
    if expected == "object" or isinstance(schema.get("properties"), dict):
        if not isinstance(value, dict):
            return
        props = schema.get("properties") or {}
        for field in schema.get("required") or []:
            if field not in value:
                problems.append(f"{location}: missing required field {field!r}")
        dep_req = schema.get("dependentRequired")
        if isinstance(dep_req, dict):
            for trigger, dependents in dep_req.items():
                if trigger in value and isinstance(dependents, list):
                    for dep in dependents:
                        if dep not in value:
                            problems.append(f"{location}: field {trigger!r} present "
                                            f"but dependent required field {dep!r} missing")
        for field, field_schema in props.items():
            if field in value and isinstance(field_schema, dict):
                if forbid_write_only and field_schema.get("writeOnly") is True:
                    problems.append(f"{location}.{field}: writeOnly "
                                    f"property present in response "
                                    f"(sensitive data leak)")
                _check(value[field], field_schema, f"{location}.{field}",
                       problems, depth + 1, forbid_write_only)
        # additionalProperties: the Petstore /store/inventory case —
        # an object schema with no declared properties but a value
        # schema for every other key. Without this, such an endpoint
        # "passed" as a bare object, checking nothing at all.
        extra = schema.get("additionalProperties")
        if extra is False:
            for field in value:
                if field not in props:
                    problems.append(f"{location}: additional property "
                                    f"{field!r} is not allowed by the schema")
        elif isinstance(extra, dict):
            for field, field_value in value.items():
                if field not in props:
                    _check(field_value, extra, f"{location}.{field}",
                           problems, depth + 1, forbid_write_only)
    if (expected == "array" or isinstance(schema.get("items"), dict)
            or isinstance(schema.get("prefixItems"), list)):
        if not isinstance(value, list):
            return
        # OpenAPI 3.1 prefixItems: positional tuple schemas.
        # Without this, a prefixItems-only schema (no type,
        # no items) skipped the array branch entirely and a
        # mistyped coordinate pair sailed through.
        prefix = schema.get("prefixItems")
        if isinstance(prefix, list):
            for idx, element in enumerate(value):
                if idx < len(prefix) and isinstance(prefix[idx], dict):
                    _check(element, prefix[idx], f"{location}[{idx}]",
                           problems, depth + 1, forbid_write_only)
        if schema.get("uniqueItems") is True:
            # Same spirit as the scalar bounds: a declared
            # uniqueness constraint that live data breaks is
            # a contract violation. Keys are JSON renderings
            # so unhashable elements (objects) compare too.
            import json as _json
            seen = set()
            for element in value:
                try:
                    key = _json.dumps(element, sort_keys=True)
                except (TypeError, ValueError):
                    key = repr(element)
                if key in seen:
                    problems.append(f"{location}: duplicate item "
                                    f"violates uniqueItems")
                    break
                seen.add(key)
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            # JSON Schema 2020-12: items applies only to
            # elements AFTER prefixItems, not to all of them.
            start = len(prefix) if isinstance(prefix, list) else 0
            for idx, element in enumerate(value):
                if idx < start:
                    continue
                _check(element, items_schema, f"{location}[{idx}]",
                       problems, depth + 1, forbid_write_only)


def run_spec_conformance_check(spec: dict, base_url: str,
                               headers: dict | None = None,
                               timeout: float = 10.0) -> Verdict:
    base_url = base_url.rstrip("/")
    checked, problems, skipped, filled, unjudged = [], [], 0, [], []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        path_item = resolve_ref(path_item, spec)
        if not isinstance(path_item, dict):
            continue
        query_op = path_item.get("query")
        if isinstance(query_op, dict):
            # QUERY is a defined read method (RFC 10008), but
            # judging its 200 needs a request body the checker
            # cannot invent — so it is listed as unjudged,
            # never silently ignored and never a false FAIL.
            unjudged.append({"url": f"{base_url}{path}",
                             "reason": "QUERY operation not judged "
                                       "(needs a request body)"})
        get_op = path_item.get("get")
        if not isinstance(get_op, dict):
            continue
        raw_schema = _schema_of(get_op, spec)
        if not raw_schema:
            skipped += 1
            continue
        schema = resolve_ref(raw_schema, spec)
        top = schema.get("type") if isinstance(schema, dict) else None
        top_is_obj_or_arr = top in ("object", "array") or (
            isinstance(top, list) and ("object" in top or "array" in top))
        if not isinstance(schema, dict) or (
                not isinstance(schema.get("properties"), dict)
                and not top_is_obj_or_arr):
            skipped += 1
            continue
        params = list(path_item.get("parameters") or []) + \
            list(get_op.get("parameters") or [])
        filled_path = fill_path_params(path, params, spec)
        if filled_path != path:
            filled.append(filled_path)
        query = fill_query_params(params, spec)
        url = f"{base_url}{filled_path}"
        if query:
            url = url + "?" + urllib.parse.urlencode(query)
        try:
            status, body, resp_headers = request_json("GET", url, headers=headers, timeout=timeout)
        except Exception as exc:
            # One unreachable endpoint must not abort the whole check:
            # record it as unjudged and keep checking the rest. A check
            # that judges nothing is still INCONCLUSIVE at the end.
            unjudged.append({"url": url, "reason": f"request failed: {exc}"})
            continue
        if status != 200:
            # An UNDOCUMENTED status is a contract violation in
            # itself: the spec's contract never covered that
            # response, so it fails instead of passing silently.
            # A DOCUMENTED status with a declared error schema gets
            # its body judged against that schema (exact code, then
            # wildcard, then default); documented without a schema
            # stays unjudged, as before.
            responses = get_op.get("responses") or {}
            documented = (str(status) in responses or "default" in responses
                          or f"{status // 100}XX" in responses)
            if not documented:
                problems.append(f"{url}: returned HTTP {status}, not "
                                f"documented by the spec")
                checked.append(url)
                continue
            err_raw = _schema_of_status(get_op, spec, status)
            err_schema = resolve_ref(err_raw, spec) if err_raw else None
            if err_schema and _judgeable(err_schema):
                if _judge_body(url, body, err_schema, problems):
                    checked.append(url)
                else:
                    skipped += 1
            else:
                unjudged.append({"url": url, "reason": f"HTTP {status}"})
            continue
        declared = _content_types(get_op, spec)
        if declared:
            actual = ""
            for name, value in (resp_headers or {}).items():
                if name.lower() == "content-type":
                    actual = str(value).split(";")[0].strip().lower()
                    break
            if actual and actual not in declared:
                problems.append(f"{url}: Content-Type {actual!r}, spec "
                                f"declares {sorted(declared)}")
                checked.append(url)
                continue
        if _judge_body(url, body, schema, problems):
            checked.append(url)
        else:
            skipped += 1

    evidence = {"checked_endpoints": checked, "skipped": skipped,
                "problems": problems, "filled_paths": filled,
                "unjudged": unjudged,
                "depth_cap": _MAX_DEPTH}
    if not checked:
        return Verdict("spec-conformance", "INCONCLUSIVE",
                       "no GET endpoint could be judged "
                       f"({len(unjudged)} unjudged, {skipped} skipped)",
                       evidence)
    if problems:
        return Verdict("spec-conformance", "FAIL", "; ".join(problems[:20]) +
                       (f" (+{len(problems) - 20} more)" if len(problems) > 20 else ""),
                       evidence)
    return Verdict("spec-conformance", "PASS",
                   f"{len(checked)} endpoint(s) conform to their spec schemas",
                   evidence)
