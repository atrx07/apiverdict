"""OpenAPI-driven check generation (JSON specs only).

Reads a JSON OpenAPI 3.x spec (stdlib json) and builds a check plan
the runner can actually execute — no hand-written endpoint lists.

  * Pagination candidates: GET operations whose parameters include an
    offset/limit, page/size, or cursor parameter, or whose 200
    (or 2XX) response schema has an array + a total-like field. (Heuristic, stated as
    such: the plan lists why each candidate was picked, so a wrong
    guess is visible, not silent.)
  * Error-shape targets: every POST/PUT/PATCH/QUERY operation
    with a requestBody — those are the endpoints that can
    reject invalid input. QUERY carries a body by definition
    (RFC 10008), so a QUERY operation with a requestBody is
    an error target; QUERY is never a burst candidate (safe
    and idempotent, it creates nothing).
  * Burst candidates: POST operations only, listed with a warning that
    a burst still needs a verify URL/command supplied by the user; the
    generator never invents one.

YAML specs are NOT parsed here. YAML is a large language and a
half-broken parser would silently misread a spec and generate wrong
checks — worse than refusing. Convert YAML to JSON first
(e.g. with a YAML tool of your choice) and feed the JSON in. This is
stated in the README and in the error message.

Running a plan executes pagination + error-shape checks (the two that
need no user-supplied verify step). Burst candidates are reported,
not auto-run, for the same reason burst without a verify step is
INCONCLUSIVE by design.
"""

from __future__ import annotations

import json
from pathlib import Path

from .error_payloads import invalid_payload_for, request_body_schema
from .error_shape import run_error_shape_check
from .pagination import run_pagination_check
from .runner import Verdict
from .spec_util import resolve_ref

_PAGE_PARAMS = {"offset", "limit", "page", "size", "cursor", "page_size", "per_page",
                "_page", "_limit", "_offset", "_size", "_cursor", "_per_page"}
_PAGE_LIKE = {"page", "_page"}
_SIZE_LIKE = {"size", "_size", "limit", "_limit", "per_page", "_per_page", "page_size"}
_OFFSET_LIKE = {"offset", "_offset"}
_CURSOR_LIKE = {"cursor", "_cursor"}


def _paging_params(operation: dict, spec: dict | None = None) -> tuple[str, dict]:
    """Infer the walk style + query-param names from the spec's own
    parameters (original casing preserved). Without this, a spec
    using _page/_limit generated no pagination check at all, and
    a detected check ran with default page/size names — which a
    _page/_limit API ignores, tripping the limit-ignored FAIL for
    the wrong reason. Cursor wins over page over offset when an
    operation declares several families (rare); no params at all
    keeps auto detection from the responses."""
    found: dict = {}
    for param in operation.get("parameters") or []:
        if spec is not None:
            param = resolve_ref(param, spec)
        if not isinstance(param, dict) or not param.get("name"):
            continue
        name = str(param["name"])
        low = name.lower()
        if low in _PAGE_LIKE and "page_param" not in found:
            found["page_param"] = name
        elif low in _SIZE_LIKE and "size_param" not in found:
            found["size_param"] = name
        elif low in _OFFSET_LIKE and "offset_param" not in found:
            found["offset_param"] = name
        elif low in _CURSOR_LIKE and "cursor_param" not in found:
            found["cursor_param"] = name
    if "cursor_param" in found:
        return "cursor", found
    if "page_param" in found:
        return "page", found
    if "offset_param" in found:
        return "offset", found
    return "auto", found
_TOTAL_FIELDS = {"total", "total_count", "count", "total_items"}


def spec_base_url(spec: dict) -> str | None:
    """First server URL from the spec, if any. This is the fallback
    base URL when the user does not pass --base-url: a spec that
    names its own server should not need the URL typed twice. A
    templated server URL (with {variables}) is not used — guessing
    variable values would test a different server than the spec
    names."""
    servers = spec.get("servers") or []
    if isinstance(servers, list) and servers:
        url = servers[0].get("url") if isinstance(servers[0], dict) else None
        if isinstance(url, str) and url.startswith(("http://", "https://")) \
                and "{" not in url:
            return url.rstrip("/")
    return None


def load_spec(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"OpenAPI spec not found: {path}")
    text = p.read_text(encoding="utf-8").strip()
    if text.startswith("openapi:") or text.startswith("swagger:") or "\nopenapi:" in text:
        raise ValueError(
            "this looks like a YAML OpenAPI spec; apiverdict reads JSON "
            "specs only — convert it to JSON first (with a YAML tool) "
            "and re-run with the .json file"
        )
    try:
        spec = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"invalid JSON OpenAPI spec {path}: {exc}") from exc
    if not isinstance(spec, dict) or "paths" not in spec:
        raise ValueError("spec must be an OpenAPI document with a 'paths' object")
    version = str(spec.get("openapi", spec.get("swagger", "")))
    if version and not (version.startswith("3.") or version.startswith("2.")):
        raise ValueError(f"unsupported OpenAPI/Swagger version: {version!r}")
    return spec


def _path_params(operation: dict, spec: dict | None = None) -> set[str]:
    names = set()
    for param in operation.get("parameters") or []:
        if spec is not None:
            param = resolve_ref(param, spec)
        if isinstance(param, dict) and param.get("name"):
            names.add(str(param["name"]).lower())
    return names


def _response_has_array_and_total(operation: dict) -> str | None:
    """Return "200" or "2XX" if that success response's schema
    has an array property plus a total field, else None. The 2XX
    fallback mirrors spec-conformance's: a spec that declares
    only the wildcard still promises a success shape."""
    responses = operation.get("responses", {}) or {}
    for key in ("200", "2XX"):
        try:
            schema = (responses.get(key, {})
                      .get("content", {}).get("application/json", {})
                      .get("schema", {}))
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            has_array = any(isinstance(v, dict) and v.get("type") == "array"
                            for v in props.values())
            has_total = bool(set(props) & _TOTAL_FIELDS)
            if has_array and has_total:
                return key
        except Exception:
            continue
    return None


def generate_plan(spec: dict, base_url: str) -> dict:
    base_url = base_url.rstrip("/")
    pagination, error_targets, burst_candidates, endpoints = [], [], [], []
    get_paths = []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        path_item = resolve_ref(path_item, spec)
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "query"):
                continue
            if not isinstance(operation, dict):
                continue
            url = f"{base_url}{path}"
            endpoints.append({"method": method.upper(), "url": url,
                              "operation_id": operation.get("operationId")})
            if method.lower() == "get":
                get_paths.append((path, url))
            params = _path_params(operation, spec)
            schema_key = _response_has_array_and_total(operation)
            if method.lower() == "get" and (params & _PAGE_PARAMS or schema_key):
                reason = ("query params " + ",".join(sorted(params & _PAGE_PARAMS))
                          if params & _PAGE_PARAMS else f"array + total in {schema_key} schema")
                style, paging = _paging_params(operation, spec)
                cand = {"method": "GET", "url": url, "reason": reason,
                        "style": style}
                cand.update(paging)
                pagination.append(cand)
            if method.lower() in ("post", "put", "patch", "query") and operation.get("requestBody") is not None:
                error_targets.append({"method": method.upper(), "url": url})
                if method.lower() == "post":
                    burst_candidates.append({
                        "method": "POST", "url": url,
                        "note": "needs a verify URL/command from the user; not auto-run"})
    # Detail candidates: a GET collection path paired with a GET path
    # that is exactly the collection plus one template segment
    # (/items + /items/{itemId}). Listed, not auto-run — like burst
    # candidates, the detail check needs no extra input, but the
    # run set stays pinned to {pagination, error-shape} (the plan
    # is a review tool; the user runs `detail-superset` explicitly).
    detail_candidates = []
    for coll_path, coll_url in get_paths:
        if "{" in coll_path:
            continue
        for det_path, det_url in get_paths:
            if det_path.startswith(coll_path + "/"):
                rest = det_path[len(coll_path) + 1:]
                if rest.startswith("{") and rest.endswith("}") \
                        and "{" not in rest[1:-1]:
                    detail_candidates.append(
                        {"list_url": coll_url, "detail_url": det_url})
    return {"endpoints": endpoints, "pagination": pagination,
            "error_targets": error_targets, "burst_candidates": burst_candidates,
            "detail_candidates": detail_candidates}


def run_openapi_checks(spec: dict, base_url: str, headers: dict | None = None,
                       page_size: int = 10, timeout: float = 10.0) -> list[Verdict]:
    """Execute the runnable half of a plan: pagination + error-shape.

    Error targets now get a per-endpoint invalid payload derived from
    their own requestBody schema (drop a required field, else
    wrong-type a field), instead of the same generic payload for all
    — see error_payloads. The probe reason per endpoint is recorded
    in the verdict evidence as error_probes."""
    plan = generate_plan(spec, base_url)
    verdicts: list[Verdict] = []
    for cand in plan["pagination"]:
        verdicts.append(run_pagination_check(
            url=cand["url"], page_size=page_size, headers=headers, timeout=timeout,
            style=cand.get("style", "auto"),
            page_param=cand.get("page_param", "page"),
            size_param=cand.get("size_param", "size"),
            offset_param=cand.get("offset_param", "offset"),
            limit_param=cand.get("limit_param", "limit"),
            cursor_param=cand.get("cursor_param", "cursor")))
    if plan["error_targets"]:
        probes = []
        targets = []
        for target in plan["error_targets"]:
            # Recover the operation for this URL to find its schema.
            op = None
            for path, path_item in (spec.get("paths") or {}).items():
                if f"{base_url.rstrip('/')}{path}" == target["url"]:
                    path_item = resolve_ref(path_item, spec)
                    op = (path_item or {}).get(target["method"].lower())
                    break
            schema = request_body_schema(op, spec) if isinstance(op, dict) else None
            payload, reason = invalid_payload_for(schema, spec)
            probes.append({"endpoint": f"{target['method']} {target['url']}",
                           "probe": reason})
            targets.append({**target, "payload": payload})
        verdict = run_error_shape_check(targets=targets, headers=headers, timeout=timeout)
        verdict.evidence["error_probes"] = probes
        verdicts.append(verdict)
    if not verdicts:
        verdicts.append(Verdict("openapi", "INCONCLUSIVE",
                                "spec produced no runnable checks "
                                "(no pagination candidate, no error target)",
                                {"endpoints": len(plan["endpoints"])}))
    return verdicts
