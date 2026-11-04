"""spec-diff — breaking-change detection between two OpenAPI specs.

Static, no network: compare an old spec with a new spec and fail
on changes that break existing consumers. This is the spec-vs-spec
job (oasdiff's Job 1), not consumer-aware verification — it cannot
tell you whether anyone actually reads a removed field, only that
a field consumers were promised is gone.

Breaking rules (each recorded with rule + location + detail):
  * removed server URL (a client pointed at it loses its endpoint)
  * operation gaining a security requirement (an unauthenticated
    client that worked now fails); losing one is non-breaking
  * removed path, removed operation on a kept path
  * removed webhook (3.1+ top-level `webhooks` map) or removed
    operation on a kept webhook — subscribers are consumers too
  * removed response status code on a kept operation
  * removed request parameter (a client relying on that parameter
    loses functionality), or a parameter that became required,
    changed type, or shrank its enum
  * added request parameter that is required
  * requestBody schema: property removed, property added to
    required, property type changed, property enum shrunk
  * response schema: property removed, property type changed,
    property enum shrunk
  * requestBody added where the old operation took none and the
    new body is required
  * removed request/response content type (a client sending or
    reading it breaks); an added content type is non-breaking

Non-breaking changes are listed separately and never fail the
check: added paths/operations, added optional parameters,
added response properties, added response status codes, grown
enums, loosened request required lists. A response property added to
required is a promise increase, not a break, so it is
non-breaking; a request property added to required is breaking.

Path templates are compared with parameter names erased, and
path parameters are paired positionally: renaming {id} to
{userId} changes no client URL, so it is recorded as a
non-breaking template rename, while a schema change on the
renamed parameter still counts. (A changed parameter COUNT
falls back to the name-keyed diff, where an added/removed path
parameter is breaking.)
Type comparison is exact: string -> integer is breaking even
though both are "scalars". Local $refs are resolved in each spec
before comparing; remote refs are never fetched (an endpoint
whose schema is only a remote ref is skipped, not judged).
Recursion depth is capped at 10; hitting the cap is noted in
evidence, not silently ignored.
"""

from __future__ import annotations

import re

from .runner import Verdict
from .spec_util import operations_of, resolve_ref

_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace", "query")
_MAX_DEPTH = 10


def _type_change(old_type, new_type):
    """Classify a type change. Widening (new type set is a superset
    of the old) is reader-compatible and non-breaking; narrowing or
    any other change is breaking. Plain strings are one-element
    sets, so string -> ["string", "null"] widens instead of
    (wrongly) reading as a breaking change."""
    if not old_type or not new_type or old_type == new_type:
        return None
    old_set = {old_type} if isinstance(old_type, str) else set(old_type)
    new_set = {new_type} if isinstance(new_type, str) else set(new_type)
    if new_set >= old_set:
        return ("nonbreaking", f"type widened {sorted(old_set)} -> {sorted(new_set)}")
    if new_set <= old_set:
        return ("breaking", f"type narrowed {sorted(old_set)} -> {sorted(new_set)}")
    return ("breaking", f"type changed {sorted(old_set)} -> {sorted(new_set)}")


def _resolve(schema, spec):
    if isinstance(schema, dict):
        return resolve_ref(schema, spec)
    return schema


def _diff_schema(old, new, location, context, breaking, nonbreaking,
                 spec_old, spec_new, depth, capped):
    """context: 'request' or 'response' (param schemas use 'request'
    rules for type/enum; required rules only apply to objects)."""
    old = _resolve(old, spec_old)
    new = _resolve(new, spec_new)
    if not isinstance(old, dict) or not isinstance(new, dict):
        return
    if depth > _MAX_DEPTH:
        capped.append(True)
        return
    change = _type_change(old.get("type"), new.get("type"))
    if change:
        kind, detail = change
        target = breaking if kind == "breaking" else nonbreaking
        target.append({"rule": "type-changed" if kind == "breaking"
                       else "type-widened", "location": location,
                       "detail": detail})
        if kind == "breaking":
            return  # sub-comparison is meaningless once the type changed
        # Widened: fall through — property/item sub-checks still apply.
    old_enum, new_enum = old.get("enum"), new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed = [v for v in old_enum if v not in new_enum]
        if removed:
            breaking.append({"rule": "enum-shrunk", "location": location,
                             "detail": f"removed {removed!r}"})
        elif [v for v in new_enum if v not in old_enum]:
            nonbreaking.append({"rule": "enum-grown", "location": location,
                                "detail": ""})
    old_props = old.get("properties") if isinstance(old.get("properties"), dict) else {}
    new_props = new.get("properties") if isinstance(new.get("properties"), dict) else {}
    for field in old_props:
        if field not in new_props:
            breaking.append({"rule": "property-removed", "location": f"{location}.{field}",
                             "detail": f"{context} property removed"})
        else:
            _diff_schema(old_props[field], new_props[field], f"{location}.{field}",
                         context, breaking, nonbreaking, spec_old, spec_new,
                         depth + 1, capped)
    for field in new_props:
        if field not in old_props:
            nonbreaking.append({"rule": "property-added", "location": f"{location}.{field}",
                                "detail": f"{context} property added"})
    old_req = set(old.get("required") or [])
    new_req = set(new.get("required") or [])
    for field in new_req - old_req:
        if context == "request":
            breaking.append({"rule": "required-added", "location": f"{location}.{field}",
                             "detail": "request property became required"})
        else:
            nonbreaking.append({"rule": "required-added", "location": f"{location}.{field}",
                                "detail": "response promise increased"})
    for field in old_req - new_req:
        if context == "response":
            # The server no longer guarantees the field: a client
            # that reads it can now get nothing. oasdiff agrees
            # (response-required-property-removed is breaking).
            # Request-side loosening stays non-breaking: old
            # requests remain valid.
            breaking.append({"rule": "required-removed", "location": f"{location}.{field}",
                             "detail": "response property became optional (promise decreased)"})
        else:
            nonbreaking.append({"rule": "required-removed", "location": f"{location}.{field}",
                                "detail": "requirement loosened"})
    old_items, new_items = old.get("items"), new.get("items")
    if isinstance(old_items, dict) and isinstance(new_items, dict):
        _diff_schema(old_items, new_items, f"{location}[]",
                     context, breaking, nonbreaking, spec_old, spec_new,
                     depth + 1, capped)


def _norm_path(path: str) -> str:
    """Path template with parameter names erased: /users/{id} and
    /users/{userId} are the same route for every client, so a pure
    parameter rename must not read as a removed path."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _params_by_key(operation, spec):
    out = {}
    for param in operation.get("parameters") or []:
        param = _resolve(param, spec)
        if isinstance(param, dict) and "name" in param:
            out[(param.get("name"), param.get("in"))] = param
    return out


def _security_names(operation, spec):
    """Effective security scheme names: the operation's own list,
    else the spec-global list. An explicit empty list means no
    security (it overrides the global list)."""
    sec = operation.get("security", spec.get("security"))
    if not isinstance(sec, list):
        return set()
    names = set()
    for req in sec:
        if isinstance(req, dict):
            names.update(req.keys())
    return names


def _diff_operation(method, path, old_op, new_op, breaking, nonbreaking,
                    spec_old, spec_new, capped,
                    path_servers_old=None, path_servers_new=None):
    base = f"{method.upper()} {path}"
    old_sec = _security_names(old_op, spec_old)
    new_sec = _security_names(new_op, spec_new)
    for scheme in sorted(new_sec - old_sec):
        breaking.append({"rule": "security-added", "location": base,
                         "detail": f"operation now requires security scheme "
                                   f"{scheme!r}"})
    for scheme in sorted(old_sec - new_sec):
        nonbreaking.append({"rule": "security-removed", "location": base,
                            "detail": f"security scheme {scheme!r} no longer "
                                      "required"})
    # operationId is the generated client's method name: renaming it
    # breaks codegen consumers even though the HTTP contract is
    # unchanged, so it is breaking, with the reason in the detail.
    old_id, new_id = old_op.get("operationId"), new_op.get("operationId")
    if old_id != new_id and old_id is not None and new_id is not None:
        breaking.append({"rule": "operation-id-changed", "location": base,
                         "detail": f"operationId {old_id!r} -> {new_id!r} "
                                   f"(generated clients key on operationId)"})
    # Operation-level servers override the global list, so a change
    # here is client-facing even when the global list is untouched.
    # Only compared when at least one side defines operation servers;
    # otherwise the operation inherits the global list, already diffed.
    if "servers" in old_op or "servers" in new_op:
        old_op_servers = {s.get("url") for s in (old_op.get("servers") or [])}
        new_op_servers = {s.get("url") for s in (new_op.get("servers") or [])}
        for url in sorted(old_op_servers - new_op_servers):
            breaking.append({"rule": "operation-server-removed", "location": base,
                             "detail": f"operation server {url!r} removed"})
        for url in sorted(new_op_servers - old_op_servers):
            nonbreaking.append({"rule": "operation-server-added", "location": base,
                                "detail": f"operation server {url!r} added"})
    elif path_servers_old is not None or path_servers_new is not None:
        # Path-item servers apply to every operation under the path
        # unless the operation overrides them (handled above), so a
        # path-level removal is client-facing even when the global
        # list is untouched. Same no-double-counting rule: only
        # compared when at least one side defines path servers.
        old_path_servers = {s.get("url") for s in (path_servers_old or [])}
        new_path_servers = {s.get("url") for s in (path_servers_new or [])}
        for url in sorted(old_path_servers - new_path_servers):
            breaking.append({"rule": "path-server-removed", "location": base,
                             "detail": f"path-item server {url!r} removed"})
        for url in sorted(new_path_servers - old_path_servers):
            nonbreaking.append({"rule": "path-server-added", "location": base,
                                "detail": f"path-item server {url!r} added"})
    old_params = _params_by_key(old_op, spec_old)
    new_params = _params_by_key(new_op, spec_new)
    # Path parameters are paired positionally, not by name: renaming
    # {id} to {userId} changes no client URL, so it must not read as
    # a removed + added parameter. Schema changes on the paired
    # parameter still count (type/enum rules below).
    old_path_params = [p for p in old_params.values() if p.get("in") == "path"]
    new_path_params = [p for p in new_params.values() if p.get("in") == "path"]
    if len(old_path_params) == len(new_path_params):
        for old_param, new_param in zip(old_path_params, new_path_params):
            old_params.pop((old_param.get("name"), "path"), None)
            new_params.pop((new_param.get("name"), "path"), None)
            _diff_schema(old_param.get("schema") or {},
                         new_param.get("schema") or {},
                         f"{base} param {old_param.get('name')!r} (path)",
                         "request", breaking, nonbreaking,
                         spec_old, spec_new, 0, capped)
    # If the counts differ, the path params stay in the name-keyed
    # diff below, where an added/removed path parameter is breaking.
    for key, old_param in old_params.items():
        name, where = key
        loc = f"{base} param {name!r} ({where})"
        if key not in new_params:
            breaking.append({"rule": "parameter-removed", "location": loc,
                             "detail": "request parameter removed"})
            continue
        new_param = new_params[key]
        if not old_param.get("required") and new_param.get("required"):
            breaking.append({"rule": "parameter-required", "location": loc,
                             "detail": "parameter became required"})
        _diff_schema(old_param.get("schema") or {}, new_param.get("schema") or {},
                     loc, "request", breaking, nonbreaking,
                     spec_old, spec_new, 0, capped)
    for key, new_param in new_params.items():
        if key not in old_params:
            name, where = key
            if new_param.get("required"):
                breaking.append({"rule": "parameter-added-required",
                                 "location": f"{base} param {name!r} ({where})",
                                 "detail": "new required parameter"})
            else:
                nonbreaking.append({"rule": "parameter-added",
                                    "location": f"{base} param {name!r} ({where})",
                                    "detail": "new optional parameter"})
    old_body = _resolve(old_op.get("requestBody"), spec_old)
    new_body = _resolve(new_op.get("requestBody"), spec_new)
    if isinstance(old_body, dict) and isinstance(new_body, dict):
        # Media types: dropping a request content type breaks
        # clients that send it; adding one never does. (The
        # schema diff below only looks at application/json, so
        # without this a JSON -> XML swap was silently missed.)
        old_media = set((old_body.get("content") or {}).keys())
        new_media = set((new_body.get("content") or {}).keys())
        for media in sorted(old_media - new_media):
            breaking.append({"rule": "request-media-type-removed",
                             "location": base,
                             "detail": f"request content type {media!r} removed"})
        for media in sorted(new_media - old_media):
            nonbreaking.append({"rule": "request-media-type-added",
                                "location": base,
                                "detail": f"request content type {media!r} added"})
        old_schema = (old_body.get("content") or {}).get("application/json", {}).get("schema")
        new_schema = (new_body.get("content") or {}).get("application/json", {}).get("schema")
        if isinstance(old_schema, dict) and isinstance(new_schema, dict):
            _diff_schema(old_schema, new_schema, f"{base} requestBody",
                         "request", breaking, nonbreaking,
                         spec_old, spec_new, 0, capped)
    elif old_body is None and isinstance(new_body, dict) and new_body.get("required"):
        breaking.append({"rule": "requestBody-added-required", "location": base,
                         "detail": "operation now requires a request body"})
    old_resps = old_op.get("responses") or {}
    new_resps = new_op.get("responses") or {}
    for code, old_resp in old_resps.items():
        loc = f"{base} response {code}"
        if code not in new_resps:
            breaking.append({"rule": "response-removed", "location": loc,
                             "detail": "response status code removed"})
            continue
        old_resolved = _resolve(old_resp, spec_old)
        new_resolved = _resolve(new_resps[code], spec_new)
        if isinstance(old_resolved, dict) and isinstance(new_resolved, dict):
            old_media = set((old_resolved.get("content") or {}).keys())
            new_media = set((new_resolved.get("content") or {}).keys())
            for media in sorted(old_media - new_media):
                breaking.append({"rule": "response-media-type-removed",
                                 "location": loc,
                                 "detail": f"response content type {media!r} removed"})
            for media in sorted(new_media - old_media):
                nonbreaking.append({"rule": "response-media-type-added",
                                    "location": loc,
                                    "detail": f"response content type {media!r} added"})
        old_schema = ((old_resolved.get("content") or {}).get(
            "application/json", {}).get("schema")
            if isinstance(old_resolved, dict) else None)
        new_schema = ((new_resolved.get("content") or {}).get(
            "application/json", {}).get("schema")
            if isinstance(new_resolved, dict) else None)
        if isinstance(old_schema, dict) and isinstance(new_schema, dict):
            _diff_schema(old_schema, new_schema, loc,
                         "response", breaking, nonbreaking,
                         spec_old, spec_new, 0, capped)
    for code in new_resps:
        if code not in old_resps:
            nonbreaking.append({"rule": "response-added",
                                "location": f"{base} response {code}",
                                "detail": "new response status code"})


def run_spec_diff_check(old_spec: dict, new_spec: dict,
                        waivers: list | None = None) -> Verdict:
    """Compare two parsed OpenAPI specs. FAIL iff any UNWAIVED
    breaking change remains.

    waivers is a list of {"rule", "location"} dicts (optional
    "reason"). Matching is exact on rule AND location, so a
    waiver can never hide a break it does not name: a waiver
    that matches nothing is reported as unused, and a break
    no waiver names still fails. Waived breaks stay visible
    in evidence ("waived") — a PASS with waivers is a loud
    PASS, never a silent one. Malformed waiver entries are
    INCONCLUSIVE, not silently skipped."""
    if not isinstance(old_spec, dict) or not isinstance(new_spec, dict):
        return Verdict("spec-diff", "INCONCLUSIVE",
                       "both specs must be JSON objects", {})
    breaking, nonbreaking, capped = [], [], []
    old_servers = {s.get("url") for s in (old_spec.get("servers") or [])
                   if isinstance(s, dict) and s.get("url")}
    new_servers = {s.get("url") for s in (new_spec.get("servers") or [])
                   if isinstance(s, dict) and s.get("url")}
    for url in sorted(old_servers - new_servers):
        breaking.append({"rule": "server-removed", "location": "servers",
                         "detail": f"server URL removed: {url}"})
    for url in sorted(new_servers - old_servers):
        nonbreaking.append({"rule": "server-added", "location": "servers",
                            "detail": f"server URL added: {url}"})
    old_paths = old_spec.get("paths") or {}
    new_paths = new_spec.get("paths") or {}
    new_by_norm = {_norm_path(p): p for p in new_paths}
    matched_new: set = set()
    for path, old_item in old_paths.items():
        if path in new_paths:
            new_path = path
        elif _norm_path(path) in new_by_norm:
            new_path = new_by_norm[_norm_path(path)]
            nonbreaking.append({"rule": "path-template-renamed",
                                "location": path,
                                "detail": f"path template parameter renamed "
                                          f"({path} -> {new_path}); client "
                                          "URLs unchanged"})
        else:
            breaking.append({"rule": "path-removed", "location": path,
                             "detail": "entire path removed"})
            continue
        matched_new.add(new_path)
        new_item = new_paths[new_path]
        old_item = resolve_ref(old_item, old_spec)
        new_item = resolve_ref(new_item, new_spec)
        old_ops = dict(operations_of(old_item, _METHODS))
        new_ops = dict(operations_of(new_item, _METHODS))
        methods = [m for m, _ in operations_of(old_item, _METHODS)]
        for m, _ in operations_of(new_item, _METHODS):
            if m not in methods:
                methods.append(m)
        for method in methods:
            old_op = old_ops.get(method)
            new_op = new_ops.get(method)
            if old_op and not new_op:
                breaking.append({"rule": "operation-removed",
                                 "location": f"{method.upper()} {path}",
                                 "detail": "operation removed"})
            elif not old_op and new_op:
                nonbreaking.append({"rule": "operation-added",
                                    "location": f"{method.upper()} {path}",
                                    "detail": "new operation"})
            elif old_op and new_op:
                _diff_operation(method, path, old_op, new_op,
                                breaking, nonbreaking, old_spec, new_spec, capped,
                                path_servers_old=old_item.get("servers") if isinstance(old_item, dict) else None,
                                path_servers_new=new_item.get("servers") if isinstance(new_item, dict) else None)
    for path in new_paths:
        if path not in matched_new and _norm_path(path) not in {
                _norm_path(p) for p in old_paths}:
            nonbreaking.append({"rule": "path-added", "location": path,
                                "detail": "new path"})
    # Top-level webhooks (3.1+): subscribers are consumers too, so a
    # removed webhook event breaks them exactly like a removed path
    # breaks API clients. Webhook names are event names, not URL
    # templates, so matching is exact (no template-rename rule).
    # Locations read "POST webhook <name>" to stay distinct from
    # path locations.
    old_hooks = old_spec.get("webhooks") or {}
    new_hooks = new_spec.get("webhooks") or {}
    if not isinstance(old_hooks, dict):
        old_hooks = {}
    if not isinstance(new_hooks, dict):
        new_hooks = {}
    for name, old_item in old_hooks.items():
        if name not in new_hooks:
            breaking.append({"rule": "webhook-removed",
                             "location": f"webhook {name}",
                             "detail": "entire webhook removed"})
            continue
        new_item = new_hooks[name]
        old_item = resolve_ref(old_item, old_spec)
        new_item = resolve_ref(new_item, new_spec)
        old_ops = dict(operations_of(old_item, _METHODS))
        new_ops = dict(operations_of(new_item, _METHODS))
        methods = [m for m, _ in operations_of(old_item, _METHODS)]
        for m, _ in operations_of(new_item, _METHODS):
            if m not in methods:
                methods.append(m)
        for method in methods:
            old_op = old_ops.get(method)
            new_op = new_ops.get(method)
            if old_op and not new_op:
                breaking.append({"rule": "operation-removed",
                                 "location": f"{method.upper()} webhook {name}",
                                 "detail": "webhook operation removed"})
            elif not old_op and new_op:
                nonbreaking.append({"rule": "operation-added",
                                    "location": f"{method.upper()} webhook {name}",
                                    "detail": "new webhook operation"})
            elif old_op and new_op:
                _diff_operation(method, f"webhook {name}", old_op, new_op,
                                breaking, nonbreaking, old_spec, new_spec, capped,
                                path_servers_old=old_item.get("servers") if isinstance(old_item, dict) else None,
                                path_servers_new=new_item.get("servers") if isinstance(new_item, dict) else None)
    for name in new_hooks:
        if name not in old_hooks:
            nonbreaking.append({"rule": "webhook-added",
                                "location": f"webhook {name}",
                                "detail": "new webhook"})
    waived, unused = [], []
    if waivers is not None:
        for index, entry in enumerate(waivers):
            if not isinstance(entry, dict) or not isinstance(
                    entry.get("rule"), str) or not isinstance(
                    entry.get("location"), str):
                return Verdict("spec-diff", "INCONCLUSIVE",
                               f"waiver entry {index} must be an object "
                               "with string 'rule' and 'location'", {})
        remaining = []
        for change in breaking:
            match = next((w for w in waivers
                          if w["rule"] == change["rule"]
                          and w["location"] == change["location"]), None)
            if match is None:
                remaining.append(change)
            else:
                entry = dict(change)
                if isinstance(match.get("reason"), str):
                    entry["waiver_reason"] = match["reason"]
                waived.append(entry)
        used = {(w["rule"], w["location"]) for w in waivers}
        matched = {(c["rule"], c["location"]) for c in breaking}
        unused = [{"rule": r, "location": loc}
                  for (r, loc) in sorted(used - matched)]
        breaking = remaining
    evidence = {"breaking_count": len(breaking),
                "nonbreaking_count": len(nonbreaking),
                "breaking": breaking, "nonbreaking": nonbreaking,
                "waived_count": len(waived), "waived": waived,
                "unused_waivers": unused}
    if capped:
        evidence["depth_cap_hit"] = True
    if breaking:
        return Verdict("spec-diff", "FAIL",
                       f"{len(breaking)} breaking spec change(s)"
                       + (f" ({len(waived)} waived, see evidence)"
                          if waived else ""),
                       evidence)
    if waived:
        return Verdict("spec-diff", "PASS",
                       f"no unwaived breaking spec changes "
                       f"({len(waived)} waived, see evidence)",
                       evidence)
    return Verdict("spec-diff", "PASS",
                   f"no breaking spec changes ({len(nonbreaking)} "
                   "non-breaking noted)", evidence)
