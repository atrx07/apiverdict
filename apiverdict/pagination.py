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
  cursor  — ?cursor=..., response carries next_cursor (aliases
            next_page_token and cursor accepted; a body
            "next"/"next_url" URL is followed verbatim; a nested
            body "links.next" is not followed)
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
    if isinstance(row, dict) and "." in id_field:
        # Dotted path ("item.id") for rows that nest the id, e.g.
        # {"item": {"id": 7}}. A missing intermediate falls through
        # to repr(row), the same fallback as a missing top-level id.
        current = row
        for part in id_field.split("."):
            if not isinstance(current, dict) or part not in current:
                return repr(row)
            current = current[part]
        return current
    return repr(row)


_CURSOR_KEYS = ("next_cursor", "next_page_token", "cursor")
# Deliberately NOT "next_page": that name is also used for a page
# NUMBER, and treating a number as a cursor token would misclassify
# page-style servers. A "next" token that is not URL-like is likewise
# ambiguous, so "next" is only honoured as a URL (see _next_url_value).


def _cursor_token(meta: dict) -> str | None:
    """First non-empty cursor token under any common field name.

    Real APIs do not agree on the field name: next_cursor,
    next_page_token, cursor, and next_page are all in use for the
    same concept (an opaque token for the next page). Integer
    tokens are stringified — a token is opaque, its type is not
    part of the contract.
    """
    for key in _CURSOR_KEYS:
        value = meta.get(key)
        if value is not None and value != "":
            return str(value)
    return None


def _next_url_value(meta: dict, fetch_url: str) -> str | None:
    """A next-page URL carried in the body ("next" or "next_url"),
    resolved against the page just fetched. URL-like values only —
    a short token under "next" is a cursor, handled by
    _cursor_token, not a URL."""
    for key in ("next_url", "next"):
        value = meta.get(key)
        if isinstance(value, str) and value and value.startswith(
                ("http://", "https://", "/")):
            return urllib.parse.urljoin(fetch_url, value)
    return None


def _json_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _walk_once(
    url: str,
    page_size: int,
    style: str,
    id_field: str,
    max_pages: int,
    timeout: float,
    headers: dict[str, str] | None = None,
    after_page=None,
    allow_total_drift: bool = False,
    page_param: str = "page",
    size_param: str = "size",
    offset_param: str = "offset",
    limit_param: str = "limit",
    cursor_param: str = "cursor",
    rows_out: list | None = None,
) -> tuple[list, dict, str | None]:
    """after_page, if given, is called as after_page(page_index_1based,
    ids_so_far) after each page is collected and before the next page
    is fetched. It is the generic mid-walk write hook used by the
    pagination-concurrent check: a callback that raises, or returns a
    string error, aborts the walk as INCONCLUSIVE (a failed write is
    not evidence about pagination)."""
    """Return (ids, info, error). error is None on a completed walk."""
    if page_size < 1:
        return [], {"pages": 0, "total": None}, "page size must be >= 1"
    if max_pages < 1:
        # Zero pages would otherwise PASS on an empty walk (no
        # duplicates, no missing, order trivially stable).
        return [], {"pages": 0, "total": None}, "max pages must be >= 1"
    if style not in ("auto", "offset", "page", "cursor", "link"):
        # A typo'd style used to silently fetch one page and judge
        # it (no branch matched, so the walk broke after page 1).
        # Refuse it as INCONCLUSIVE instead of a verdict on a
        # walk that never happened.
        return [], {"pages": 0, "total": None}, \
            f"unknown pagination style {style!r}"
    ids: list = []
    field_types: dict[str, set] = {}
    empty_pages = 0
    seen_urls: set[str] = set()
    total: int | None = None
    total_pages_seen: int | None = None
    pages = 0
    detected = style
    next_url: str | None = url
    next_cursor: str | None = None
    limit_ignored = None
    direct = False  # next fetch uses next_url verbatim (body/Link URL)
    body_url = False  # a body next-URL was followed: the server
                      # speaks URLs, not offsets — never fall back
                      # to offset advance after one (that invents a
                      # URL the server already served and loops)
    offset = 0
    page_no = 1

    while next_url is not None and pages < max_pages:
        parsed = urllib.parse.urlparse(next_url)
        sent_limit = False
        if direct:
            # A body/Link next URL is followed verbatim: injecting
            # paging params into it would corrupt its own cursor.
            fetch_url = next_url
            direct = False
        else:
            query = dict(urllib.parse.parse_qsl(parsed.query))
            if detected in ("auto", "offset") and offset_param not in query and cursor_param not in query:
                query[offset_param] = str(offset)
                query[limit_param] = str(page_size)
                sent_limit = True
            elif detected == "page":
                query[page_param] = str(page_no)
                query[size_param] = str(page_size)
                sent_limit = True
            elif detected == "cursor":
                # Send an explicit cursor on the first page ("0" as the start
                # sentinel) so cursor-style servers return next_cursor instead
                # of falling back to an offset response. An empty value would
                # be dropped by standard query parsing.
                query[cursor_param] = next_cursor if next_cursor is not None else "0"
            fetch_url = urllib.parse.urlunparse(parsed._replace(
                query=urllib.parse.urlencode(query)))
        # Loop detection must use the full fetch URL (with paging params),
        # not the bare base URL — the base URL legitimately repeats.
        if fetch_url in seen_urls:
            return ids, {"pages": pages, "total": total, "loop_url": fetch_url}, \
                f"walk looped on {fetch_url}"
        seen_urls.add(fetch_url)
        try:
            status, body, resp_headers = request_json("GET", fetch_url, headers=headers, timeout=timeout)
            headers_out = resp_headers
        except Exception as exc:
            return ids, {"pages": pages, "total": total}, f"request failed: {exc}"
        if status != 200:
            return ids, {"pages": pages, "total": total}, \
                f"page request returned HTTP {status}"
        items, meta = _items_of(body)
        if isinstance(headers_out, dict) and "total" not in meta:
            # X-Total-Count (JSONPlaceholder and friends): the total
            # lives in a header, not the body. Case-insensitive
            # lookup; a non-integer value is ignored, not fatal.
            for header_name, header_value in headers_out.items():
                if header_name.lower() == "x-total-count":
                    try:
                        meta = {**meta, "total": int(header_value)}
                    except (ValueError, TypeError):
                        pass
                    break
        if not items and pages == 0 and not isinstance(body, (dict, list)):
            return ids, {"pages": pages, "total": total}, "non-JSON list response"
        pages += 1
        if not items:
            empty_pages += 1
        if sent_limit and limit_ignored is None and len(items) > page_size:
            # The client asked for page_size rows and got more: the
            # server ignored the limit. Without this, a server that
            # dumps the whole list on page 1 "passes" a pagination
            # check without ever paging (JSONPlaceholder without
            # _page/_limit behaves exactly this way).
            limit_ignored = {"page": pages, "rows": len(items),
                             "limit": page_size}
        if rows_out is not None:
            rows_out.extend(items)
        for row in items:
            ids.append(_row_id(row, id_field))
            if isinstance(row, dict):
                # Row-shape stability: a field that changes JSON
                # type across pages (int on page 1, string on page
                # 2) breaks typed clients. Nulls are exempt: a
                # nullable field is legitimately int on some rows
                # and null on others.
                for key, value in row.items():
                    if value is not None:
                        field_types.setdefault(key, set()).add(
                            _json_type(value))
        if limit_ignored is not None:
            # Verdict already determined (FAIL): continuing to
            # max_pages would burn up to 100 requests against a
            # real API for no new information (observed live:
            # a JSONPlaceholder auto-style walk ran 100 pages
            # after the flag was set on page 1).
            break
        # Malformed paging metadata must abort the walk as
        # INCONCLUSIVE, never crash the check: int("soon") on a
        # string next_offset used to raise out of _walk_once and
        # take the whole CLI down with a traceback. Cycle 4
        # made ANY non-int total/next_offset inconclusive;
        # cycle 210 narrows that: a string of plain digits
        # ("30") means exactly 30 — some APIs stringify
        # counts (big-int safety) — so it is coerced and
        # judged, while an unparseable string ("soon") still
        # aborts. Coercion only turns INCONCLUSIVE into a
        # correct verdict, never into a wrong one.
        def _is_int(v):
            return isinstance(v, int) and not isinstance(v, bool)

        def _coerce_int(v):
            if _is_int(v):
                return v
            # Plain ASCII digits only: isdigit() alone is
            # unsafe ("²".isdigit() is True, int("²") raises),
            # and a signed/whitespace-padded count is not a
            # plain count. Anything else stays unjudgeable.
            if isinstance(v, str) and v.isascii() and v.isdigit():
                return int(v)
            return None
        for key in ("total", "next_offset"):
            if key in meta and meta[key] is not None and not _is_int(meta[key]):
                coerced = _coerce_int(meta[key])
                if coerced is None:
                    return ids, {"pages": pages, "total": total}, \
                        f"{key} is not an integer: {meta[key]!r}"
                meta = {**meta, key: coerced}
        if meta.get("next_cursor") is not None and not isinstance(meta["next_cursor"], str):
            return ids, {"pages": pages, "total": total}, \
                f"next_cursor is not a string: {meta['next_cursor']!r}"
        if after_page is not None:
            try:
                hook_error = after_page(pages, list(ids))
            except Exception as exc:
                return ids, {"pages": pages, "total": total}, \
                    f"mid-walk write hook failed: {exc}"
            if isinstance(hook_error, str) and hook_error:
                return ids, {"pages": pages, "total": total}, \
                    f"mid-walk write hook failed: {hook_error}"
        if total is None and isinstance(meta.get("total"), int):
            total = meta["total"]
        elif isinstance(meta.get("total"), int) and meta["total"] != total:
            if allow_total_drift:
                # Concurrent-writes walk: the total legitimately grows
                # (or shrinks) when rows are written mid-walk. Record
                # the latest total; drift itself is judged by the
                # duplicate/missing invariants, not by this error.
                total = meta["total"]
            else:
                return ids, {"pages": pages, "total": total}, \
                    f"total changed mid-walk: {total} -> {meta['total']}"

        # total_pages drift, symmetric with total drift: a
        # static list's page count must not change mid-walk.
        # (The concurrent walk passes allow_total_drift and
        # updates silently, like total.)
        if isinstance(meta.get("total_pages"), int):
            if total_pages_seen is None:
                total_pages_seen = meta["total_pages"]
            elif meta["total_pages"] != total_pages_seen:
                if allow_total_drift:
                    total_pages_seen = meta["total_pages"]
                else:
                    return ids, {"pages": pages, "total": total}, \
                        f"total_pages changed mid-walk: " \
                        f"{total_pages_seen} -> {meta['total_pages']}"

        # Decide the next step, in priority order.
        if detected == "auto":
            body_next = _next_url_value(meta, fetch_url)
            if body_next:
                next_url = body_next
                direct = True
                body_url = True
                continue
            token = _cursor_token(meta)
            if token:
                detected = "cursor"
                next_cursor = token
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
            link_next = parse_link_next(headers_out)
            if link_next:
                detected = "link"
                # Link URLs are often relative ("/api/items?page=2");
                # resolve against the page just fetched, not the
                # original base — a relative link on page 2 must
                # resolve from page 2's URL, and urljoin handles
                # both relative and absolute links correctly.
                next_url = urllib.parse.urljoin(fetch_url, link_next)
                continue
            # Fall back to offset advance — unless a body URL was
            # followed earlier: then no next marker means the list
            # ended, and offset-advancing would re-fetch a page
            # already served (loop detection used to catch it as
            # INCONCLUSIVE instead of a clean PASS).
            if body_url:
                break
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
            body_next = _next_url_value(meta, fetch_url)
            if body_next:
                next_url = body_next
                direct = True
                body_url = True
                continue
            token = _cursor_token(meta)
            if token:
                next_cursor = token
                next_url = url
                continue
            break
        if detected == "link":
            link_next = parse_link_next(headers_out)
            if not link_next:
                break
            next_url = urllib.parse.urljoin(fetch_url, link_next)
            continue
        break

    if pages >= max_pages and next_url is not None:
        info = {"pages": pages, "total": total, "empty_pages": empty_pages}
        if limit_ignored is not None:
            info["limit_ignored"] = limit_ignored
        return ids, info, "max_pages reached, walk incomplete"
    info = {"pages": pages, "total": total, "style": detected,
            "empty_pages": empty_pages}
    if limit_ignored is not None:
        info["limit_ignored"] = limit_ignored
    retyped = {k: sorted(v) for k, v in field_types.items() if len(v) > 1}
    if retyped:
        info["retyped_fields"] = retyped
    return ids, info, None


def run_pagination_check(
    url: str,
    page_size: int = 10,
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
    ids, info, error = _walk_once(url, page_size, style, id_field, max_pages, timeout, headers,
                                  page_param=page_param, size_param=size_param,
                                  offset_param=offset_param, limit_param=limit_param,
                                  cursor_param=cursor_param)
    if info.get("limit_ignored"):
        ignored = info["limit_ignored"]
        return Verdict("pagination", "FAIL",
                       f"server ignored the requested limit: page "
                       f"{ignored['page']} returned {ignored['rows']} rows "
                       f"for limit {ignored['limit']}", info)
    if error:
        return Verdict("pagination", "INCONCLUSIVE",
                       f"walk could not complete: {error}", info)
    if info.get("retyped_fields"):
        fields = ", ".join(f"{k} ({'/'.join(v)})"
                            for k, v in info["retyped_fields"].items())
        return Verdict("pagination", "FAIL",
                       f"row field(s) changed type across pages: {fields}",
                       info)
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
    # It must use the SAME param names as the first walk: without
    # them, a _page/_limit API's second walk ran with default
    # page/size names, errored, and reported order_stable None
    # instead of a real comparison (observed live, cycle 51).
    ids2, _info2, error2 = _walk_once(url, page_size, style, id_field, max_pages, timeout, headers,
                                      page_param=page_param, size_param=size_param,
                                      offset_param=offset_param, limit_param=limit_param,
                                      cursor_param=cursor_param)
    order_stable = None if error2 else (ids2 == ids)

    evidence = {
        "url": url,
        "pages": info.get("pages"),
        "style": info.get("style"),
        "empty_pages": info.get("empty_pages", 0),
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
    if missing is not None and missing < 0:
        # More unique rows than the declared total: the total is
        # stale or under-reported. A client paging by total stops
        # early and never sees the extra rows — the mirror image
        # of missing rows, and previously it passed silently
        # because only missing > 0 was checked.
        problems.append(f"walked {len(seen)} unique row(s) but total "
                        f"declares {total} (total stale or under-reported)")
    if order_stable is False:
        problems.append("row order changed between walks")
    if info.get("empty_pages", 0) > 1:
        # Two or more empty pages inside the declared range:
        # the server's page count overstates the list. One
        # empty page is tolerated as a boundary artefact
        # (filtered totals, off-by-one page counts); without
        # a declared total_pages the walk stops at the first
        # empty page anyway, so this only bites when the
        # server itself declared the overstated range.
        problems.append(f"{info['empty_pages']} empty page(s) served "
                        "within the declared page range (page count "
                        "overstated)")
    if problems:
        return Verdict("pagination", "FAIL", "; ".join(problems), evidence)
    return Verdict("pagination", "PASS",
                   f"walked {info.get('pages')} page(s), {len(seen)} unique row(s), "
                   "no duplicates, no gaps, stable order",
                   evidence)
