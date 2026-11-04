"""Per-endpoint invalid payloads from OpenAPI requestBody schemas.

The v2 error-shape check sent the same generic payload
({"__invalid__": True}) to every endpoint. That proves shape
consistency, but it is a weak probe: many APIs answer any garbage
with the same generic 400, so a real validation inconsistency
(one endpoint validates deeply, another accepts almost anything)
stays hidden.

This helper derives a *targeted* invalid payload per endpoint from
its own schema, in order of preference:

  1. Drop the first required field (missing-field validation).
  2. Else, wrong-type the first typed property (string -> 12345,
     integer -> "not-a-number", boolean -> "yes", array -> {},
     object -> []).
  3. Else, fall back to the generic {"__invalid__": True} payload.

The payload is still invalid input, never a valid request, and the
reason is returned alongside it so the error-shape evidence shows
*what* was probed per endpoint, not just that something was sent.
Nested schemas are resolved with spec_util (local $refs only).
"""

from __future__ import annotations

from .spec_util import resolve_ref

_WRONG_TYPE = {
    "string": 12345,
    "integer": "not-a-number",
    "number": "not-a-number",
    "boolean": "yes",
    "array": {},
    "object": [],
}


def _example_value(schema: dict):
    """A plausible valid value for a property, for building the
    rest of the payload around the one invalid field."""
    if not isinstance(schema, dict):
        return "x"
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    t = schema.get("type")
    if t == "integer":
        return 1
    if t == "number":
        return 1
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    return "x"


def invalid_payload_for(request_schema, spec: dict | None = None) -> tuple[dict, str]:
    """Return (payload, reason) for a requestBody JSON schema."""
    schema = resolve_ref(request_schema, spec or {}) if request_schema else {}
    if not isinstance(schema, dict):
        return {"__invalid__": True}, "no usable schema; generic invalid payload"
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    # Resolve each property: a $ref property carries no "type" of its
    # own, so without this the generator skipped ref'd properties
    # entirely and fell back to the generic payload.
    resolved = {name: resolve_ref(ps, spec or {})
                for name, ps in props.items() if isinstance(ps, dict)}
    required = [f for f in (schema.get("required") or []) if f in props]
    base = {name: _example_value(ps) for name, ps in resolved.items()}
    if required:
        dropped = required[0]
        payload = {k: v for k, v in base.items() if k != dropped}
        return payload, f"dropped required field {dropped!r}"
    for name, ps in resolved.items():
        if isinstance(ps, dict) and ps.get("type") in _WRONG_TYPE:
            payload = dict(base)
            payload[name] = _WRONG_TYPE[ps["type"]]
            return payload, f"wrong-typed field {name!r} (spec says {ps['type']})"
    return {"__invalid__": True}, "no required/typed fields; generic invalid payload"


def request_body_schema(operation: dict, spec: dict | None = None) -> dict | None:
    """Extract the application/json requestBody schema for an operation,
    $ref-resolved, or None."""
    try:
        # Resolve the requestBody itself first: a $ref'd body
        # ({"$ref": "#/components/requestBodies/X"}) has no
        # "content" key until resolved, so without this the
        # generator fell back to the generic payload.
        body = resolve_ref(operation.get("requestBody"), spec or {})
        schema = ((body or {}).get("content", {})
                  .get("application/json", {}).get("schema"))
    except Exception:
        return None
    if not isinstance(schema, dict):
        return None
    return resolve_ref(schema, spec or {})
