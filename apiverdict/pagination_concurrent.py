"""Pagination under a mid-walk write — the concurrent-writes check.

Why this exists, and why it is not just "pagination, but slower":
offset pagination is a *position* (skip N rows), and positions move
when rows are inserted ahead of the reader. A client paging through
a live table with LIMIT/OFFSET will see duplicates and silently
miss rows — a bug that never shows up on seed data, because nobody
writes while the test clicks. Cursor/keyset pagination anchors to
the last row seen, so inserts ahead of the reader do not shift it.

Design (deterministic, not timing-based — this matters for CI):
instead of a background writer thread racing the walk (flaky by
construction: a fast walk may finish before the writer ever fires),
the check uses pagination's generic mid-walk hook:

  1. Baseline walk: walk the list once, with no writes. Record the
     baseline ids (the rows that existed before the test wrote).
  2. Concurrent walk: walk again, and after page N (default 1) the
     hook synchronously POSTs one new row to --write-url, with
     {seq} substituted in the payload (seq=1 for the single write).
     The walk then continues to the end.
  3. Invariants on the concurrent walk:
       * no duplicate ids (an insert ahead of an offset reader
         pushes already-seen rows back into the window);
       * every baseline id appears at least once (an insert must
         not make the walk skip a pre-existing row).
     Total drift is EXPECTED and is not a failure: the total
     legitimately grows by the write. The new row itself is NOT
     required to appear: a correct cursor walk anchored before the
     insert may legitimately finish without it (it was inserted
     ahead of the anchor). Requiring it would fail correct cursors.

A FAIL on offset style is therefore the *expected* demonstration
that the check works, not a server bug to "fix" by changing the
server — the fix is to switch the endpoint to cursor pagination.
The fixture's /api/feed endpoint demonstrates both: offset FAILs,
cursor PASSes, against the same server, same write.

Scope: one write, after one page. Multiple writes, deletes, and
updates mid-walk are not covered — they are the same mechanism,
and one deterministic write already proves the drift.
"""

from __future__ import annotations

import json

from .http_util import request_json
from .pagination import _walk_once
from .runner import Verdict


def _render_payload(payload, seq: int):
    if isinstance(payload, str):
        rendered = payload.replace("{seq}", str(seq))
        try:
            return json.loads(rendered)
        except ValueError:
            return rendered
    if isinstance(payload, dict):
        rendered = json.dumps(payload).replace("{seq}", str(seq))
        return json.loads(rendered)
    return payload


def run_pagination_concurrent_check(
    url: str,
    page_size: int = 10,
    style: str = "auto",
    id_field: str = "id",
    write_url: str | None = None,
    write_method: str = "POST",
    write_payload: object | None = None,
    write_after_page: int = 1,
    max_pages: int = 100,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
    page_param: str = "page",
    size_param: str = "size",
    offset_param: str = "offset",
    limit_param: str = "limit",
    cursor_param: str = "cursor",
    allow_remote: bool = False,
) -> Verdict:
    if not write_url:
        return Verdict("pagination-concurrent", "INCONCLUSIVE",
                       "no --write-url: a concurrent-writes check needs a "
                       "write endpoint to insert one row mid-walk", {})
    # The walk itself is GET-only, but the mid-walk write is a real
    # mutation: it gets the same remote guard as every other writer.
    from .http_util import mutating_guard
    refused = mutating_guard(write_url, allow_remote)
    if refused:
        return Verdict("pagination-concurrent", "INCONCLUSIVE", refused,
                       {"url": url, "write_url": write_url})
    baseline, info, error = _walk_once(url, page_size, style, id_field,
                                       max_pages, timeout, headers,
                                       page_param=page_param, size_param=size_param,
                                       offset_param=offset_param, limit_param=limit_param,
                                       cursor_param=cursor_param)
    if error:
        return Verdict("pagination-concurrent", "INCONCLUSIVE",
                       f"baseline walk could not complete: {error}", info)
    if info.get("limit_ignored"):
        ignored = info["limit_ignored"]
        return Verdict("pagination-concurrent", "FAIL",
                       f"server ignored the requested limit: page "
                       f"{ignored['page']} returned {ignored['rows']} rows "
                       f"for limit {ignored['limit']}", info)
    baseline_set = set(baseline)

    write_state: dict = {"done": False, "status": None, "error": None}

    def _hook(page_index: int, _ids):
        if write_state["done"] or page_index != write_after_page:
            return None
        write_state["done"] = True
        try:
            status, _body, _hdrs = request_json(
                write_method, write_url, _render_payload(write_payload, 1),
                headers, timeout=timeout)
            write_state["status"] = status
            if not (200 <= status < 300):
                return f"write returned HTTP {status}"
        except Exception as exc:
            write_state["error"] = str(exc)
            return str(exc)
        return None

    walked, info2, error2 = _walk_once(
        url, page_size, style, id_field, max_pages, timeout, headers,
        after_page=_hook, allow_total_drift=True,
        page_param=page_param, size_param=size_param,
        offset_param=offset_param, limit_param=limit_param,
        cursor_param=cursor_param)
    if error2:
        return Verdict("pagination-concurrent", "INCONCLUSIVE",
                       f"concurrent walk could not complete: {error2}",
                       {"baseline_rows": len(baseline_set),
                        "write_status": write_state["status"]})
    if info2.get("limit_ignored"):
        ignored = info2["limit_ignored"]
        return Verdict("pagination-concurrent", "FAIL",
                       f"server ignored the requested limit: page "
                       f"{ignored['page']} returned {ignored['rows']} rows "
                       f"for limit {ignored['limit']}", info2)
    if not write_state["done"]:
        return Verdict("pagination-concurrent", "INCONCLUSIVE",
                       "walk finished before the mid-walk write page "
                       f"(write_after_page={write_after_page}, "
                       f"pages walked={info2.get('pages')}); increase "
                       "page count or lower write_after_page", {})
    seen: dict = {}
    duplicates: list = []
    for row_id in walked:
        if row_id in seen and row_id not in duplicates:
            duplicates.append(row_id)
        seen[row_id] = True
    missing = [r for r in baseline_set if r not in seen]
    evidence = {
        "url": url, "style": info2.get("style"),
        "baseline_rows": len(baseline_set),
        "concurrent_rows": len(walked),
        "concurrent_unique": len(seen),
        "pages": info2.get("pages"),
        "write_url": write_url, "write_after_page": write_after_page,
        "write_status": write_state["status"],
        "duplicates": duplicates, "missing_baseline": missing,
    }
    problems = []
    if duplicates:
        problems.append(f"{len(duplicates)} duplicate row(s) under a mid-walk write")
    if missing:
        problems.append(f"{len(missing)} baseline row(s) missed under a mid-walk write")
    if problems:
        return Verdict("pagination-concurrent", "FAIL", "; ".join(problems), evidence)
    return Verdict(
        "pagination-concurrent", "PASS",
        f"stable under a mid-walk write: {len(baseline_set)} baseline row(s) "
        f"all present exactly once across {info2.get('pages')} page(s)",
        evidence)
