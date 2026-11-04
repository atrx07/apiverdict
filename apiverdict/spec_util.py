"""Shared OpenAPI helpers: local $ref resolution + path-param filling.

Both are deliberately narrow, because both are places where a
half-broken implementation silently tests the wrong thing:

* $ref: only local refs ("#/components/schemas/...", "#/paths/...",
  any "#/..." path into the same document) are resolved. Remote refs
  (http://..., other files) are NOT fetched — a spec check must not
  turn into a network fetch, and must not fail because a URL is down.
  A ref cycle resolves to {} with the cycle recorded in evidence by
  the caller, never an infinite loop.
* Path params: a templated path like /users/{userId} cannot be
  fetched literally. The filler prefers, in order, the parameter's
  `example`, then its `default`, then a type-based placeholder
  (integer -> 1, boolean -> true, otherwise "1"). A filled path that
  404s is still INCONCLUSIVE in the caller (no response to judge),
  not a conformance FAIL — the filler is a best effort, not proof
  that the id exists.
"""

from __future__ import annotations

import urllib.parse


def resolve_ref(schema, spec: dict, _seen: frozenset | None = None):
    """Resolve local $refs in a schema dict, recursively. Non-dict
    schemas pass through unchanged. Remote refs pass through as-is
    (caller treats an unresolved $ref as 'no usable schema', not as
    a schema to judge)."""
    if not isinstance(schema, dict):
        return schema
    if _seen is None:
        _seen = frozenset()
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in _seen:
            return {}
        target = spec
        try:
            for part in ref[2:].split("/"):
                part = urllib.parse.unquote(part.replace("~1", "/").replace("~0", "~"))
                target = target[part]
        except (KeyError, TypeError, IndexError):
            return {}
        resolved = resolve_ref(target, spec, _seen | {ref})
        if not isinstance(resolved, dict):
            return resolved
        merged = dict(resolved)
        for key, value in schema.items():
            if key != "$ref":
                merged[key] = resolve_ref(value, spec, _seen | {ref})
        return merged
    out = {}
    for key, value in schema.items():
        if isinstance(value, dict):
            out[key] = resolve_ref(value, spec, _seen)
        elif isinstance(value, list):
            out[key] = [resolve_ref(v, spec, _seen) if isinstance(v, dict) else v
                        for v in value]
        else:
            out[key] = value
    return out


def operations_of(path_item, methods):
    """(method, operation) pairs for a path item, in stable order.

    OpenAPI 3.2 additionalOperations holds operations under
    arbitrary method names; they compare exactly like fixed
    fields. A fixed field wins over an additionalOperations
    entry of the same (lowercased) name — the spec forbids
    repeating one, so a clash means a malformed spec and the
    fixed field is the safer read. Order: `methods` order
    first, then sorted extras, so evidence order for classic
    specs never changes.
    """
    ops: dict = {}
    if not isinstance(path_item, dict):
        return []
    extra = path_item.get("additionalOperations")
    if isinstance(extra, dict):
        for method, op in extra.items():
            if isinstance(method, str) and isinstance(op, dict):
                ops[method.lower()] = op
    for method in methods:
        op = path_item.get(method)
        if isinstance(op, dict):
            ops[method] = op
    ordered = [m for m in methods if m in ops]
    ordered += sorted(m for m in ops if m not in ordered)
    return [(m, ops[m]) for m in ordered]


def _placeholder(param: dict) -> str:
    if "example" in param:
        return str(param["example"])
    schema = param.get("schema") or {}
    if isinstance(schema, dict):
        if "default" in schema:
            return str(schema["default"])
        if "example" in schema:
            return str(schema["example"])
        if isinstance(schema.get("enum"), list) and schema["enum"]:
            return str(schema["enum"][0])
        t = schema.get("type")
        if t == "integer":
            return "1"
        if t == "number":
            return "1"
        if t == "boolean":
            return "true"
    return "1"


def fill_path_params(path: str, parameters: list[dict], spec: dict | None = None) -> str:
    """Replace {name} segments in a path template from parameter
    examples/defaults/type placeholders. Unknown names get "1".
    A $ref'd parameter has no name until resolved, so with a
    spec it is resolved first (without one, it is skipped as
    before)."""
    by_name = {}
    for param in parameters or []:
        if spec is not None:
            param = resolve_ref(param, spec)
        if isinstance(param, dict) and param.get("name"):
            by_name[str(param["name"])] = _placeholder(param)
    out = path
    start = 0
    while True:
        i = out.find("{", start)
        j = out.find("}", i + 1) if i >= 0 else -1
        if i < 0 or j < 0:
            break
        name = out[i + 1:j]
        out = out[:i] + by_name.get(name, "1") + out[j + 1:]
        start = i + 1
    return out


def fill_query_params(parameters: list[dict], spec: dict | None = None) -> dict[str, str]:
    """Query values for a GET probe, from parameter examples/defaults/
    enums/type placeholders.

    Required query params are always filled (a probe without them
    returns 400 on any spec-conformant server, which proves nothing
    about the response schema). Optional params are filled only when
    they carry an example/default/enum — inventing values for an
    optional filter would test a different endpoint than the spec
    describes. The filled values are recorded in the check evidence
    via the URL itself, so a surprising probe is visible.
    """
    out = {}
    for param in parameters or []:
        if spec is not None:
            param = resolve_ref(param, spec)
        if not isinstance(param, dict) or param.get("in") != "query":
            continue
        name = param.get("name")
        if not name:
            continue
        schema = param.get("schema") or {}
        has_hint = ("example" in param or "default" in schema
                    or "example" in schema
                    or isinstance(schema.get("enum"), list))
        if param.get("required") or has_hint:
            out[str(name)] = _placeholder(param)
    return out
