"""CLI for apiverdict (working name, verify before publish).

Usage:
  python -m apiverdict demo [--mode naive|fixed|both]
  python -m apiverdict check --base-url http://127.0.0.1:PORT [--json]
  python -m apiverdict burst --url ... --payload '...' --verify-cmd '...'
  python -m apiverdict pagination --url ... [--page-size 10]
  python -m apiverdict errors --endpoint POST:http://... --endpoint POST:http://...
  python -m apiverdict fixture --mode naive [--port 8765]

Exit codes: 0 pass, 1 check failed, 2 could-not-verify (never green
on a skipped check). `demo` with no --mode runs both fixture modes
and exits 0 itself, because the naive FAIL is the expected demo.
"""

from __future__ import annotations

import argparse
import json
import sys

from .burst import LOCALHOST_BANNER
from .checks import run_burst_check, run_error_shape_check, run_pagination_check
from .demo import run_all_checks, run_demo
from .fixture import serve_forever
from .http_util import is_localhost
from .runner import Verdict, build_report, print_report


def _parse_headers(raw: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw:
        if ":" in item:
            key, value = item.split(":", 1)
            headers[key.strip()] = value.strip()
    return headers


def _parse_endpoint(raw: str) -> dict:
    method, sep, url = raw.partition(":")
    if sep and method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return {"method": method.upper(), "url": url}
    return {"method": "POST", "url": raw}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apiverdict")
    sub = parser.add_subparsers(dest="command", required=True)

    demo_p = sub.add_parser("demo", help="run all checks vs the bundled fixture")
    demo_p.add_argument("--mode", choices=["naive", "fixed", "both"], default="both")
    demo_p.add_argument("--json", action="store_true")

    check_p = sub.add_parser("check", help="run all checks vs a base URL")
    check_p.add_argument("--base-url", required=True)
    check_p.add_argument("--json", action="store_true")

    burst_p = sub.add_parser("burst", help="simultaneous-requests check")
    burst_p.add_argument("--url", required=True)
    burst_p.add_argument("--method", default="POST")
    burst_p.add_argument("--payload", default="{}")
    burst_p.add_argument("--header", action="append", default=[])
    burst_p.add_argument("--n", type=int, default=20)
    burst_p.add_argument("--rounds", type=int, default=3)
    burst_p.add_argument("--verify-cmd")
    burst_p.add_argument("--verify-url")
    burst_p.add_argument("--expect-delta", type=int, default=1)
    burst_p.add_argument("--allow-remote", action="store_true")
    burst_p.add_argument("--json", action="store_true")

    page_p = sub.add_parser("pagination", help="list-walker check")
    page_p.add_argument("--url", required=True)
    page_p.add_argument("--page-size", type=int, default=10)
    page_p.add_argument("--style", default="auto",
                        choices=["auto", "offset", "page", "cursor", "link"])
    page_p.add_argument("--id-field", default="id")
    page_p.add_argument("--json", action="store_true")

    err_p = sub.add_parser("errors", help="error-shape consistency check")
    err_p.add_argument("--endpoint", action="append", required=True)
    err_p.add_argument("--invalid-payload", default='{"__invalid__": true}')
    err_p.add_argument("--json", action="store_true")

    fix_p = sub.add_parser("fixture", help="run the bundled fixture server")
    fix_p.add_argument("--mode", choices=["naive", "fixed"], default="naive")
    fix_p.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)

    if args.command == "fixture":
        serve_forever(args.mode, port=args.port)
        return 0

    if args.command == "demo":
        modes = ["naive", "fixed"] if args.mode == "both" else [args.mode]
        code = 0
        for mode in modes:
            print(f"--- demo: fixture mode = {mode} ---")
            report = run_demo(mode)
            print_report(report, args.json)
            print()
            if args.mode != "both":
                code = report["exit_code"]
        return code

    if args.command == "check":
        verdicts = run_all_checks(args.base_url.rstrip("/"))
        report = build_report(verdicts)
        print_report(report, args.json)
        return report["exit_code"]

    if args.command == "burst":
        if not is_localhost(args.url) and args.allow_remote:
            print(LOCALHOST_BANNER)
        try:
            payload = json.loads(args.payload)
        except ValueError:
            payload = args.payload
        verdict = run_burst_check(
            url=args.url, method=args.method, payload=payload,
            headers=_parse_headers(args.header), n=args.n, rounds=args.rounds,
            verify_cmd=args.verify_cmd, verify_url=args.verify_url,
            expect_delta=args.expect_delta, allow_remote=args.allow_remote,
        )
        report = build_report([verdict])
        print_report(report, args.json)
        return report["exit_code"]

    if args.command == "pagination":
        verdict = run_pagination_check(
            url=args.url, page_size=args.page_size, style=args.style,
            id_field=args.id_field)
        report = build_report([verdict])
        print_report(report, args.json)
        return report["exit_code"]

    if args.command == "errors":
        try:
            invalid = json.loads(args.invalid_payload)
        except ValueError:
            invalid = args.invalid_payload
        verdict = run_error_shape_check(
            targets=[_parse_endpoint(e) for e in args.endpoint],
            invalid_payload=invalid)
        report = build_report([verdict])
        print_report(report, args.json)
        return report["exit_code"]

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
