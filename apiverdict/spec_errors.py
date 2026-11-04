"""Spec error-documentation check — static, no network.

For every operation in a JSON OpenAPI spec, is at least one error
response documented (any 4xx/5xx status, or "default")? An API whose
errors are undocumented forces every client to guess: is a failure
a 400 with a body, a 404 with plain text, or a 500 with nothing?
The error-shape check judges whether live errors agree; this check
judges whether the contract even says errors exist.

This is deliberately opt-in (a standalone `spec-errors` command,
not part of the automatic OpenAPI run) and deliberately
opinionated: some teams document only success responses and rely
on a global error format. If that is your policy, do not run this
check — or rather, write the global error format into the spec
once, and this check passes everywhere for free. A check that is
only ever run by teams that already agree with it is still useful
in CI: it stops the agreement from silently eroding, one new
endpoint at a time.

"Default" counts as an error response: in OpenAPI, a default
response is the catch-all for statuses not otherwise documented,
and it is conventionally used for errors. An operation with only
a 200 (or no responses at all) is listed as undocumented.
"""

from __future__ import annotations

from .runner import Verdict
from .spec_util import operations_of, resolve_ref

_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace", "query")


def _documents_error(operation: dict) -> bool:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return False
    for status in responses:
        code = str(status)
        if code == "default" or (code and code[0] in ("4", "5")):
            return True
    return False


def run_spec_errors_check(spec: dict) -> Verdict:
    operations: list[str] = []
    undocumented: list[str] = []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        # Path items may themselves be $refs (OpenAPI 3.1
        # components/pathitems); resolve before reading methods.
        path_item = resolve_ref(path_item, spec)
        if not isinstance(path_item, dict):
            continue
        for method, operation in operations_of(path_item, _METHODS):
            label = f"{method.upper()} {path}"
            operations.append(label)
            if not _documents_error(operation):
                undocumented.append(label)
    evidence = {"operations": len(operations), "undocumented": undocumented}
    if not operations:
        return Verdict("spec-errors", "INCONCLUSIVE",
                       "spec has no operations to judge", evidence)
    if not undocumented:
        return Verdict("spec-errors", "PASS",
                       f"all {len(operations)} operation(s) document an "
                       "error response", evidence)
    shown = undocumented[:20]
    extra = f" (+{len(undocumented) - 20} more)" if len(undocumented) > 20 else ""
    return Verdict("spec-errors", "FAIL",
                   f"{len(undocumented)} of {len(operations)} operation(s) "
                   f"document no error response: {', '.join(shown)}{extra}",
                   evidence)
