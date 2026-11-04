"""Detail-superset check — a detail response must cover its list row.

Clients share one type for a resource between list and detail
views. A detail response that DROPS a field the list row carries,
or retypes it, breaks that type — even though each response
looks fine alone. This check fetches a list (first page only;
the shape question needs one row, not a full walk), takes the
first row, fetches its detail URL, and requires:

  * every top-level key of the row present in the detail
    object (a missing key fails, named),
  * every shared key with the same JSON type on both sides,
    with nulls exempt on either side (a nullable field may
    read null in one view and typed in the other).

Value differences are NOT judged: the data may legitimately
change between the two GETs; only the shape must agree. A
detail response that ADDS fields is fine (detail views are
normally richer). An empty list, an unresolvable row id, a
non-2xx detail fetch, or a non-object detail body is
INCONCLUSIVE — nothing to judge, not a failure.

Read-only: GET + GET, so no remote guard is needed.
"""

from __future__ import annotations

import re

from .http_util import request_json
from .pagination import _items_of, _json_type
from .runner import Verdict


def _resolve_dotted(row: dict, field: str):
    current: object = row
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def run_detail_superset_check(
    list_url: str,
    detail_url: str,
    id_field: str = "id",
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> Verdict:
    try:
        status, body, _h = request_json(
            "GET", list_url, headers=headers, timeout=timeout)
    except Exception as exc:
        return Verdict("detail-superset", "INCONCLUSIVE",
                       f"list fetch failed: {exc}", {})
    if not (200 <= status < 300):
        return Verdict("detail-superset", "INCONCLUSIVE",
                       f"list fetch returned HTTP {status}", {})
    items, _meta = _items_of(body)
    if not items or not isinstance(items[0], dict):
        return Verdict("detail-superset", "INCONCLUSIVE",
                       "list is empty or its first row is not an "
                       "object: no row to compare", {})
    row = items[0]
    row_id, found = _resolve_dotted(row, id_field)
    if not found:
        return Verdict("detail-superset", "INCONCLUSIVE",
                       f"first row has no id field {id_field!r}", {})
    url = re.sub(r"\{[^}]+\}", str(row_id), detail_url, count=1)
    try:
        dstatus, detail, _h2 = request_json(
            "GET", url, headers=headers, timeout=timeout)
    except Exception as exc:
        return Verdict("detail-superset", "INCONCLUSIVE",
                       f"detail fetch failed: {exc}", {})
    if not (200 <= dstatus < 300) or not isinstance(detail, dict):
        return Verdict("detail-superset", "INCONCLUSIVE",
                       f"detail fetch unusable (HTTP {dstatus})", {})
    missing = [k for k in row if k not in detail]
    retyped = [k for k in row
               if k in detail and row[k] is not None
               and detail[k] is not None
               and _json_type(row[k]) != _json_type(detail[k])]
    evidence = {"list_url": list_url, "detail_url": url,
                "row_id": row_id, "missing": missing,
                "retyped": retyped}
    problems = []
    if missing:
        problems.append(f"detail response is missing list-row field(s): "
                        f"{', '.join(sorted(missing))}")
    if retyped:
        problems.append(f"detail response retypes list-row field(s): "
                        f"{', '.join(sorted(retyped))}")
    if problems:
        return Verdict("detail-superset", "FAIL", "; ".join(problems),
                       evidence)
    return Verdict("detail-superset", "PASS",
                   f"detail response covers all {len(row)} list-row "
                   f"field(s) with matching types", evidence)


__all__ = ["run_detail_superset_check"]
