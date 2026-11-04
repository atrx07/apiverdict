"""List-filter check: a list endpoint that accepts a filter
parameter must actually apply it.

Walk the list with ?field=value applied (via _walk_once, so
all pagination styles work and the filter param survives
offset/page walks; Link/cursor servers must echo it in
their next URLs, and a server that drops it mid-walk fails
here — its pagination lost the filter, which is a real
bug). Every returned row must satisfy field == value
(top-level field only; comparison is stringified and
casefolded, so 5 matches "5" and True matches "true").
A row violating the predicate PROVES the filter did not
apply — unlike sort order, where identical orders can be
coincidence, a predicate violation is exact. That is why
this check exists and a sort-ignored check does not.

INCONCLUSIVE when the walk fails, when no rows come back,
or when the field appears in no row at all (a wrong field
name is a config error, not a filter finding).
Read-only, so no remote guard.
"""

import urllib.parse

from .http_util import DEFAULT_TIMEOUT
from .pagination import _row_id, _walk_once
from .runner import Verdict


def run_filter_check(url, field, value, page_size=10, style="auto",
                     max_pages=100, id_field="id", headers=None,
                     timeout=DEFAULT_TIMEOUT, page_param="page",
                     size_param="size", offset_param="offset",
                     limit_param="limit", cursor_param="cursor"):
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    query[field] = str(value)
    filtered_url = urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query)))
    rows: list = []
    _ids, info, error = _walk_once(
        filtered_url, page_size, style, id_field, max_pages, timeout,
        headers=headers, rows_out=rows, page_param=page_param,
        size_param=size_param, offset_param=offset_param,
        limit_param=limit_param, cursor_param=cursor_param)
    if error is not None:
        return Verdict("filter", "INCONCLUSIVE",
                       f"walk failed: {error}",
                       {"url": url, "field": field, "value": str(value)})
    if not rows:
        return Verdict("filter", "INCONCLUSIVE",
                       "no rows returned; nothing to judge the filter on",
                       {"url": url, "field": field, "value": str(value),
                        "pages": info.get("pages")})
    if not any(isinstance(row, dict) and field in row for row in rows):
        return Verdict("filter", "INCONCLUSIVE",
                       f"field {field!r} not present in any row; "
                       "cannot judge the filter (wrong field name?)",
                       {"url": url, "field": field, "value": str(value),
                        "rows_checked": len(rows)})
    expected = str(value).casefold()
    violations = []
    total_violations = 0
    for row in rows:
        bad = None
        if not isinstance(row, dict) or field not in row:
            bad = {"row": _row_id(row, id_field), "field_value": None}
        elif str(row[field]).casefold() != expected:
            bad = {"row": _row_id(row, id_field), "field_value": row[field]}
        if bad is not None:
            total_violations += 1
            if len(violations) < 5:
                violations.append(bad)
    evidence = {"url": url, "field": field, "value": str(value),
                "rows_checked": len(rows),
                "pages": info.get("pages"),
                "violation_count": total_violations,
                "violations": violations}
    if total_violations:
        return Verdict("filter", "FAIL",
                       f"{total_violations} row(s) violate the filter "
                       f"{field}={value} (first few shown)",
                       evidence)
    return Verdict("filter", "PASS",
                   f"all {len(rows)} row(s) satisfy {field}={value}",
                   evidence)
