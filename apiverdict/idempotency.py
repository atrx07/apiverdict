"""idempotency-key replay check — sequential replay, not a burst.

Burst tests simultaneity. This tests the other half of the same bug
class: send the same request twice, sequentially, with the same
Idempotency-Key header, and compare final state.

  * Delta 1 across both sends => PASS (the key was honoured).
  * Delta 2 => FAIL (the key was ignored; a client retry double-charges).
  * No verify command/URL => INCONCLUSIVE, same rule as burst: a replay
    without a state check would be green on a skipped check.
  * Send statuses are judged as well as the delta: a server can
    answer 200 to both sends and still have written twice, so
    the delta decides duplication — but a first send that
    errors means nothing was created to replay (INCONCLUSIVE),
    and a replay that errors instead of returning the stored
    response is a FAIL even when the delta happens to match.

This is deliberately sequential: a burst that passes proves the lock,
a replay that passes proves the key store. Either alone is half proof.
"""

from __future__ import annotations

import json

from .burst import _read_state
from .http_util import mutating_guard, request_json
from .runner import Verdict


def run_idempotency_check(
    url: str,
    method: str = "POST",
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    idempotency_key: str = "apiverdict-replay-1",
    idempotency_header: str = "Idempotency-Key",
    verify_cmd: str | None = None,
    verify_url: str | None = None,
    expect_delta: int = 1,
    timeout: float = 10.0,
    allow_remote: bool = False,
    verify_headers: dict[str, str] | None = None,
    compare_responses: bool = False,
    ignore_fields: list[str] | None = None,
) -> Verdict:
    refused = mutating_guard(url, allow_remote)
    if refused:
        return Verdict("idempotency", "INCONCLUSIVE", refused, {"url": url})
    if expect_delta < 0:
        return Verdict("idempotency", "INCONCLUSIVE",
                       "expected delta must be >= 0", {"url": url})
    # Replaying a GET is meaningless — GETs are idempotent by
    # definition, so a delta 0 would misread as "key ignored".
    method = str(method).upper()
    if method not in ("POST", "PUT", "PATCH", "DELETE"):
        return Verdict("idempotency", "INCONCLUSIVE",
                       f"idempotency method must mutate (POST/PUT/PATCH/DELETE), "
                       f"got {method!r}", {"url": url})
    if verify_cmd is None and verify_url is None:
        return Verdict("idempotency", "INCONCLUSIVE",
                       "no verify command or verify URL: final state "
                       "cannot be checked", {})
    headers = dict(headers or {})
    headers[idempotency_header] = idempotency_key
    try:
        before = _read_state(verify_cmd, verify_url, 1, verify_headers=verify_headers if verify_headers is not None else headers)
    except Exception as exc:
        return Verdict("idempotency", "INCONCLUSIVE",
                       f"could not read state before replay: {exc}", {})
    statuses = []
    bodies = []
    for _ in range(2):
        try:
            status, body, _hdrs = request_json(
                method, url, payload, headers, timeout=timeout)
        except Exception as exc:
            return Verdict("idempotency", "INCONCLUSIVE",
                           f"replay request failed: {exc}", {})
        statuses.append(status)
        bodies.append(body)
    if not (200 <= statuses[0] < 300):
        # Nothing was created, so there is no replay to judge:
        # the old code read state anyway and reported delta 0 as
        # "key ignored", a wrong diagnosis for a failed send.
        return Verdict("idempotency", "INCONCLUSIVE",
                       f"first send failed (HTTP {statuses[0]}): no "
                       f"resource was created to replay", {})
    try:
        after = _read_state(verify_cmd, verify_url, 1, verify_headers=verify_headers if verify_headers is not None else headers)
    except Exception as exc:
        return Verdict("idempotency", "INCONCLUSIVE",
                       f"could not read state after replay: {exc}", {})
    delta = after - before
    evidence = {"url": url, "key_header": idempotency_header,
                "before": before, "after": after, "delta": delta,
                "expected_delta": expect_delta, "statuses": statuses,
                "responses_match": None}
    problems = []
    if compare_responses and len(statuses) == 2 and all(
            200 <= st < 300 for st in statuses):
        # A stored replay should return the ORIGINAL response, not
        # a freshly computed one: a replay that mints a new id or
        # timestamp while the delta stays 1 is still lying to the
        # client. Off by default (delta-only is the core promise);
        # opt in with --compare-responses. Volatile top-level
        # fields (timestamps, request ids) are stripped via
        # ignore_fields so they cannot false-FAIL. Bodies are
        # never copied into evidence whole — only the differing
        # top-level keys are named.
        ignored = set(ignore_fields or [])
        def _stripped(body):
            if isinstance(body, dict):
                return {k: v for k, v in body.items() if k not in ignored}
            return body
        first, second = _stripped(bodies[0]), _stripped(bodies[1])
        # A stored replay returns the ORIGINAL response, status
        # included: 201-then-200 with the same body is still a
        # freshly computed response, not the stored one.
        status_same = statuses[0] == statuses[1]
        if not status_same:
            problems.append(f"replay status {statuses[1]} differs "
                            f"from original status {statuses[0]}")
        if first == second and status_same:
            evidence["responses_match"] = True
        else:
            evidence["responses_match"] = False
            if isinstance(first, dict) and isinstance(second, dict):
                diff_keys = sorted(
                    k for k in set(first) | set(second)
                    if first.get(k) != second.get(k))
                detail = f"differing fields: {', '.join(diff_keys)}"
            else:
                detail = "response bodies differ"
            problems.append(f"replay response differs from the original "
                            f"response ({detail})")
    if not (200 <= statuses[1] < 300):
        problems.append(f"replay errored (HTTP {statuses[1]}) instead of "
                        f"returning the original response")
    if delta != expect_delta:
        problems.append(f"idempotency key ignored: delta {delta}, expected "
                        f"{expect_delta} across 2 sends with the same key "
                        f"(statuses {statuses})")
    if problems:
        return Verdict("idempotency", "FAIL", "; ".join(problems), evidence)
    return Verdict("idempotency", "PASS",
                   f"idempotency key honoured: delta {expect_delta} across "
                   f"2 sends with the same key (statuses {statuses})",
                   evidence)


def run_idempotency_variants_check(
    url: str,
    method: str = "POST",
    payload: object | None = None,
    alt_payload: object | None = None,
    headers: dict[str, str] | None = None,
    idempotency_key: str = "apiverdict-replay-1",
    idempotency_header: str = "Idempotency-Key",
    verify_cmd: str | None = None,
    verify_url: str | None = None,
    timeout: float = 10.0,
    allow_remote: bool = False,
    verify_headers: dict[str, str] | None = None,
) -> Verdict:
    """Idempotency-key variants, sequential:

      1. same key + same payload, twice  -> delta must be 1
         (the plain replay; same as run_idempotency_check).
      2. same key + a DIFFERENT payload  -> delta must be 0 more.
         Reusing a key must never create a second side effect, even
         when the client retries with changed parameters. Two
         behaviours are acceptable here and both pass: returning the
         original response (key wins), or a 4xx key-conflict error.
         Creating a second resource is the FAIL — that is how a
         retried payment with an edited amount double-charges.

    A different key + same payload is deliberately NOT asserted:
    some APIs deduplicate by payload (booking slot) and some do not,
    so neither delta 1 nor delta 2 is universal there.
    """
    from .http_util import mutating_guard

    refused = mutating_guard(url, allow_remote)
    if refused:
        return Verdict("idempotency-variants", "INCONCLUSIVE", refused, {"url": url})
    method = str(method).upper()
    if method not in ("POST", "PUT", "PATCH", "DELETE"):
        return Verdict("idempotency-variants", "INCONCLUSIVE",
                       f"idempotency method must mutate (POST/PUT/PATCH/DELETE), "
                       f"got {method!r}", {"url": url})
    if verify_cmd is None and verify_url is None:
        return Verdict("idempotency-variants", "INCONCLUSIVE",
                       "no verify command or verify URL: final state "
                       "cannot be checked", {})
    headers = dict(headers or {})
    headers[idempotency_header] = idempotency_key

    def _state():
        return _read_state(verify_cmd, verify_url, 1, verify_headers=verify_headers if verify_headers is not None else headers)

    def _send(body):
        try:
            status, _b, _h = request_json(method, url, body, headers, timeout=timeout)
            return status, None
        except Exception as exc:
            return None, str(exc)

    try:
        before = _state()
    except Exception as exc:
        return Verdict("idempotency-variants", "INCONCLUSIVE",
                       f"could not read state before replay: {exc}", {})
    statuses = []
    for body in (payload, payload):
        status, err = _send(body)
        if err is not None:
            return Verdict("idempotency-variants", "INCONCLUSIVE",
                           f"replay request failed: {err}", {})
        statuses.append(status)
    if not (200 <= statuses[0] < 300):
        return Verdict("idempotency-variants", "INCONCLUSIVE",
                       f"first send failed (HTTP {statuses[0]}): no "
                       f"resource was created to replay", {})
    try:
        mid = _state()
    except Exception as exc:
        return Verdict("idempotency-variants", "INCONCLUSIVE",
                       f"could not read state mid-variant: {exc}", {})
    alt_status, err = _send(alt_payload if alt_payload is not None else payload)
    if err is not None:
        return Verdict("idempotency-variants", "INCONCLUSIVE",
                       f"alt-payload request failed: {err}", {})
    statuses.append(alt_status)
    try:
        after = _state()
    except Exception as exc:
        return Verdict("idempotency-variants", "INCONCLUSIVE",
                       f"could not read state after variant: {exc}", {})
    evidence = {"url": url, "key_header": idempotency_header,
                "before": before, "mid": mid, "after": after,
                "replay_delta": mid - before, "alt_delta": after - mid,
                "statuses": statuses, "alt_status": alt_status}
    problems = []
    if not (200 <= statuses[1] < 300):
        problems.append(f"replay errored (HTTP {statuses[1]}) instead of "
                        f"returning the original response")
    if alt_status >= 500:
        problems.append(f"alt-payload send errored (HTTP {alt_status}); a "
                        f"4xx key-conflict is acceptable, a 5xx is not")
    if mid - before != 1:
        problems.append(f"same-key replay delta {mid - before}, expected 1")
    if after - mid != 0:
        problems.append(f"same-key different-payload delta {after - mid}, "
                        "expected 0 (key reuse must not create a second "
                        "side effect)")
    if problems:
        return Verdict("idempotency-variants", "FAIL", "; ".join(problems), evidence)
    return Verdict("idempotency-variants", "PASS",
                   "idempotency key honoured across replay and "
                   "different-payload reuse (deltas 1 then 0)",
                   evidence)


def run_idempotency_scope_check(
    url_a: str,
    url_b: str,
    method: str = "POST",
    payload_a: object | None = None,
    payload_b: object | None = None,
    headers: dict[str, str] | None = None,
    idempotency_key: str = "apiverdict-scope-1",
    idempotency_header: str = "Idempotency-Key",
    verify_cmd_a: str | None = None,
    verify_url_a: str | None = None,
    verify_cmd_b: str | None = None,
    verify_url_b: str | None = None,
    timeout: float = 10.0,
    allow_remote: bool = False,
    verify_headers: dict[str, str] | None = None,
) -> Verdict:
    """Cross-endpoint idempotency-key scope: the same key sent to two
    DIFFERENT endpoints must never silently replay the first
    endpoint's result on the second. Keys are scoped per operation
    (a key for /charges must not be reusable on /refunds): the
    second endpoint must either create its own resource (delta 1,
    PASS) or reject loudly. A 2xx on B with delta 0 is the FAIL —
    B returned A's stored result for a different operation.

    Verdicts, deliberately narrow:
      * B 2xx + delta_b 1 -> PASS (scoped creation).
      * B 2xx + delta_b 0 -> FAIL (silent cross-endpoint replay).
      * B non-2xx -> INCONCLUSIVE: a rejection cannot be told
        apart from a validation rejection without more signal, so
        servers in the Stripe model (same key + different request
        -> 422) land here, not PASS and not FAIL. The check proves
        absence of silent replay, not presence of scoped creation
        for reject-model servers.
      * A failing to create, or either state unreadable ->
        INCONCLUSIVE (the scope question needs a clean A creation
        and both states).
    """
    method = str(method).upper()
    if method not in ("POST", "PUT", "PATCH", "DELETE"):
        return Verdict("idempotency-scope", "INCONCLUSIVE",
                       f"idempotency method must mutate (POST/PUT/PATCH/DELETE), "
                       f"got {method!r}", {"url_a": url_a, "url_b": url_b})
    for label, url in (("A", url_a), ("B", url_b)):
        refused = mutating_guard(url, allow_remote)
        if refused:
            return Verdict("idempotency-scope", "INCONCLUSIVE", refused,
                           {"url_a": url_a, "url_b": url_b})
    if (verify_cmd_a is None and verify_url_a is None) or \
            (verify_cmd_b is None and verify_url_b is None):
        return Verdict("idempotency-scope", "INCONCLUSIVE",
                       "need a verify command/URL for BOTH endpoints: scope "
                       "cannot be judged from one state", {})
    headers = dict(headers or {})
    headers[idempotency_header] = idempotency_key
    vheaders = verify_headers if verify_headers is not None else headers
    try:
        before_a = _read_state(verify_cmd_a, verify_url_a, 1,
                               verify_headers=vheaders)
        before_b = _read_state(verify_cmd_b, verify_url_b, 1,
                               verify_headers=vheaders)
    except Exception as exc:
        return Verdict("idempotency-scope", "INCONCLUSIVE",
                       f"could not read state before sends: {exc}", {})
    try:
        status_a, _b, _h = request_json(
            method, url_a, payload_a, headers, timeout=timeout)
        status_b, _b2, _h2 = request_json(
            method, url_b, payload_b, headers, timeout=timeout)
    except Exception as exc:
        return Verdict("idempotency-scope", "INCONCLUSIVE",
                       f"send failed: {exc}", {})
    if not (200 <= status_a < 300):
        return Verdict("idempotency-scope", "INCONCLUSIVE",
                       f"endpoint A failed (HTTP {status_a}): no A "
                       f"operation exists for B to replay", {})
    try:
        after_a = _read_state(verify_cmd_a, verify_url_a, 1,
                              verify_headers=vheaders)
        after_b = _read_state(verify_cmd_b, verify_url_b, 1,
                              verify_headers=vheaders)
    except Exception as exc:
        return Verdict("idempotency-scope", "INCONCLUSIVE",
                       f"could not read state after sends: {exc}", {})
    delta_a = after_a - before_a
    delta_b = after_b - before_b
    evidence = {"url_a": url_a, "url_b": url_b,
                "key_header": idempotency_header,
                "before_a": before_a, "after_a": after_a, "delta_a": delta_a,
                "before_b": before_b, "after_b": after_b, "delta_b": delta_b,
                "status_a": status_a, "status_b": status_b}
    if delta_a != 1:
        return Verdict("idempotency-scope", "INCONCLUSIVE",
                       f"endpoint A delta {delta_a} (expected 1): the first "
                       f"operation did not create cleanly, so scope cannot "
                       f"be judged", evidence)
    if not (200 <= status_b < 300):
        return Verdict("idempotency-scope", "INCONCLUSIVE",
                       f"endpoint B rejected (HTTP {status_b}): cannot tell "
                       f"a key-scope rejection from a validation rejection",
                       evidence)
    if delta_b == 1:
        return Verdict("idempotency-scope", "PASS",
                       "key scoped per endpoint: B created its own resource "
                       "(delta 1) instead of replaying A's", evidence)
    if delta_b == 0:
        return Verdict("idempotency-scope", "FAIL",
                       "key leaked across endpoints: B returned 2xx with "
                       "delta 0, replaying A's stored result for a "
                       "different operation", evidence)
    return Verdict("idempotency-scope", "FAIL",
                   f"unexpected B delta {delta_b} for a single send",
                   evidence)


__all__ = ["run_idempotency_check", "run_idempotency_variants_check", "run_idempotency_scope_check"]
