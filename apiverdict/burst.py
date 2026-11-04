"""burst check — simultaneous identical requests, final-state verification.

Idea: fire N identical state-changing requests through a threading
barrier gate, repeat for R rounds, and compare the *final state* (via a
user-supplied verify command / verify URL that prints a single number)
before and after each burst against --expect-delta (default 1).

Why final state, not status codes: a double-applied operation can still
answer 200/201 every time. Status codes lie; the count does not.

Rounds need isolation: each round substitutes ``{round}`` (1-based) into
the payload, verify command, and verify URL, so a correctly deduplicating
API can pass every round with a fresh unique field (e.g. slot-{round})
instead of failing round 2 because round 1 already created the resource.
If the payload has no {round} placeholder, rounds still run, but a
correctly deduplicating API will legitimately show delta 0 on later
rounds — the report says so in the evidence, and the user should add a
per-round unique field.

Safety: localhost targets need no flag. Any other host requires
allow_remote=True; the CLI prints a banner warning in that case.
This tool creates resources on purpose and never deletes anything.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass

from .http_util import is_localhost, request_json
from .runner import Verdict

LOCALHOST_BANNER = (
    "WARNING: burst creates real resources on purpose and never deletes "
    "anything. Use staging / test data only."
)


def _render(template: str, round_no: int) -> str:
    return template.replace("{round}", str(round_no))


def _run_verify_cmd(cmd: str) -> int:
    """Run a shell command; it must print exactly one integer."""
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise ValueError(f"verify command exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    text = proc.stdout.strip()
    match = re.search(r"-?\d+", text)
    if not match:
        raise ValueError(f"verify command printed no number: {text[:200]!r}")
    return int(match.group(0))


def _fetch_verify_url(url: str) -> int:
    with urllib.request.urlopen(url, timeout=10) as resp:
        body = resp.read().decode("utf-8", "replace").strip()
    try:
        data = json.loads(body)
    except ValueError:
        data = body
    if isinstance(data, dict) and "count" in data:
        return int(data["count"])
    return int(data)


def _read_state(verify_cmd: str | None, verify_url: str | None, round_no: int) -> int:
    if verify_cmd:
        return _run_verify_cmd(_render(verify_cmd, round_no))
    if verify_url:
        return _fetch_verify_url(_render(verify_url, round_no))
    raise ValueError("no verify command or verify URL supplied")


@dataclass
class _OneRequest:
    status: int | None
    send_skew_ms: float
    error: str | None = None


def _fire_burst(
    method: str,
    url: str,
    payload: object,
    headers: dict[str, str],
    n: int,
    timeout: float,
) -> list[_OneRequest]:
    barrier = threading.Barrier(n)
    results: list[_OneRequest | None] = [None] * n
    start_times: list[float] = [0.0] * n

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            results[index] = _OneRequest(None, 0.0, "barrier broken")
            return
        start_times[index] = time.perf_counter()
        try:
            status, _body, _hdrs = request_json(method, url, payload, headers, timeout)
            results[index] = _OneRequest(status, 0.0)
        except Exception as exc:  # transport failure is data, not a crash
            results[index] = _OneRequest(None, 0.0, str(exc)[:200])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    base = min(t for t in start_times if t) if any(start_times) else 0.0
    out: list[_OneRequest] = []
    for index, result in enumerate(results):
        if result is None:
            out.append(_OneRequest(None, 0.0, "no result"))
        else:
            skew = (start_times[index] - base) * 1000.0 if start_times[index] else 0.0
            out.append(_OneRequest(result.status, skew, result.error))
    return out


def run_burst_check(
    url: str,
    method: str = "POST",
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    n: int = 20,
    rounds: int = 3,
    verify_cmd: str | None = None,
    verify_url: str | None = None,
    expect_delta: int = 1,
    timeout: float = 10.0,
    allow_remote: bool = False,
) -> Verdict:
    if not is_localhost(url) and not allow_remote:
        return Verdict(
            "burst",
            "INCONCLUSIVE",
            "refused non-localhost target without --allow-remote",
            {"url": url, "hint": "re-run with --allow-remote for staging/test data only"},
        )
    if verify_cmd is None and verify_url is None:
        return Verdict(
            "burst",
            "INCONCLUSIVE",
            "no verify command or verify URL: final state cannot be checked",
            {"hint": "supply --verify-cmd or --verify-url; a burst without a "
                     "state check would be green on a skipped check"},
        )
    if n < 2:
        return Verdict("burst", "INCONCLUSIVE", "burst size n must be >= 2", {"n": n})

    headers = headers or {}
    round_results = []
    failed_rounds = []
    for round_no in range(1, rounds + 1):
        round_payload = payload
        if isinstance(payload, str):
            round_payload = _render(payload, round_no)
            try:
                round_payload = json.loads(round_payload)
            except ValueError:
                pass
        elif isinstance(payload, dict):
            rendered = json.dumps(payload).replace("{round}", str(round_no))
            round_payload = json.loads(rendered)
        try:
            before = _read_state(verify_cmd, verify_url, round_no)
        except Exception as exc:
            return Verdict("burst", "INCONCLUSIVE",
                           f"could not read state before round {round_no}: {exc}",
                           {"round": round_no})
        fired = _fire_burst(method, url, round_payload, headers, n, timeout)
        try:
            after = _read_state(verify_cmd, verify_url, round_no)
        except Exception as exc:
            return Verdict("burst", "INCONCLUSIVE",
                           f"could not read state after round {round_no}: {exc}",
                           {"round": round_no})
        delta = after - before
        statuses = sorted({r.status for r in fired if r.status is not None})
        errors = [r.error for r in fired if r.error]
        skew_ms = max((r.send_skew_ms for r in fired), default=0.0)
        ok = delta == expect_delta and not errors
        record = {
            "round": round_no,
            "before": before,
            "after": after,
            "delta": delta,
            "expected_delta": expect_delta,
            "statuses": statuses,
            "send_skew_ms": round(skew_ms, 2),
            "transport_errors": errors,
        }
        round_results.append(record)
        if not ok:
            failed_rounds.append(round_no)

    evidence = {
        "url": url,
        "burst_size": n,
        "rounds": rounds,
        "rounds_failed": failed_rounds,
        "per_round": round_results,
    }
    if failed_rounds:
        worst = max(r["delta"] for r in round_results)
        return Verdict(
            "burst",
            "FAIL",
            f"duplicate side effects in round(s) {failed_rounds}: "
            f"largest delta {worst}, expected {expect_delta}",
            evidence,
        )
    return Verdict(
        "burst",
        "PASS",
        f"exactly {expect_delta} side effect(s) per round across {rounds} round(s)",
        evidence,
    )
