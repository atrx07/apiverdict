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
from .runner import Verdict, build_report


def run_all_checks(base_url: str) -> list[Verdict]:
    """Run the three v1 checks against a base URL (fixture or real API).

    Burst uses a per-round slot so each round is isolated; pagination
    walks /api/items; error-shape probes /api/book and /api/order.
    """
    burst = run_burst_check(
        url=f"{base_url}/api/book",
        payload={"slot": "demo-slot-{round}"},
        n=10,
        rounds=2,
        verify_url=f"{base_url}/api/count?slot=demo-slot-{{round}}",
        expect_delta=1,
    )
    pagination = run_pagination_check(url=f"{base_url}/api/items", page_size=10)
    errors = run_error_shape_check(
        targets=[
            {"method": "POST", "url": f"{base_url}/api/book"},
            {"method": "POST", "url": f"{base_url}/api/order"},
        ],
        invalid_payload={"__invalid__": True},
    )
    return [burst, pagination, errors]


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
