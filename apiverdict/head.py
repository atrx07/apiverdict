"""HEAD/GET consistency check — the same resource, two methods.

A GET that works while HEAD 404s (or lies about Content-Type /
Content-Length) breaks crawlers, health checks, download clients,
and anything that probes before fetching. Rules, per URL:

  * GET non-2xx -> unjudged (no HEAD contract to compare; the
    check continues with the other URLs, like spec-conformance).
  * HEAD 405 -> unjudged ("HEAD unsupported", recorded). Some
    servers deliberately disable HEAD; failing them would punish
    a choice, not a bug. Any other HEAD status disagreeing with
    a 2xx GET (404, 500, ...) is a FAIL.
  * HEAD 2xx: Content-Type and Content-Length, when present on
    BOTH responses, must agree (media type before parameters,
    case-insensitive; length numeric). Absent on either side is
    not judged — a missing header is sloppiness, not a proven
    inconsistency, and chunked GET responses carry no length.
  * Transport failure on either request -> unjudged for that URL.
  * Nothing judged at all -> INCONCLUSIVE, never a vacuous PASS.

Read-only: GET + HEAD only, so no remote guard is needed.
"""

from __future__ import annotations

from .http_util import request_json
from .runner import Verdict


def _base_media_type(headers: dict, name: str) -> str | None:
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return str(value).split(";")[0].strip().lower()
    return None


def _length(headers: dict) -> int | None:
    for key, value in (headers or {}).items():
        if key.lower() == "content-length":
            try:
                return int(value)
            except (ValueError, TypeError):
                return None
    return None


def run_head_consistency_check(
    urls: list[str],
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> Verdict:
    if not urls:
        return Verdict("head-consistency", "INCONCLUSIVE",
                       "no URLs supplied", {})
    checked: list[dict] = []
    problems: list[str] = []
    unjudged: list[dict] = []
    for url in urls:
        try:
            get_status, _body, get_headers = request_json(
                "GET", url, headers=headers, timeout=timeout)
        except Exception as exc:
            unjudged.append({"url": url, "reason": f"GET failed: {exc}"})
            continue
        if not (200 <= get_status < 300):
            unjudged.append({"url": url,
                             "reason": f"GET returned HTTP {get_status}"})
            continue
        try:
            head_status, _b, head_headers = request_json(
                "HEAD", url, headers=headers, timeout=timeout)
        except Exception as exc:
            unjudged.append({"url": url, "reason": f"HEAD failed: {exc}"})
            continue
        record = {"url": url, "get_status": get_status,
                  "head_status": head_status, "mismatches": []}
        if head_status == 405:
            unjudged.append({"url": url, "reason": "HEAD unsupported (405)"})
            continue
        if not (200 <= head_status < 300):
            record["mismatches"].append(
                f"HEAD status {head_status} vs GET {get_status}")
        get_type = _base_media_type(get_headers, "Content-Type")
        head_type = _base_media_type(head_headers, "Content-Type")
        if get_type and head_type and get_type != head_type:
            record["mismatches"].append(
                f"Content-Type HEAD {head_type!r} vs GET {get_type!r}")
        get_len = _length(get_headers)
        head_len = _length(head_headers)
        if get_len is not None and head_len is not None \
                and get_len != head_len:
            record["mismatches"].append(
                f"Content-Length HEAD {head_len} vs GET {get_len}")
        checked.append(record)
        if record["mismatches"]:
            problems.append(f"{url}: " + "; ".join(record["mismatches"]))
    evidence = {"checked": checked, "unjudged": unjudged}
    if not checked:
        return Verdict("head-consistency", "INCONCLUSIVE",
                       "no URL could be judged "
                       f"({len(unjudged)} unjudged)", evidence)
    if problems:
        return Verdict("head-consistency", "FAIL", "; ".join(problems),
                       evidence)
    return Verdict("head-consistency", "PASS",
                   f"{len(checked)} URL(s): HEAD agrees with GET",
                   evidence)


__all__ = ["run_head_consistency_check"]
