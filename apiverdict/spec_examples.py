"""spec-examples — static: a spec's own examples must match its own
schemas.

An example that violates its schema is a self-inconsistent spec:
consumers copy examples, generators emit them, and a reader who
trusts the example builds the wrong request. Checking them is
static, deterministic, and has no false-positive class of its own
— the example is either consistent with the schema it sits next
to or it is not.

Checked, per operation:
  * requestBody: schema.example, content example, and each
    content examples entry's value, against the request schema;
  * each response's content example(s), against that response's
    schema (any status code, since error examples lie too);
  * each parameter's example (and its schema's example),
    against the parameter's schema — a wrong query example
    is copied into client code just as fast as a wrong body.

Validation reuses spec_conformance._check (required, primitive
types, enums, nested objects/arrays, additionalProperties), so
the rules are exactly the live check's rules. Local $refs are
resolved; a schema that is only a remote ref is skipped, not
judged. A spec with no examples at all PASSES with
examples_checked 0 — this check judges consistency, not
coverage; requiring examples would be a different, opinionated
check.
"""

from __future__ import annotations

from .runner import Verdict
from .spec_conformance import _check
from .spec_util import operations_of, resolve_ref

_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace", "query")


def _examples_of(content: dict, spec: dict) -> tuple[list[tuple[str, object]], int]:
    """(label, value) pairs from one media-type content object, plus
    a count of entries that could not be judged. Named examples are
    often $refs to components/examples: resolving them first means
    a ref'd example is judged, not silently invisible (the old code
    only read entries with a literal "value" key). An entry with an
    externalValue URL is never fetched, so it counts as skipped."""
    out = []
    skipped = 0
    if not isinstance(content, dict):
        return out, skipped
    if "example" in content:
        out.append(("example", content["example"]))
    examples = content.get("examples")
    if isinstance(examples, dict):
        for name, entry in examples.items():
            if isinstance(entry, dict) and "$ref" in entry:
                entry = resolve_ref(entry, spec)
            if isinstance(entry, dict) and "value" in entry:
                out.append((f"examples.{name}", entry["value"]))
            elif isinstance(entry, dict) and "externalValue" in entry:
                skipped += 1
            elif isinstance(entry, dict) and _is_remote_ref(entry):
                skipped += 1
    return out, skipped


def _is_remote_ref(schema) -> bool:
    """A schema that is ONLY an unresolved remote $ref: resolve_ref
    passes remote refs through as-is, so without this a remote-ref
    example counted as 'checked' while nothing was judged."""
    return isinstance(schema, dict) and set(schema.keys()) == {"$ref"}


def _schema_examples(schema, spec) -> list[tuple[str, object]]:
    """Examples carried on the schema object itself."""
    schema = resolve_ref(schema, spec)
    if isinstance(schema, dict) and "example" in schema:
        return [("schema.example", schema["example"])]
    return []


def run_spec_examples_check(spec: dict) -> Verdict:
    if not isinstance(spec, dict):
        return Verdict("spec-examples", "INCONCLUSIVE",
                       "spec must be a JSON object", {})
    problems: list[str] = []
    checked = 0
    skipped = 0
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        path_item = resolve_ref(path_item, spec)
        if not isinstance(path_item, dict):
            continue
        for method, op in operations_of(path_item, _METHODS):
            if not isinstance(op, dict):
                continue
            base = f"{method.upper()} {path}"
            for param in op.get("parameters") or []:
                param = resolve_ref(param, spec)
                if not isinstance(param, dict):
                    continue
                schema = resolve_ref(param.get("schema"), spec)
                if _is_remote_ref(schema):
                    skipped += 1
                    continue
                if not isinstance(schema, dict):
                    continue
                examples = []
                if "example" in param:
                    examples.append(("example", param["example"]))
                if "example" in schema:
                    examples.append(("schema.example", schema["example"]))
                for label, value in examples:
                    checked += 1
                    _check(value, schema,
                           f"{base} param {param.get('name')!r} {label}",
                           problems)
            body = resolve_ref(op.get("requestBody"), spec)
            if isinstance(body, dict):
                content = body.get("content") or {}
                media = content.get("application/json") or {}
                schema = resolve_ref(media.get("schema"), spec)
                if _is_remote_ref(schema):
                    skipped += 1
                elif isinstance(schema, dict):
                    pairs, skipped_here = _examples_of(media, spec)
                    skipped += skipped_here
                    for label, value in (pairs
                                         + _schema_examples(media.get("schema"), spec)):
                        checked += 1
                        _check(value, schema, f"{base} requestBody {label}",
                               problems)
                elif media.get("schema"):
                    skipped += 1
            for code, resp in (op.get("responses") or {}).items():
                resp = resolve_ref(resp, spec)
                if not isinstance(resp, dict):
                    continue
                content = resp.get("content") or {}
                media = content.get("application/json") or {}
                schema = resolve_ref(media.get("schema"), spec)
                if _is_remote_ref(schema):
                    skipped += 1
                elif isinstance(schema, dict):
                    pairs, skipped_here = _examples_of(media, spec)
                    skipped += skipped_here
                    for label, value in (pairs
                                         + _schema_examples(media.get("schema"), spec)):
                        checked += 1
                        _check(value, schema,
                               f"{base} response {code} {label}", problems)
                elif media.get("schema"):
                    skipped += 1
    evidence = {"examples_checked": checked, "skipped_remote_ref": skipped,
                "problems": problems}
    if problems:
        return Verdict("spec-examples", "FAIL",
                       f"{len(problems)} example(s) violate their schema",
                       evidence)
    return Verdict("spec-examples", "PASS",
                   f"all {checked} example(s) match their schemas", evidence)
