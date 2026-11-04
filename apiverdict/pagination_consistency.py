"""Pagination consistency check — same list, two page sizes.

The plain pagination check walks a list once (twice, for order
stability) at one page size. That misses a whole bug class: a list
whose content or order depends on the page size. Causes seen in the
wild: a total computed as pages * page_size, a per-page sort that
re-sorts each page independently, a cursor that encodes an offset
and therefore drifts differently at different sizes, and a
page-size cap silently applied by the server (ask for 50, get 10,
and the walker's offset math walks the wrong rows).

This check walks the same endpoint at two page sizes and requires:
  * the same full id sequence at both sizes (content AND order),
  * the same declared total at both sizes (when a total is given),
  * no duplicates and no gaps at either size (reusing the plain
    walker's invariants, so a failure here is never "just" a
    size artefact — the walk itself must be clean).

A server that caps page size is not failed for the cap alone: the
walk still has to return the same sequence. If it does, the cap is
invisible to a client walking to the end, and this check says so.
If the cap changes the sequence, that is the failure.

This is deterministic and CI-enforceable in a way a chat answer
is not: the same two walks, on every run, against any list
endpoint, with no hand-written assertions about the data.
"""

from __future__ import annotations

from .pagination import _walk_once
from .runner import Verdict


def _analyse(ids: list, total) -> tuple[list, int | None]:
    seen: dict = {}
    duplicates: list = []
    for row_id in ids:
        if row_id in seen and row_id not in duplicates:
            duplicates.append(row_id)
        seen[row_id] = True
    missing = (total - len(seen)) if isinstance(total, int) else None
    return duplicates, missing


def run_pagination_consistency_check(
    url: str,
    page_size_a: int = 10,
    page_size_b: int = 5,
    style: str = "auto",
    id_field: str = "id",
    max_pages: int = 100,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
    page_param: str = "page",
    size_param: str = "size",
    offset_param: str = "offset",
    limit_param: str = "limit",
    cursor_param: str = "cursor",
) -> Verdict:
    if page_size_a == page_size_b:
        return Verdict("pagination-consistency", "INCONCLUSIVE",
                       "page sizes must differ (that is the check)",
                       {"page_size_a": page_size_a,
                        "page_size_b": page_size_b})
    if page_size_a < 1 or page_size_b < 1:
        return Verdict("pagination-consistency", "INCONCLUSIVE",
                       "page sizes must be positive", {})
    walks = {}
    for label, size in (("a", page_size_a), ("b", page_size_b)):
        ids, info, error = _walk_once(url, size, style, id_field,
                                      max_pages, timeout, headers,
                                      page_param=page_param, size_param=size_param,
                                      offset_param=offset_param, limit_param=limit_param,
                                      cursor_param=cursor_param)
        if error:
            return Verdict("pagination-consistency", "INCONCLUSIVE",
                           f"walk at page size {size} could not complete: "
                           f"{error}", {"page_size": size, **info})
        walks[label] = (ids, info)
    ids_a, info_a = walks["a"]
    ids_b, info_b = walks["b"]
    dup_a, miss_a = _analyse(ids_a, info_a.get("total"))
    dup_b, miss_b = _analyse(ids_b, info_b.get("total"))
    evidence = {
        "url": url,
        "page_size_a": page_size_a, "page_size_b": page_size_b,
        "rows_a": len(ids_a), "rows_b": len(ids_b),
        "unique_a": len(set(ids_a)), "unique_b": len(set(ids_b)),
        "duplicates_a": dup_a, "duplicates_b": dup_b,
        "total_a": info_a.get("total"), "total_b": info_b.get("total"),
        "missing_a": miss_a, "missing_b": miss_b,
        "same_sequence": ids_a == ids_b,
    }
    problems = []
    for label, info in (("a", info_a), ("b", info_b)):
        ignored = info.get("limit_ignored")
        if ignored:
            problems.append(f"page size {page_size_a if label == 'a' else page_size_b}: "
                            f"server ignored the requested limit (page "
                            f"{ignored['page']} returned {ignored['rows']} rows "
                            f"for limit {ignored['limit']})")
    if dup_a:
        problems.append(f"page size {page_size_a}: {len(dup_a)} duplicate row(s)")
    if dup_b:
        problems.append(f"page size {page_size_b}: {len(dup_b)} duplicate row(s)")
    if miss_a is not None and miss_a > 0:
        problems.append(f"page size {page_size_a}: {miss_a} missing row(s) "
                        f"vs total {info_a.get('total')}")
    if miss_b is not None and miss_b > 0:
        problems.append(f"page size {page_size_b}: {miss_b} missing row(s) "
                        f"vs total {info_b.get('total')}")
    for miss, info, ids, size in (
            (miss_a, info_a, ids_a, page_size_a),
            (miss_b, info_b, ids_b, page_size_b)):
        if miss is not None and miss < 0:
            problems.append(f"page size {size}: walked "
                            f"{len(set(ids))} unique row(s) "
                            f"but total declares {info.get('total')} "
                            f"(total stale or under-reported)")
    if (isinstance(info_a.get("total"), int)
            and isinstance(info_b.get("total"), int)
            and info_a["total"] != info_b["total"]):
        problems.append(f"declared total changes with page size: "
                        f"{info_a['total']} at size {page_size_a} vs "
                        f"{info_b['total']} at size {page_size_b}")
    if ids_a != ids_b:
        problems.append("full row sequence differs between page sizes "
                        f"({len(ids_a)} rows at size {page_size_a}, "
                        f"{len(ids_b)} rows at size {page_size_b})")
    if problems:
        return Verdict("pagination-consistency", "FAIL", "; ".join(problems),
                       evidence)
    return Verdict("pagination-consistency", "PASS",
                   f"same {len(ids_a)}-row sequence at page sizes "
                   f"{page_size_a} and {page_size_b}, totals agree",
                   evidence)
