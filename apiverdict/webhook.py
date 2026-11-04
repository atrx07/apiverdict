"""webhook handler check — signature, duplicate, replay.

Sends to a webhook handler URL (the user supplies it; apiverdict is the
sender here, not the receiver):

  1. Valid signed event      => must be 2xx.
  2. Tampered body, same sig  => must be 401/403 (signature rejected).
  3. Same event id + same sig, sent twice => must be 2xx both times
     (an idempotent ack), or 409 (a recognised duplicate — the
     handler saw the event id and refused to re-apply, which is
     also correct dedup behaviour), BUT the side effect can only be proven with
     a verify URL/command: without one, the duplicate cell is reported
     as "ack-only, side effect unverified" and does NOT upgrade the
     verdict to a full duplicate-proof PASS on its own — the verdict
     stays PASS only on the signature findings, with the limitation
     stated in evidence. With a verify URL, delta must be 1.
  4. Replayed event with a stale timestamp (default: 10 minutes old,
     past a 5-minute tolerance) => must be 401/403 if the handler
     enforces timestamp tolerance; if the handler accepts it, that is
     a FAIL finding only when enforce_timestamp=True (default True,
     because an unenforced timestamp is exactly how replays succeed).
  5. Future-dated event (10 minutes ahead) => same rule. A handler
     that only rejects old timestamps accepts a signed event dated
     next week, which is a replay window in the other direction
     (and a clock-skew bug made permanent).

Signing schemes (--scheme, default apiverdict):
  apiverdict: HMAC-SHA256 over f"{timestamp}.{event_id}.{body_json}"
    (body_json sorted), header X-Signature-256: "sha256=<hex>",
    X-Event-Id, X-Timestamp. This is the check's own scheme: it is
    verified against the PARSED body re-serialized sorted, which is
    what the bundled fixtures do.
  stripe: HMAC-SHA256 over f"{timestamp}.{body_json}" (body_json
    exactly as sent, event id injected as body["id"]), header
    Stripe-Signature: "t=<ts>,v1=<hex>". Signs the exact bytes
    sent, so it works against real Stripe-style handlers, which
    verify raw bytes.
  github: HMAC-SHA256 over body_json exactly as sent, header
    X-Hub-Signature-256: "sha256=<hex>" plus X-GitHub-Delivery
    (the event id). GitHub's scheme carries no timestamp, so the
    stale/future probes are skipped for it (recorded in evidence)
    instead of false-FAILING every GitHub handler.
The secret is supplied by the caller (test/staging secret), never
printed in evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from .burst import _read_state
from .http_util import mutating_guard, request_json
from .runner import Verdict


SCHEMES = ("apiverdict", "stripe", "github")


def sign(secret: str, timestamp: int, event_id: str, body: dict,
         scheme: str = "apiverdict") -> str:
    """Signature header value for one scheme. stripe/github sign the
    exact bytes request_json will send (json.dumps, insertion
    order); apiverdict signs the sorted form (parsed-body
    verification, see module docstring)."""
    if scheme == "stripe":
        signed_body = dict(body, id=event_id)
        msg = f"{timestamp}.{json.dumps(signed_body)}".encode()
        return ("t=" + str(timestamp) + ",v1=" +
                hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest())
    if scheme == "github":
        msg = json.dumps(body).encode()
        return "sha256=" + hmac.new(
            secret.encode(), msg, hashlib.sha256).hexdigest()
    if scheme != "apiverdict":
        raise ValueError(f"unknown webhook scheme: {scheme!r}")
    msg = f"{timestamp}.{event_id}.{json.dumps(body, sort_keys=True)}".encode()
    return "sha256=" + hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _send(url, secret, event_id, body, timestamp, tamper=False,
          headers=None, scheme="apiverdict"):
    if scheme == "stripe":
        send_body = dict(body, id=event_id)
        sig = sign(secret, timestamp, event_id, body, scheme)
        hdrs = {"Stripe-Signature": sig}
    elif scheme == "github":
        send_body = dict(body)
        sig = sign(secret, timestamp, event_id, body, scheme)
        hdrs = {"X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": event_id}
    else:
        send_body = dict(body)
        sig = sign(secret, timestamp, event_id, body, scheme)
        hdrs = {"X-Signature-256": sig, "X-Event-Id": event_id,
                "X-Timestamp": str(timestamp)}
    if tamper:
        send_body = dict(send_body, tampered=True)
    if headers:
        hdrs.update(headers)
    return request_json("POST", url, send_body, hdrs)


def run_webhook_check(
    url: str,
    secret: str,
    headers: dict | None = None,
    event_id: str = "evt-apiverdict-1",
    body: dict | None = None,
    verify_cmd: str | None = None,
    verify_url: str | None = None,
    verify_headers: dict[str, str] | None = None,
    enforce_timestamp: bool = True,
    timestamp_tolerance_s: int = 300,
    scheme: str = "apiverdict",
    timeout: float = 10.0,
    allow_remote: bool = False,
) -> Verdict:
    refused = mutating_guard(url, allow_remote)
    if refused:
        return Verdict("webhook", "INCONCLUSIVE", refused, {"url": url})
    if scheme not in SCHEMES:
        return Verdict("webhook", "INCONCLUSIVE",
                       f"unknown webhook scheme: {scheme!r}", {"url": url})
    if timestamp_tolerance_s < 0:
        return Verdict("webhook", "INCONCLUSIVE",
                       "timestamp tolerance must be >= 0", {"url": url})
    body = body or {"type": "test.event", "amount": 100}
    if not isinstance(body, dict):
        # dict(body) in _send would raise on a list/string body and
        # take the CLI down with a traceback; refuse it cleanly.
        return Verdict("webhook", "INCONCLUSIVE",
                       "webhook body must be a JSON object", {"url": url})
    now = int(time.time())
    findings: list[str] = []
    evidence: dict = {"url": url, "event_id": event_id, "scheme": scheme}

    def _post(*args, **kwargs):
        try:
            status, _b, _h = _send(url, secret, event_id, body, *args, headers=headers, scheme=scheme, **kwargs)
            return status, None
        except Exception as exc:
            return None, str(exc)

    # 1. valid
    status, err = _post(now)
    evidence["valid_status"] = status
    if err is not None:
        return Verdict("webhook", "INCONCLUSIVE", f"valid event failed: {err}", evidence)
    if not (200 <= status < 300):
        findings.append(f"valid signed event rejected (HTTP {status})")
    # 2. tampered
    status, err = _post(now, tamper=True)
    evidence["tampered_status"] = status
    if err is not None:
        return Verdict("webhook", "INCONCLUSIVE", f"tampered event failed: {err}", evidence)
    if status not in (401, 403):
        findings.append(f"tampered body accepted (HTTP {status}, expected 401/403)")
    # 3. duplicate (ack + optional final-state check)
    before = after = None
    if verify_cmd or verify_url:
        try:
            before = _read_state(verify_cmd, verify_url, 1, verify_headers=verify_headers if verify_headers is not None else headers)
        except Exception as exc:
            return Verdict("webhook", "INCONCLUSIVE",
                           f"could not read state before duplicate: {exc}", evidence)
    status, err = _post(now)
    evidence["duplicate_status"] = status
    if err is not None:
        return Verdict("webhook", "INCONCLUSIVE", f"duplicate event failed: {err}", evidence)
    if not (200 <= status < 300) and status != 409:
        findings.append(f"duplicate event not acked (HTTP {status})")
    if verify_cmd or verify_url:
        try:
            after = _read_state(verify_cmd, verify_url, 1, verify_headers=verify_headers if verify_headers is not None else headers)
        except Exception as exc:
            return Verdict("webhook", "INCONCLUSIVE",
                           f"could not read state after duplicate: {exc}", evidence)
        evidence.update({"duplicate_before": before, "duplicate_after": after,
                         "duplicate_delta": after - before})
        if after - before != 0:
            # Valid event already counted once before this duplicate send;
            # the duplicate itself must add 0.
            findings.append(f"duplicate event double-applied (delta {after - before})")
    else:
        evidence["duplicate_side_effect"] = "unverified (no verify URL/command)"
    # 4. stale replay — skipped for github: its scheme carries no
    # timestamp, so there is nothing for the handler to enforce.
    if scheme == "github":
        evidence["timestamp_probes"] = ("skipped: github scheme carries "
                                        "no timestamp")
        if findings:
            return Verdict("webhook", "FAIL", "; ".join(findings), evidence)
        return Verdict("webhook", "PASS",
                       "webhook handler: valid acked, tampered rejected, "
                       "duplicate acked" + (" with no double-apply" if verify_cmd or verify_url
                                            else " (side effect unverified, no verify supplied)"),
                       evidence)
    stale = now - timestamp_tolerance_s - 600
    status, err = _post(stale)
    evidence["stale_replay_status"] = status
    if err is not None:
        return Verdict("webhook", "INCONCLUSIVE", f"stale replay failed: {err}", evidence)
    if enforce_timestamp and status not in (401, 403):
        findings.append(f"stale replay accepted (HTTP {status}, expected 401/403)")
    # 5. future-dated event (same tolerance, other direction)
    future = now + timestamp_tolerance_s + 600
    status, err = _post(future)
    evidence["future_status"] = status
    if err is not None:
        return Verdict("webhook", "INCONCLUSIVE", f"future event failed: {err}", evidence)
    if enforce_timestamp and status not in (401, 403):
        findings.append(f"future-dated event accepted (HTTP {status}, expected 401/403)")

    if findings:
        return Verdict("webhook", "FAIL", "; ".join(findings), evidence)
    return Verdict("webhook", "PASS",
                   "webhook handler: valid acked, tampered rejected, "
                   "duplicate acked" + (" with no double-apply" if verify_cmd or verify_url
                                        else " (side effect unverified, no verify supplied)"),
                   evidence)
