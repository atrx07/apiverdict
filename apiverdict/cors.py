"""CORS preflight check — opt-in, for browser-facing APIs only.

A browser-facing API that mishandles preflight breaks every web
client, silently, at runtime. This check sends a real preflight
(OPTIONS + Origin + Access-Control-Request-Method) per URL and
requires:

  * a 2xx preflight status (204 is the common one),
  * Access-Control-Allow-Origin present (an echo of the request
    Origin, or `*`),
  * Access-Control-Allow-Methods listing the requested method.

Findings name exactly what is absent, so "origin not whitelisted"
(2xx without Allow-Origin) reads differently from "preflight not
handled" (non-2xx). A preflight that errors at transport level is
unjudged for that URL, and nothing judged at all is INCONCLUSIVE.

This check is OPT-IN by selection: run it only against APIs
meant to be called from browsers. A server-to-server API that
deliberately omits CORS will fail it, and that failure is a user
error (wrong check for that API), not a server bug — the same
way running the webhook check against a non-webhook handler is.
Read-only: OPTIONS + no body, so no remote guard is needed.
"""

from __future__ import annotations

from .http_util import request_json
from .runner import Verdict


def _header(headers: dict, name: str) -> str | None:
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return str(value)
    return None


def run_cors_check(
    urls: list[str],
    method: str = "POST",
    origin: str = "https://example.com",
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> Verdict:
    if not urls:
        return Verdict("cors", "INCONCLUSIVE", "no URLs supplied", {})
    method = str(method).upper()
    checked: list[dict] = []
    problems: list[str] = []
    unjudged: list[dict] = []
    for url in urls:
        preflight = dict(headers or {})
        preflight["Origin"] = origin
        preflight["Access-Control-Request-Method"] = method
        try:
            status, _body, resp_headers = request_json(
                "OPTIONS", url, headers=preflight, timeout=timeout)
        except Exception as exc:
            unjudged.append({"url": url,
                             "reason": f"preflight failed: {exc}"})
            continue
        record = {"url": url, "preflight_status": status,
                  "mismatches": []}
        if not (200 <= status < 300):
            record["mismatches"].append(
                f"preflight status {status} (not 2xx)")
        allow_origin = _header(resp_headers, "Access-Control-Allow-Origin")
        if not allow_origin:
            record["mismatches"].append(
                "Access-Control-Allow-Origin missing")
        elif allow_origin not in ("*", origin):
            record["mismatches"].append(
                f"Access-Control-Allow-Origin {allow_origin!r} neither "
                f"echoes {origin!r} nor '*'")
        allow_methods = _header(resp_headers, "Access-Control-Allow-Methods")
        if not allow_methods:
            record["mismatches"].append(
                "Access-Control-Allow-Methods missing")
        else:
            methods = {m.strip().upper()
                       for m in allow_methods.split(",")}
            if method not in methods:
                record["mismatches"].append(
                    f"Access-Control-Allow-Methods {allow_methods!r} does "
                    f"not list {method}")
        checked.append(record)
        if record["mismatches"]:
            problems.append(f"{url}: " + "; ".join(record["mismatches"]))
    evidence = {"method": method, "origin": origin,
                "checked": checked, "unjudged": unjudged}
    if not checked:
        return Verdict("cors", "INCONCLUSIVE",
                       "no URL could be judged "
                       f"({len(unjudged)} unjudged)", evidence)
    if problems:
        return Verdict("cors", "FAIL", "; ".join(problems), evidence)
    return Verdict("cors", "PASS",
                   f"{len(checked)} URL(s): preflight allows {method} "
                   f"from {origin}", evidence)


__all__ = ["run_cors_check"]
