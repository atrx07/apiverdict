"""Demo runner — FAIL on naive, PASS on fixed, in seconds.

Starts a localhost fixture server in-process, runs all three v1 checks
against it, prints a unified report, and stops the server. No real API,
no auth, no data risk. ``run_demo(mode)`` returns the report dict so
tests can assert on it without parsing stdout.
"""

from __future__ import annotations

import threading

from .burst import run_burst_check
from .error_shape import run_error_shape_check
from .fixture import create_server
from .pagination import run_pagination_check
from .runner import Verdict, build_report, run_parallel, time_verdict


def run_all_checks(base_url: str, headers: dict | None = None, timeout: float = 10.0) -> list[Verdict]:
    """Run the three v1 checks against a base URL (fixture or real API).

    Burst uses a per-round slot so each round is isolated; pagination
    walks /api/items; error-shape probes /api/book and /api/order.
    """
    burst = time_verdict(lambda: run_burst_check(
        url=f"{base_url}/api/book",
        payload={"slot": "demo-slot-{round}"},
        headers=headers,
        n=10,
        rounds=2,
        verify_url=f"{base_url}/api/count?slot=demo-slot-{{round}}",
        expect_delta=1, timeout=timeout,
        verify_headers=headers,
    ))
    pagination = time_verdict(lambda: run_pagination_check(
        url=f"{base_url}/api/items", page_size=10,
        headers=headers, timeout=timeout))
    errors = time_verdict(lambda: run_error_shape_check(
        targets=[
            {"method": "POST", "url": f"{base_url}/api/book"},
            {"method": "POST", "url": f"{base_url}/api/order"},
        ],
        invalid_payload={"__invalid__": True},
        headers=headers, timeout=timeout,
    ))
    return [burst, pagination, errors]


def run_all_checks_parallel(base_url: str, headers: dict | None = None,
                            timeout: float = 10.0) -> list[Verdict]:
    """Same trio as run_all_checks, run concurrently.

    Only the trio, and only because its endpoints are independent for
    the fixture (burst counts by slot; error probes carry no slot;
    pagination only reads). Do not generalise this to arbitrary
    check sets against a shared target without checking that their
    writes cannot interact — parallel checks that write to the same
    resource test each other, not the API. Output order is the trio
    order, not completion order (see runner.run_parallel).
    """
    return run_parallel([
        ("burst", lambda: run_burst_check(
            url=f"{base_url}/api/book",
            payload={"slot": "demo-slot-{round}"},
            headers=headers, n=10, rounds=2,
            verify_url=f"{base_url}/api/count?slot=demo-slot-{{round}}",
            expect_delta=1, timeout=timeout, verify_headers=headers)),
        ("pagination", lambda: run_pagination_check(
            url=f"{base_url}/api/items", page_size=10,
            headers=headers, timeout=timeout)),
        ("error-shape", lambda: run_error_shape_check(
            targets=[
                {"method": "POST", "url": f"{base_url}/api/book"},
                {"method": "POST", "url": f"{base_url}/api/order"},
            ],
            invalid_payload={"__invalid__": True},
            headers=headers, timeout=timeout)),
    ])


def run_demo(mode: str) -> dict:
    server = create_server(mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        verdicts = run_all_checks(base_url)
        report = build_report(verdicts)
        report["fixture_mode"] = mode
        return report
    finally:
        server.shutdown()
        server.server_close()
