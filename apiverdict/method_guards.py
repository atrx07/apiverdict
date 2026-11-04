"""Method-guards check — a rejected method must say so properly.

When an endpoint does not support a method, RFC 9110 requires
a 405 response WITH an Allow header listing what IS supported.
Two common failures: the 405 comes without Allow (clients
cannot discover the allowed methods), or Allow lists the very
method just rejected (a contradiction). Rules, per target:

  * status must be 405. Anything else fails: a 2xx means the
    method was accepted though the user declared it rejected;
    any other status is still not the required rejection.
  * Allow header must be present on the 405.
  * Allow must not list the probed method itself.

Targets are user-declared (METHOD:url, like the error-shape
--endpoint flag): the user asserts "this method must be
rejected here". WARNING: probing a method the server DOES
support can execute it — a DELETE probe on a real resource
deletes it. That is why mutating-method probes carry the
standard localhost/--allow-remote guard, and why this check
belongs on test doubles first, like burst. A transport failure
aborts the whole check as INCONCLUSIVE (same as error-shape);
no targets is INCONCLUSIVE.
"""

from __future__ import annotations

from .http_util import mutating_guard, request_json
from .runner import Verdict

_MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def run_method_guards_check(
    targets: list[dict],
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
    allow_remote: bool = False,
) -> Verdict:
    if not targets:
        return Verdict("method-guards", "INCONCLUSIVE",
                       "no targets supplied", {})
    # Suite targets bypass the CLI's endpoint parsing, so a
    # typo'd verb would otherwise be probed and — worse — a
    # server that 405s any unknown verb would hand back a
    # false PASS for a method nobody meant to test. Validate
    # every target up front, before any probe is sent.
    for target in targets:
        method = str(target.get("method", "GET")).upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE",
                          "HEAD", "OPTIONS", "QUERY"):
            return Verdict("method-guards", "INCONCLUSIVE",
                           f"unsupported HTTP method {method!r} in target "
                           f"{target.get('url')!r}", {})
    details: list[dict] = []
    problems: list[str] = []
    for target in targets:
        method = str(target.get("method", "GET")).upper()
        url = target["url"]
        if method in _MUTATING:
            refused = mutating_guard(url, allow_remote)
            if refused:
                return Verdict("method-guards", "INCONCLUSIVE",
                               refused, {"url": url})
        merged = dict(headers or {})
        merged.update(target.get("headers") or {})
        try:
            status, _body, resp_headers = request_json(
                method, url, None, merged, timeout=timeout)
        except Exception as exc:
            return Verdict("method-guards", "INCONCLUSIVE",
                           f"request to {url} failed: {exc}",
                           {"url": url})
        allow = None
        for key, value in (resp_headers or {}).items():
            if key.lower() == "allow":
                allow = str(value)
                break
        label = f"{method} {url}"
        record = {"endpoint": label, "status": status, "allow": allow}
        details.append(record)
        if status != 405:
            if 200 <= status < 300:
                problems.append(f"{label}: method accepted (HTTP "
                                f"{status}) though declared rejected")
            else:
                problems.append(f"{label}: expected 405, got HTTP "
                                f"{status}")
            continue
        if not allow:
            problems.append(f"{label}: 405 without Allow header "
                            f"(RFC 9110 requires it)")
            continue
        allowed = {m.strip().upper() for m in allow.split(",")}
        if method in allowed:
            problems.append(f"{label}: Allow header lists the "
                            f"rejected method itself ({allow!r})")
    evidence = {"targets": len(targets), "details": details}
    if problems:
        return Verdict("method-guards", "FAIL", "; ".join(problems),
                       evidence)
    return Verdict("method-guards", "PASS",
                   f"{len(targets)} method(s) properly rejected with "
                   f"Allow headers", evidence)


__all__ = ["run_method_guards_check"]
