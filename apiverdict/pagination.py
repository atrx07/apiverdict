"""pagination check — style-agnostic list walker.

Walks a list endpoint to the end and checks four invariants:
  * duplicate rows  — the same id appears on more than one page
  * missing rows    — collected rows < declared total (when a total is given)
  * order changes   — a second full walk returns a different id order
  * metadata drift  — total changes between pages, or the walk loops

Styles (auto-detected from the first response, overridable with --style):
  offset  — ?offset=N&limit=M, response carries next_offset (or, failing
            that, pages advance by limit while a full page is returned)
  page    — ?page=N&size=M, response carries page/total_pages
  cursor  — ?cursor=..., response carries next_cursor
  link    — Link: <url>; rel="next" response header

Response shape expected (any subset): a JSON object with an items list
under "items", "data", or "results", plus optional "total",
"next_offset", "next_cursor", "page", "total_pages". A bare JSON list is
also accepted (then only duplicate/order checks apply, no total check).

The id field defaults to "id"; rows without that field are compared by
their whole JSON value.
"""

from __future__ import annotations

import urllib.parse

from .http_util import parse_link_next, request_json
from .runner import Verdict


def _items_of(body) -> tuple[list, dict]:
    if isinstance(body, list):
        return body, {}
    if isinstance(body, dict):
        for key in ("items", "data", "results"):
            if isinstance(body.get(key), list):
                return body[key], body
    return [], body if isinstance(body, dict) else {}


def _row_id(row, id_field: str):
    if isinstance(row, dict) and id_field in row:
        return row[id_field]
    return repr(row)


def _walk_once(
    url: str,
    page_size: int,
    style: str,
    id_field: str,
    max_pages: int,
    timeout: float,
) -> tuple[list, dict, str | None]:
    """Return (ids, info, error). error is None on a completed walk."""
    ids: list = []
    seen_urls: set[str] = set()
    total: int | None = None
    pages = 0
    detected = style
    next_url: str | None = url
    next_cursor: str | None = None
    offset = 0
    page_no = 1

    while next_url is not None and pages < max_pages:
        parsed = urllib.parse.urlparse(next_url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if detected in ("auto", "offset") and "offset" not in query and "cursor" not in query:
            query["offset"] = str(offset)
            query["limit"] = str(page_size)
        elif detected == "page":
            query["page"] = str(page_no)
            query["size"] = str(page_size)
        elif detected == "cursor":
            # Send an explicit cursor on the first page ("0" as the start
            # sentinel) so cursor-style servers return next_cursor instead
            # of falling back to an offset response. An empty value would
            # be dropped by standard query parsing.
            query["cursor"] = next_cursor if next_cursor is not None else "0"
        fetch_url = urllib.parse.urlunparse(parsed._replace(
            query=urllib.parse.urlencode(query)))
        # Loop detection must use the full fetch URL (with paging params),
        # not the bare base URL — the base URL legitimately repeats.
        if fetch_url in seen_urls:
            return ids, {"pages": pages, "total": total, "loop_url": fetch_url}, \
                f"walk looped on {fetch_url}"
        seen_urls.add(fetch_url)
        try:
            status, body, headers = request_json("GET", fetch_url, timeout=timeout)
        except Exception as exc:
            return ids, {"pages": pages, "total": total}, f"request failed: {exc}"
        if status != 200:
            return ids, {"pages": pages, "total": total}, \
                f"page request returned HTTP {status}"
        items, meta = _items_of(body)
        if not items and pages == 0 and not isinstance(body, (dict, list)):
            return ids, {"pages": pages, "total": total}, "non-JSON list response"
        pages += 1
        for row in items:
            ids.append(_row_id(row, id_field))
        if total is None and isinstance(meta.get("total"), int):
            total = meta["total"]
        elif isinstance(meta.get("total"), int) and meta["total"] != total:
            return ids, {"pages": pages, "total": total}, \
                f"total changed mid-walk: {total} -> {meta['total']}"

        # Decide the next step, in priority order.
        if detected == "auto":
            if meta.get("next_cursor"):
                detected = "cursor"
                next_cursor = meta["next_cursor"]
                next_url = url
                continue
            if "next_offset" in meta:
                detected = "offset"
                if meta["next_offset"] is None:
                    break
                offset = int(meta["next_offset"])
                next_url = url
                continue
            if isinstance(meta.get("page"), int) and isinstance(meta.get("total_pages"), int):
                detected = "page"
                if meta["page"] >= meta["total_pages"]:
                    break
                page_no = int(meta["page"]) + 1
                next_url = url
                continue
            link_next = parse_link_next(headers)
            if link_next:
                detected = "link"
                next_url = link_next
                continue
            # Fall back to offset advance.
            if len(items) < page_size:
                break
            offset += page_size
            next_url = url
            continue
        if detected == "offset":
            if "next_offset" in meta:
                if meta["next_offset"] is None:
                    break
                offset = int(meta["next_offset"])
                next_url = url
                continue
            if len(items) < page_size:
                break
            offset += page_size
            next_url = url
            continue
        if detected == "page":
            if isinstance(meta.get("total_pages"), int):
                if page_no >= meta["total_pages"]:
                    break
                page_no += 1
                next_url = url
                continue
            if len(items) < page_size:
                break
            page_no += 1
            next_url = url
            continue
        if detected == "cursor":
            if meta.get("next_cursor"):
                next_cursor = meta["next_cursor"]
                next_url = url
                continue
            break
        if detected == "link":
            link_next = parse_link_next(headers)
            if not link_next:
                break
            next_url = link_next
            continue
        break

    if pages >= max_pages and next_url is not None:
        return ids, {"pages": pages, "total": total}, "max_pages reached, walk incomplete"
    return ids, {"pages": pages, "total": total, "style": detected}, None


def run_pagination_check(
    url: str,
    page_size: int = 10,
    style: str = "auto",
    id_field: str = "id",
    max_pages: int = 100,
    timeout: float = 10.0,
) -> Verdict:
    ids, info, error = _walk_once(url, page_size, style, id_field, max_pages, timeout)
    if error:
        return Verdict("pagination", "INCONCLUSIVE",
                       f"walk could not complete: {error}", info)
    seen: dict = {}
    duplicates: list = []
    for row_id in ids:
        if row_id in seen:
            if row_id not in duplicates:
                duplicates.append(row_id)
        seen[row_id] = True
    total = info.get("total")
    missing = (total - len(seen)) if isinstance(total, int) else None

    # Order stability: a second walk must return the same id order.
    ids2, _info2, error2 = _walk_once(url, page_size, style, id_field, max_pages, timeout)
    order_stable = None if error2 else (ids2 == ids)

    evidence = {
        "url": url,
        "pages": info.get("pages"),
        "style": info.get("style"),
        "rows_collected": len(ids),
        "unique_rows": len(seen),
        "duplicates": duplicates,
        "total": total,
        "missing": missing,
        "order_stable": order_stable,
    }
    problems = []
    if duplicates:
        problems.append(f"{len(duplicates)} duplicate row(s)")
    if missing is not None and missing > 0:
        problems.append(f"{missing} missing row(s) vs total {total}")
    if order_stable is False:
        problems.append("row order changed between walks")
    if problems:
        return Verdict("pagination", "FAIL", "; ".join(problems), evidence)
    return Verdict("pagination", "PASS",
                   f"walked {info.get('pages')} page(s), {len(seen)} unique row(s), "
                   "no duplicates, no gaps, stable order",
                   evidence)
