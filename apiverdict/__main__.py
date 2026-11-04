"""CLI for apiverdict — API behaviour toolkit.

Usage:
  python -m apiverdict demo [--mode naive|fixed|both]
  python -m apiverdict casestudy [--mode naive|fixed|both]
  python -m apiverdict check --base-url http://127.0.0.1:PORT [--json]
  python -m apiverdict burst --url ... --payload '...' --verify-cmd '...'
  python -m apiverdict pagination --url ... [--page-size 10]
  python -m apiverdict errors --endpoint POST:http://... --endpoint POST:http://...
  python -m apiverdict auth-matrix --roles '[{"name":"admin","headers":{}}]' \
      --targets '[{"method":"GET","url":"...","allowed_roles":["admin"]}]'
  python -m apiverdict rate-limit --url ... [--requests 30]
  python -m apiverdict idempotency --url ... --verify-url ...
  python -m apiverdict webhook --url ... --secret '...' [--verify-url ...]
  python -m apiverdict cache --read-url ... --write-url ...
  python -m apiverdict openapi --spec spec.json --base-url http://127.0.0.1:PORT
  python -m apiverdict fixture --mode naive [--port 8765]

Auth/env: most commands accept --config apiverdict.json --env staging.
The config names an env var for the credential; apiverdict reads that
named var only, never auto-scrapes the environment, and never prints
the credential value in output or reports.

Exit codes: 0 pass, 1 check failed, 2 could-not-verify (never green
on a skipped check). `demo` with no --mode runs both fixture modes
and exits 0 itself, because the naive FAIL is the expected demo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .auth_matrix import run_auth_matrix_check
from .burst import LOCALHOST_BANNER
from .cache import run_cache_check
from .cache_etag import run_cache_etag_check
from .if_match import run_if_match_check
from .casestudy import run_casestudy
from .error_leak import run_error_leak_check
from .error_variants import run_error_variants_check
from .head import run_head_consistency_check
from .cors import run_cors_check
from .delete_replay import run_delete_replay_check
from .patch import run_patch_preserves_check
from .detail import run_detail_superset_check
from .method_guards import run_method_guards_check
from .cache_privacy import run_cache_privacy_check
from .auth_schemes import run_auth_schemes_check
from .redirect import run_redirect_check
from .list_filter import run_filter_check
from .checks import CHECK_DESCRIPTIONS, CHECKS, run_burst_check, run_error_shape_check, run_pagination_check
from .config import auth_evidence, get_environment, load_config, resolve_headers
from .demo import run_all_checks, run_all_checks_parallel, run_demo
from .fixture import serve_forever
from .http_util import is_localhost, validate_url
from .idempotency import (run_idempotency_check,
                            run_idempotency_scope_check,
                            run_idempotency_variants_check)
from .library import run_library
from .junit import report_to_junit, reports_to_junit
from .pagination_concurrent import run_pagination_concurrent_check
from .pagination_consistency import run_pagination_consistency_check
from .openapi import generate_plan, load_spec, run_openapi_checks, spec_base_url
from .rate_limit import run_rate_limit_check
from .runner import Verdict, build_report, print_report, run_parallel, time_verdict
from .spec_conformance import run_spec_conformance_check
from .spec_diff import run_spec_diff_check
from .spec_examples import run_spec_examples_check
from .spec_errors import run_spec_errors_check
from .suite import load_suite, run_suite
from .webhook import run_webhook_check


def _parse_headers(raw: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw:
        if ":" in item:
            key, value = item.split(":", 1)
            headers[key.strip()] = value.strip()
    return headers


def _verify_headers_for(args) -> dict[str, str] | None:
    """--verify-header values, or None when none given so the library
    falls back to the check's own headers (passing {} would override
    them with an empty dict and break authed verify reads)."""
    parsed = _parse_headers(getattr(args, "verify_header", []) or [])
    return parsed or None


def _parse_endpoint(raw: str) -> dict:
    method, sep, url = raw.partition(":")
    if sep and method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE", "QUERY"):
        return {"method": method.upper(), "url": url}
    return {"method": "POST", "url": raw}


def _load_json_arg(raw: str, what: str):
    """Parse a JSON string, or @file.json. Raises ValueError with context."""
    text = raw
    if raw.startswith("@"):
        try:
            text = open(raw[1:], encoding="utf-8").read()
        except OSError as exc:
            raise ValueError(f"cannot read {what} file {raw[1:]}: {exc}") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"invalid JSON for {what}: {exc}") from exc


def _auth_for(args) -> tuple[dict[str, str], dict]:
    """Resolve --config/--env + --header into headers (secrets excluded
    from the returned evidence dict). Returns (headers, evidence)."""
    config_path = getattr(args, "config", None)
    if not config_path:
        return _parse_headers(getattr(args, "header", []) or []), {"auth": "none"}
    try:
        config = load_config(config_path)
        env = get_environment(config, getattr(args, "env", None))
        headers = resolve_headers(env, _parse_headers(getattr(args, "header", []) or []))
    except ValueError as exc:
        print(f"apiverdict: config error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    return headers, auth_evidence(env)


def _add_auth_args(p) -> None:
    p.add_argument("--config", help="config file (JSON, or TOML on Python 3.11+)")
    p.add_argument("--env", help="named environment in the config")
    p.add_argument("--header", action="append", default=[],
                   help="extra header 'Name: value' (repeatable, wins over config)")


def _both_code(naive_exit: int, fixed_exit: int) -> int:
    """Exit code for --mode both runs. 0 only when the demonstration
    behaved as expected: naive FAILs (exit 1, the tool caught the
    seeded bugs) and fixed PASSes (exit 0). Anything else is a
    tool problem: naive passing means the tool missed its own bugs
    (exit 1), naive inconclusive or fixed non-passing propagates
    that run's code. The old code returned 0 unconditionally for
    both-mode, hiding a fixed-run regression."""
    if naive_exit == 1:
        return 0 if fixed_exit == 0 else fixed_exit
    if naive_exit == 0:
        return 1
    return naive_exit


def _write_junit(report: dict, path: str | None) -> None:
    if path:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report_to_junit(report))
        except OSError as exc:
            print(f"apiverdict: could not write junit file {path}: {exc}",
                  file=sys.stderr)


def _die(message: str) -> None:
    print(f"apiverdict: {message}", file=sys.stderr)
    raise SystemExit(2)


def _add_timeout(p) -> None:
    p.add_argument("--timeout", type=float, default=10.0,
                   help="per-request timeout in seconds (default 10)")


def _add_page_params(p) -> None:
    p.add_argument("--page-param", default="page",
                   help="query param for the page number (default page)")
    p.add_argument("--size-param", default="size",
                   help="query param for the page size (default size)")
    p.add_argument("--offset-param", default="offset",
                   help="query param for the offset (default offset)")
    p.add_argument("--limit-param", default="limit",
                   help="query param for the limit (default limit)")
    p.add_argument("--cursor-param", default="cursor",
                   help="query param for the cursor (default cursor)")


def _add_junit(p) -> None:
    p.add_argument("--junit", help="write JUnit XML report to this file")


def _validate_args(args) -> None:
    """CLI argument validation, all failures exit 2 before any network
    call. Library checks keep returning INCONCLUSIVE for bad runtime
    values; the CLI refuses bad user input outright — the two layers
    are different, and both are deliberate."""
    int_rules = {"n": 1, "rounds": 1, "page_size": 1, "max_pages": 1, "page_size_a": 1,
                 "page_size_b": 1, "requests": 1,
                 "write_after_page": 1, "expect_delta": 0, "port": 1,
                 "timestamp_tolerance": 0, "max_hops": 1}
    for name, minimum in int_rules.items():
        value = getattr(args, name, None)
        if value is not None and value < minimum:
            _die(f"--{name.replace('_', '-')} must be >= {minimum}, got {value}")
    # Burst is thread-per-request behind a barrier: an uncapped
    # --n is a thread-exhaustion footgun (a --n 100000 typo
    # would try to spawn 100000 threads). 1000 is a generous
    # safety bound, not a judgement on good burst sizes.
    n_value = getattr(args, "n", None)
    if n_value is not None and n_value > 1000:
        _die(f"--n must be <= 1000, got {n_value}")
    port = getattr(args, "port", None)
    if port is not None and port > 65535:
        _die(f"--port must be <= 65535, got {port}")
    timeout = getattr(args, "timeout", None)
    if timeout is not None and timeout <= 0:
        _die(f"--timeout must be positive, got {timeout}")
    for field in ("url", "base_url", "read_url", "write_url", "verify_url",
                "url_a", "url_b", "verify_url_a", "verify_url_b",
                "list_url"):
        value = getattr(args, field, None)
        if not value:
            continue
        # head --url is repeatable, so value is a list there.
        for one in value if isinstance(value, list) else [value]:
            error = validate_url(one, f"--{field.replace('_', '-')}")
            if error:
                _die(error)
    for raw in getattr(args, "endpoint", None) or []:
        parsed = _parse_endpoint(raw)
        error = validate_url(parsed["url"], "--endpoint")
        if error:
            _die(error)
    method = getattr(args, "method", None) or getattr(args, "write_method", None)
    if method and method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "QUERY"):
        _die(f"unsupported HTTP method {method!r}")
    # Writes must mutate: a GET/HEAD "write" (cache, cache-etag,
    # pagination-concurrent) would misreport as a server finding.
    write_method = getattr(args, "write_method", None)
    if write_method and write_method.upper() not in ("POST", "PUT", "PATCH", "DELETE"):
        _die(f"--write-method must mutate (POST/PUT/PATCH/DELETE), got {write_method!r}")
    # Side-effect checks must mutate too: a GET burst or GET replay
    # creates nothing, so its delta 0 would misread as a finding.
    if getattr(args, "command", None) in ("burst", "idempotency",
                                          "idempotency-variants",
                                          "idempotency-scope"):
        m = getattr(args, "method", None)
        if m and m.upper() not in ("POST", "PUT", "PATCH", "DELETE"):
            _die(f"--method must mutate (POST/PUT/PATCH/DELETE) for "
                 f"{args.command}, got {m!r}")


def _report(verdict: Verdict, as_json: bool, junit_path: str | None = None) -> int:
    report = build_report([verdict])
    print_report(report, as_json)
    _write_junit(report, junit_path)
    return report["exit_code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apiverdict")
    parser.add_argument("--version", action="version", version=f"apiverdict {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    demo_p = sub.add_parser("demo", help="run all checks vs the bundled fixture")
    demo_p.add_argument("--mode", choices=["naive", "fixed", "both"], default="both")
    demo_p.add_argument("--json", action="store_true")
    demo_p.add_argument("--junit", help="write JUnit XML report to this file")

    lib_p = sub.add_parser("library", help="run all 8 checks vs the library case-study fixture")
    lib_p.add_argument("--mode", choices=["naive", "fixed", "both"], default="both")
    lib_p.add_argument("--json", action="store_true")
    lib_p.add_argument("--junit", help="write JUnit XML report to this file")

    case_p = sub.add_parser("casestudy", help="run all 8 checks vs the case-study fixture")
    case_p.add_argument("--mode", choices=["naive", "fixed", "both"], default="both")
    case_p.add_argument("--json", action="store_true")
    case_p.add_argument("--junit", help="write JUnit XML report to this file")

    check_p = sub.add_parser("check", help="run the v1 trio vs a base URL "
                           "(burst, pagination, error-shape; use the "
                           "single-check commands for the rest). The trio "
                           "is wired to fixture-shaped endpoints "
                           "(/api/book, /api/items, /api/order, "
                           "/api/count) — for a real API, use the "
                           "single-check commands or spec-check, not check")
    check_p.add_argument("--base-url", required=True)
    check_p.add_argument("--only", help="comma-separated subset, e.g. "
                         "--only burst,pagination")
    check_p.add_argument("--json", action="store_true")
    check_p.add_argument("--junit", help="write JUnit XML report to this file")
    check_p.add_argument("--parallel", action="store_true",
                         help="run the trio concurrently (only for "
                              "independent endpoints; output order is "
                              "still burst, pagination, error-shape)")
    _add_auth_args(check_p)
    _add_timeout(check_p)

    list_p = sub.add_parser("list", help="list all checks and what each proves")

    conc_p = sub.add_parser("pagination-concurrent",
                            help="pagination walk with one row written mid-walk")
    conc_p.add_argument("--url", required=True)
    conc_p.add_argument("--page-size", type=int, default=10)
    conc_p.add_argument("--style", default="auto",
                        choices=["auto", "offset", "page", "cursor", "link"])
    conc_p.add_argument("--id-field", default="id")
    conc_p.add_argument("--write-url", required=True)
    conc_p.add_argument("--write-method", default="POST")
    conc_p.add_argument("--write-payload", default="{}")
    conc_p.add_argument("--write-after-page", type=int, default=1)
    conc_p.add_argument("--max-pages", type=int, default=100)
    conc_p.add_argument("--allow-remote", action="store_true",
                        help="allow this mutating check against "
                             "non-localhost URLs (staging/test data only)")
    conc_p.add_argument("--json", action="store_true")
    _add_auth_args(conc_p)
    _add_timeout(conc_p)
    _add_junit(conc_p)
    _add_page_params(conc_p)

    pcons_p = sub.add_parser("pagination-consistency",
                             help="same list at two page sizes; sequence and total must agree")
    pcons_p.add_argument("--url", required=True)
    pcons_p.add_argument("--page-size-a", type=int, default=10)
    pcons_p.add_argument("--page-size-b", type=int, default=5)
    pcons_p.add_argument("--style", default="auto",
                         choices=["auto", "offset", "page", "cursor", "link"])
    pcons_p.add_argument("--id-field", default="id")
    pcons_p.add_argument("--max-pages", type=int, default=100)
    pcons_p.add_argument("--json", action="store_true")
    _add_auth_args(pcons_p)
    _add_timeout(pcons_p)
    _add_junit(pcons_p)
    _add_page_params(pcons_p)

    burst_p = sub.add_parser("burst", help="simultaneous-requests check")
    burst_p.add_argument("--url", required=True)
    burst_p.add_argument("--method", default="POST")
    burst_p.add_argument("--payload", default="{}")
    burst_p.add_argument("--n", type=int, default=20)
    burst_p.add_argument("--rounds", type=int, default=3)
    burst_p.add_argument("--verify-cmd")
    burst_p.add_argument("--verify-url")
    burst_p.add_argument("--verify-header", action="append", default=[],
                         help="header 'Name: value' for verify reads only "
                              "(repeatable; defaults to the check headers)")
    burst_p.add_argument("--idempotency-key", help="same key on every request "
                         "in a round ({round} substituted per round)")
    burst_p.add_argument("--idempotency-header", default="Idempotency-Key")
    burst_p.add_argument("--expect-delta", type=int, default=1)
    burst_p.add_argument("--compare-responses", action="store_true",
                         help="with --idempotency-key: require all N "
                              "responses in a round to be identical "
                              "(needs the key; without one, differing "
                              "responses are correct)")
    burst_p.add_argument("--ignore-field", action="append", default=[],
                         help="top-level response field to ignore when "
                              "comparing burst responses (repeatable)")
    burst_p.add_argument("--allow-remote", action="store_true",
                        help="allow this mutating check against "
                             "non-localhost URLs (staging/test data only)")
    burst_p.add_argument("--json", action="store_true")
    _add_auth_args(burst_p)
    _add_timeout(burst_p)
    _add_junit(burst_p)

    page_p = sub.add_parser("pagination", help="list-walker check")
    page_p.add_argument("--url", required=True)
    page_p.add_argument("--page-size", type=int, default=10)
    page_p.add_argument("--style", default="auto",
                        choices=["auto", "offset", "page", "cursor", "link"])
    page_p.add_argument("--id-field", default="id")
    page_p.add_argument("--max-pages", type=int, default=100)
    page_p.add_argument("--json", action="store_true")
    _add_auth_args(page_p)
    _add_timeout(page_p)
    _add_junit(page_p)
    _add_page_params(page_p)

    err_p = sub.add_parser("errors", help="error-shape consistency check")
    err_p.add_argument("--endpoint", action="append", required=True)
    err_p.add_argument("--invalid-payload", default='{"__invalid__": true}')
    err_p.add_argument("--require-rfc7807", action="store_true",
                       help="fail unless every endpoint returns RFC 7807/9457 problem details")
    err_p.add_argument("--require-rfc9457", action="store_true",
                       help="as --require-rfc7807, plus strict checks: status "
                            "member == HTTP status, type is an absolute URI "
                            "or 'about:blank', title non-empty")
    err_p.add_argument("--json", action="store_true")
    _add_auth_args(err_p)
    _add_timeout(err_p)
    _add_junit(err_p)

    evar_p = sub.add_parser("error-variants",
                              help="many invalid payload kinds to one endpoint; shapes must agree")
    evar_p.add_argument("--url", required=True)
    evar_p.add_argument("--method", default="POST")
    evar_p.add_argument("--payload", action="append", default=[],
                        help="JSON payload kind (repeatable; default set covers missing-field, wrong-type, unknown-field)")
    evar_p.add_argument("--json", action="store_true")
    _add_auth_args(evar_p)
    _add_timeout(evar_p)
    _add_junit(evar_p)

    auth_p = sub.add_parser("auth-matrix", help="role x endpoint check")
    auth_p.add_argument("--roles", required=True,
                        help="JSON list, or @file.json: "
                             '[{"name":"admin","headers":{"X-Role":"admin"}}]')
    auth_p.add_argument("--targets", required=True,
                        help="JSON list, or @file.json: "
                             '[{"method":"GET","url":"...","allowed_roles":["admin"]}]')
    auth_p.add_argument("--json", action="store_true")
    auth_p.add_argument("--allow-remote", action="store_true",
                        help="allow mutating-method targets against "
                             "non-localhost URLs")
    _add_timeout(auth_p)
    _add_junit(auth_p)

    rate_p = sub.add_parser("rate-limit", help="rate-limit behaviour check (sequential, not load)")
    rate_p.add_argument("--url", required=True)
    rate_p.add_argument("--method", default="GET")
    rate_p.add_argument("--payload")
    rate_p.add_argument("--requests", type=int, default=30)
    rate_p.add_argument("--expect-limit", action="store_true", default=True,
                        help="expect limiting (default); use --no-limit to invert")
    rate_p.add_argument("--no-limit", dest="expect_limit", action="store_false")
    rate_p.add_argument("--allow-remote", action="store_true",
                        help="allow a mutating method against a non-localhost URL")
    rate_p.add_argument("--json", action="store_true")
    _add_auth_args(rate_p)
    _add_timeout(rate_p)
    _add_junit(rate_p)

    idem_p = sub.add_parser("idempotency", help="idempotency-key replay check")
    idem_p.add_argument("--url", required=True)
    idem_p.add_argument("--method", default="POST")
    idem_p.add_argument("--payload", default="{}")
    idem_p.add_argument("--key", default="apiverdict-replay-1")
    idem_p.add_argument("--key-header", default="Idempotency-Key")
    idem_p.add_argument("--verify-cmd")
    idem_p.add_argument("--verify-url")
    idem_p.add_argument("--verify-header", action="append", default=[],
                        help="header 'Name: value' for verify reads only "
                             "(repeatable; defaults to the check headers)")
    idem_p.add_argument("--expect-delta", type=int, default=1,
                        help="expected state delta across the replay (default 1)")
    idem_p.add_argument("--compare-responses", action="store_true",
                        help="also require the replay response to equal the "
                             "original response (opt-in; delta is the default)")
    idem_p.add_argument("--ignore-field", action="append", default=[],
                        help="top-level response field to ignore when "
                             "comparing replay responses (repeatable, e.g. "
                             "a timestamp or request id)")

    idemv_p = sub.add_parser("idempotency-variants",
                             help="same key, same payload, then same key with "
                                  "a different payload")
    idemv_p.add_argument("--url", required=True)
    idemv_p.add_argument("--method", default="POST")
    idemv_p.add_argument("--payload", default="{}")
    idemv_p.add_argument("--alt-payload", default="{}")
    idemv_p.add_argument("--key", default="apiverdict-replay-1")
    idemv_p.add_argument("--key-header", default="Idempotency-Key")
    idemv_p.add_argument("--verify-cmd")
    idemv_p.add_argument("--verify-url")
    idemv_p.add_argument("--verify-header", action="append", default=[],
                         help="header 'Name: value' for verify reads only "
                              "(repeatable; defaults to the check headers)")
    idemv_p.add_argument("--allow-remote", action="store_true",
                        help="allow this mutating check against "
                             "non-localhost URLs (staging/test data only)")
    idemv_p.add_argument("--json", action="store_true")
    _add_auth_args(idemv_p)
    _add_timeout(idemv_p)
    _add_junit(idemv_p)
    idem_p.add_argument("--allow-remote", action="store_true",
                        help="required for non-localhost targets (mutating check)")
    idem_p.add_argument("--json", action="store_true")
    _add_auth_args(idem_p)
    _add_timeout(idem_p)
    _add_junit(idem_p)

    scope_p = sub.add_parser("idempotency-scope",
                               help="same key to two endpoints; B must create, never replay A")
    scope_p.add_argument("--url-a", required=True)
    scope_p.add_argument("--url-b", required=True)
    scope_p.add_argument("--method", default="POST")
    scope_p.add_argument("--payload-a", default="{}")
    scope_p.add_argument("--payload-b", default="{}")
    scope_p.add_argument("--key", default="apiverdict-scope-1")
    scope_p.add_argument("--key-header", default="Idempotency-Key")
    scope_p.add_argument("--verify-cmd-a")
    scope_p.add_argument("--verify-url-a")
    scope_p.add_argument("--verify-cmd-b")
    scope_p.add_argument("--verify-url-b")
    scope_p.add_argument("--verify-header", action="append", default=[],
                         help="header 'Name: value' for verify reads only "
                              "(repeatable; defaults to the check headers)")
    scope_p.add_argument("--allow-remote", action="store_true",
                         help="required for non-localhost targets (mutating check)")
    scope_p.add_argument("--json", action="store_true")
    _add_auth_args(scope_p)
    _add_timeout(scope_p)
    _add_junit(scope_p)

    web_p = sub.add_parser("webhook", help="webhook handler check")
    web_p.add_argument("--url", required=True)
    web_p.add_argument("--secret", help="webhook signing secret (never printed; "
                       "prefer --secret-env: a --secret value is visible in "
                       "shell history and process lists)")
    web_p.add_argument("--secret-env", help="env var holding the webhook secret")
    web_p.add_argument("--event-id", default="evt-apiverdict-1",
                         help="event id (default evt-apiverdict-1)")
    web_p.add_argument("--body", default=None,
                       help="JSON event body (default: "
                            '{"type": "test.event", "amount": 100})')
    web_p.add_argument("--scheme", choices=["apiverdict", "stripe", "github"],
                       default="apiverdict",
                       help="signing scheme (default apiverdict; stripe/github "
                            "for real-handler-compatible signatures)")
    web_p.add_argument("--verify-cmd")
    web_p.add_argument("--verify-url")
    web_p.add_argument("--verify-header", action="append", default=[],
                       help="header 'Name: value' for verify reads only "
                            "(repeatable; defaults to the check headers)")
    web_p.add_argument("--timestamp-tolerance", type=int, default=300,
                       help="seconds of timestamp tolerance the handler is "
                            "expected to enforce (default 300; stale/future "
                            "probes go tolerance + 600s out)")
    web_p.add_argument("--no-enforce-timestamp", action="store_true",
                       help="stale/future probes still run, but their "
                            "acceptance is not judged (for handlers that "
                            "deliberately do not enforce timestamps)")
    web_p.add_argument("--allow-remote", action="store_true",
                        help="required for non-localhost targets (mutating check)")
    web_p.add_argument("--json", action="store_true")
    _add_auth_args(web_p)
    _add_timeout(web_p)
    _add_junit(web_p)

    leak_p = sub.add_parser("error-leak", help="error bodies must not leak internals")
    leak_p.add_argument("--endpoint", action="append", required=True)
    leak_p.add_argument("--invalid-payload", default='{"__invalid__": true}')
    leak_p.add_argument("--json", action="store_true")
    _add_auth_args(leak_p)
    _add_timeout(leak_p)
    _add_junit(leak_p)

    ifmatch_p = sub.add_parser("if-match", help="If-Match optimistic-concurrency check")
    ifmatch_p.add_argument("--read-url", required=True)
    ifmatch_p.add_argument("--write-url")
    ifmatch_p.add_argument("--write-method", default="PATCH")
    ifmatch_p.add_argument("--write-payload", default="{}")
    ifmatch_p.add_argument("--allow-remote", action="store_true",
                           help="allow this mutating check against "
                                "non-localhost URLs (staging/test data only)")
    ifmatch_p.add_argument("--json", action="store_true")
    _add_auth_args(ifmatch_p)
    _add_timeout(ifmatch_p)
    _add_junit(ifmatch_p)

    etag_p = sub.add_parser("cache-etag", help="ETag conditional-request check")
    etag_p.add_argument("--read-url", required=True)
    etag_p.add_argument("--write-url")
    etag_p.add_argument("--write-method", default="POST")
    etag_p.add_argument("--write-payload", default="{}")
    etag_p.add_argument("--allow-remote", action="store_true",
                        help="allow this mutating check against "
                             "non-localhost URLs (staging/test data only)")
    etag_p.add_argument("--json", action="store_true")
    _add_auth_args(etag_p)
    _add_timeout(etag_p)
    _add_junit(etag_p)

    cache_p = sub.add_parser("cache", help="stale-after-write check")
    cache_p.add_argument("--read-url", required=True)
    cache_p.add_argument("--write-url")
    cache_p.add_argument("--write-method", default="POST")
    cache_p.add_argument("--write-payload", default="{}")
    cache_p.add_argument("--compare-field")
    cache_p.add_argument("--allow-remote", action="store_true",
                        help="required for non-localhost write targets")
    cache_p.add_argument("--json", action="store_true")
    _add_auth_args(cache_p)
    _add_timeout(cache_p)
    _add_junit(cache_p)

    oa_p = sub.add_parser("openapi", help="generate + run checks from a JSON OpenAPI spec")
    oa_p.add_argument("--spec", required=True, help="JSON OpenAPI spec file")
    oa_p.add_argument("--base-url", help="base URL; defaults to the spec's first server URL")
    oa_p.add_argument("--page-size", type=int, default=10)
    oa_p.add_argument("--json", action="store_true")
    oa_p.add_argument("--plan-only", action="store_true",
                      help="print the generated check plan and exit 0 without running anything")
    oa_p.add_argument("--junit", help="write JUnit XML report to this file")
    _add_auth_args(oa_p)
    _add_timeout(oa_p)

    head_p = sub.add_parser("head", help="HEAD vs GET consistency for the same URLs")
    head_p.add_argument("--url", action="append", required=True,
                        help="URL to compare (repeatable)")
    head_p.add_argument("--json", action="store_true")
    _add_auth_args(head_p)
    _add_timeout(head_p)
    _add_junit(head_p)

    cors_p = sub.add_parser("cors", help="CORS preflight check (browser-facing APIs only)")
    cors_p.add_argument("--url", action="append", required=True,
                        help="URL to preflight (repeatable)")
    cors_p.add_argument("--method", default="POST",
                        help="method the browser page wants to use")
    cors_p.add_argument("--origin", default="https://example.com",
                        help="Origin to preflight from")
    cors_p.add_argument("--json", action="store_true")
    _add_auth_args(cors_p)
    _add_timeout(cors_p)
    _add_junit(cors_p)

    del_p = sub.add_parser("delete-replay", help="DELETE twice; replay must not change state again")
    del_p.add_argument("--url", required=True, help="resource URL to DELETE twice")
    del_p.add_argument("--verify-cmd")
    del_p.add_argument("--verify-url")
    del_p.add_argument("--verify-header", action="append", default=[],
                       help="header 'Name: value' for verify reads only "
                            "(repeatable; defaults to the check headers)")
    del_p.add_argument("--allow-remote", action="store_true",
                       help="required for non-localhost targets (mutating check)")
    del_p.add_argument("--json", action="store_true")
    _add_auth_args(del_p)
    _add_timeout(del_p)
    _add_junit(del_p)

    patch_p = sub.add_parser("patch-preserves", help="PATCH one field; only that field may change")
    patch_p.add_argument("--url", required=True, help="resource URL (GET + PATCH)")
    patch_p.add_argument("--field", required=True, help="field to PATCH")
    patch_p.add_argument("--value", required=True,
                         help="new value for the field (JSON; falls back to raw string)")
    patch_p.add_argument("--allow-remote", action="store_true",
                         help="required for non-localhost targets (mutating check)")
    patch_p.add_argument("--json", action="store_true")
    _add_auth_args(patch_p)
    _add_timeout(patch_p)
    _add_junit(patch_p)

    det_p = sub.add_parser("detail-superset", help="detail response must cover its list row")
    det_p.add_argument("--list-url", required=True)
    det_p.add_argument("--detail-url", required=True,
                       help="detail URL template with {id} for the row id")
    det_p.add_argument("--id-field", default="id")
    det_p.add_argument("--json", action="store_true")
    _add_auth_args(det_p)
    _add_timeout(det_p)
    _add_junit(det_p)

    mg_p = sub.add_parser("method-guards", help="declared-rejected methods must 405 with Allow")
    mg_p.add_argument("--endpoint", action="append", required=True,
                      help="METHOD:url to probe (repeatable); only probe "
                           "methods you expect rejected, on test doubles first")
    mg_p.add_argument("--allow-remote", action="store_true",
                      help="required for non-localhost mutating-method probes")
    mg_p.add_argument("--json", action="store_true")
    _add_auth_args(mg_p)
    _add_timeout(mg_p)
    _add_junit(mg_p)

    cp_p = sub.add_parser("cache-privacy", help="same URL as two callers must differ (user-specific endpoints)")
    cp_p.add_argument("--url", required=True)
    cp_p.add_argument("--header-a", action="append", default=[],
                      help="header 'Name: value' for caller A (repeatable)")
    cp_p.add_argument("--header-b", action="append", default=[],
                      help="header 'Name: value' for caller B (repeatable)")
    cp_p.add_argument("--ignore-field", action="append", default=[],
                      help="top-level response field to ignore when "
                           "comparing (repeatable)")
    cp_p.add_argument("--json", action="store_true")
    _add_timeout(cp_p)
    _add_junit(cp_p)

    as_p = sub.add_parser("auth-schemes", help="token must work under its own scheme only")
    as_p.add_argument("--url", required=True)
    as_p.add_argument("--token", help="credential (visible in shell history; prefer --token-env)")
    as_p.add_argument("--token-env", help="env var holding the credential")
    as_p.add_argument("--scheme", default="Bearer", help="correct scheme (default Bearer)")
    as_p.add_argument("--json", action="store_true")
    _add_timeout(as_p)
    _add_junit(as_p)

    rd_p = sub.add_parser("redirect", help="redirect chain must be finite, acyclic, https-preserving")
    rd_p.add_argument("--url", required=True)
    rd_p.add_argument("--max-hops", type=int, default=10)
    rd_p.add_argument("--json", action="store_true")
    _add_timeout(rd_p)
    _add_junit(rd_p)

    filt_p = sub.add_parser("filter", help="every row of a filtered list must satisfy the filter")
    filt_p.add_argument("--url", required=True)
    filt_p.add_argument("--field", required=True)
    filt_p.add_argument("--value", required=True)
    filt_p.add_argument("--page-size", type=int, default=10)
    filt_p.add_argument("--style", default="auto",
                        choices=["auto", "offset", "page", "cursor", "link"])
    filt_p.add_argument("--id-field", default="id")
    filt_p.add_argument("--max-pages", type=int, default=100)
    filt_p.add_argument("--json", action="store_true")
    _add_auth_args(filt_p)
    _add_timeout(filt_p)
    _add_junit(filt_p)
    _add_page_params(filt_p)

    spec_p = sub.add_parser("spec-check", help="live GET responses vs OpenAPI success schemas (200 or 2XX)")
    spec_p.add_argument("--spec", required=True, help="JSON OpenAPI spec file")
    spec_p.add_argument("--base-url", help="base URL; defaults to the spec's first server URL")
    spec_p.add_argument("--json", action="store_true")
    _add_auth_args(spec_p)
    _add_timeout(spec_p)
    _add_junit(spec_p)

    sperr_p = sub.add_parser("spec-errors",
                             help="static: every operation in a JSON OpenAPI spec documents an error response")
    sperr_p.add_argument("--spec", required=True, help="JSON OpenAPI spec file")
    sperr_p.add_argument("--json", action="store_true")
    _add_junit(sperr_p)

    sdiff_p = sub.add_parser("spec-diff",
                             help="breaking changes between two JSON OpenAPI specs")
    sdiff_p.add_argument("--old-spec", required=True,
                         help="JSON file for the old (deployed) spec")
    sdiff_p.add_argument("--new-spec", required=True,
                         help="JSON file for the new (proposed) spec")
    sdiff_p.add_argument("--waiver-file",
                         help="JSON list of {rule, location} waivers "
                         "(exact match; waived breaks stay in evidence)")
    sdiff_p.add_argument("--json", action="store_true")
    _add_junit(sdiff_p)

    sex_p = sub.add_parser("spec-examples",
                           help="static: a spec's own examples must match its schemas")
    sex_p.add_argument("--spec", required=True, help="JSON OpenAPI spec file")
    sex_p.add_argument("--json", action="store_true")
    _add_junit(sex_p)

    suite_p = sub.add_parser("suite",
                             help="run all checks listed in a JSON suite config, one report")
    suite_p.add_argument("--config", required=True, help="suite config JSON file")
    suite_p.add_argument("--dry-run", action="store_true",
                       help="validate the config and list the checks that "
                            "would run, without running anything (prints "
                            "check names only, never arguments, so a "
                            "resolved secret cannot leak into output)")
    suite_p.add_argument("--parallel", action="store_true",
                         help="run checks concurrently (output order is still config order)")
    suite_p.add_argument("--json", action="store_true")
    suite_p.add_argument("--junit", help="write JUnit XML report to this file")

    cc_p = sub.add_parser("config-check",
                          help="validate a config file and its auth wiring without running checks")
    cc_p.add_argument("--file", required=True, help="config file (JSON or TOML)")
    cc_p.add_argument("--env", help="environment name; defaults to the config default")

    fix_p = sub.add_parser("fixture", help="run the bundled fixture server")
    fix_p.add_argument("--mode", choices=["naive", "fixed"], default="naive")
    fix_p.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    _validate_args(args)

    if args.command == "config-check":
        try:
            config = load_config(args.file)
            env = get_environment(config, args.env)
            headers = resolve_headers(env)
        except ValueError as exc:
            print(f"apiverdict: config error: {exc}", file=sys.stderr)
            return 2
        envs = config.get("environments") or {}
        print(f"config OK: {args.file}")
        if isinstance(envs, dict) and envs:
            print(f"environments: {', '.join(sorted(envs))}")
        print(f"environment: {env.get('name', args.env or 'default')}")
        if env.get("base_url"):
            print(f"base_url: {env['base_url']}")
        ev = auth_evidence(env)
        if ev.get("auth") == "none":
            print("auth: none")
        else:
            print(f"auth: {ev['auth']} via env var {ev['auth_env_var']} "
                  f"(header {ev['auth_header']} resolves)")
        # Header NAMES only — values never printed (resolve_headers
        # returns live secrets; redact_headers exists for evidence,
        # but names alone prove the wiring).
        print(f"headers: {', '.join(sorted(headers)) or '(none)'}")
        return 0

    if args.command == "fixture":
        try:
            serve_forever(args.mode, port=args.port)
        except OSError as exc:
            # A busy port used to die with a socket traceback;
            # a fixture that cannot bind is a setup problem (exit
            # 2), not a crash.
            print(f"apiverdict: cannot bind fixture port {args.port}: {exc}",
                  file=sys.stderr)
            return 2
        return 0

    if args.command == "demo":
        modes = ["naive", "fixed"] if args.mode == "both" else [args.mode]
        code = 0
        junit_reports = []
        mode_exits = {}
        for mode in modes:
            print(f"--- demo: fixture mode = {mode} ---")
            report = run_demo(mode)
            print_report(report, args.json)
            print()
            junit_reports.append((f"apiverdict-demo-{mode}", report))
            mode_exits[mode] = report["exit_code"]
        code = (_both_code(mode_exits["naive"], mode_exits["fixed"])
                if args.mode == "both" else mode_exits[modes[0]])
        if args.junit:
            try:
                with open(args.junit, "w", encoding="utf-8") as fh:
                    fh.write(reports_to_junit(junit_reports))
            except OSError as exc:
                print(f"apiverdict: could not write junit file {args.junit}: {exc}",
                      file=sys.stderr)
        return code

    if args.command == "casestudy":
        modes = ["naive", "fixed"] if args.mode == "both" else [args.mode]
        code = 0
        junit_reports = []
        mode_exits = {}
        for mode in modes:
            print(f"--- casestudy: fixture mode = {mode} ---")
            report = run_casestudy(mode)
            print_report(report, args.json)
            print()
            junit_reports.append((f"apiverdict-casestudy-{mode}", report))
            mode_exits[mode] = report["exit_code"]
        code = (_both_code(mode_exits["naive"], mode_exits["fixed"])
                if args.mode == "both" else mode_exits[modes[0]])
        if args.junit:
            try:
                with open(args.junit, "w", encoding="utf-8") as fh:
                    fh.write(reports_to_junit(junit_reports))
            except OSError as exc:
                print(f"apiverdict: could not write junit file {args.junit}: {exc}",
                      file=sys.stderr)
        return code

    if args.command == "library":
        modes = ["naive", "fixed"] if args.mode == "both" else [args.mode]
        code = 0
        junit_reports = []
        mode_exits = {}
        for mode in modes:
            print(f"--- library: fixture mode = {mode} ---")
            report = run_library(mode)
            print_report(report, args.json)
            print()
            junit_reports.append((f"apiverdict-library-{mode}", report))
            mode_exits[mode] = report["exit_code"]
        code = (_both_code(mode_exits["naive"], mode_exits["fixed"])
                if args.mode == "both" else mode_exits[modes[0]])
        if args.junit:
            try:
                with open(args.junit, "w", encoding="utf-8") as fh:
                    fh.write(reports_to_junit(junit_reports))
            except OSError as exc:
                print(f"apiverdict: could not write junit file {args.junit}: {exc}",
                      file=sys.stderr)
        return code

    if args.command == "list":
        for name in CHECKS:
            print(f"{name}: {CHECK_DESCRIPTIONS.get(name, '')}")
        # Static commands are not live checks and are not counted
        # in the 27 above, but a user who only runs `list` should
        # still discover them.
        print("\nStatic commands (no live requests): spec-errors, "
              "spec-diff, spec-examples, openapi "
              "--plan-only, config-check")
        return 0

    if args.command == "check":
        headers, _ev = _auth_for(args)
        base_url = args.base_url.rstrip("/")
        # --only is validated BEFORE any check runs, and only the
        # wanted checks run. The old code ran the whole trio (burst
        # included — a mutating check) and filtered afterwards, so
        # `check --only pagination` still fired burst writes against
        # a real API, and an unknown --only name still ran checks
        # before erroring. Both are fixed here.
        wanted = None
        if args.only:
            wanted = {s.strip() for s in args.only.split(",") if s.strip()}
            unknown = wanted - {"burst", "pagination", "error-shape"}
            if unknown:
                print(f"apiverdict: unknown check(s) for --only: "
                      f"{sorted(unknown)} (check runs burst, pagination, "
                      "error-shape)", file=sys.stderr)
                return 2
            if not wanted:
                # "--only ' , '" parses to an empty set, which used
                # to run zero checks (now INCONCLUSIVE via the
                # empty-verdict rule, but with no explanation).
                print("apiverdict: --only names no checks; give at "
                      "least one of burst, pagination, error-shape",
                      file=sys.stderr)
                return 2
        if wanted is None:
            run_fn = run_all_checks_parallel if args.parallel else run_all_checks
            verdicts = run_fn(base_url, headers=headers, timeout=args.timeout)
        else:
            trio = [
                ("burst", lambda: run_burst_check(
                    url=f"{base_url}/api/book",
                    payload={"slot": "demo-slot-{round}"},
                    headers=headers, n=10, rounds=2,
                    verify_url=f"{base_url}/api/count?slot=demo-slot-{{round}}",
                    expect_delta=1, timeout=args.timeout,
                    verify_headers=headers)),
                ("pagination", lambda: run_pagination_check(
                    url=f"{base_url}/api/items", page_size=10,
                    headers=headers, timeout=args.timeout)),
                ("error-shape", lambda: run_error_shape_check(
                    targets=[
                        {"method": "POST", "url": f"{base_url}/api/book"},
                        {"method": "POST", "url": f"{base_url}/api/order"},
                    ],
                    invalid_payload={"__invalid__": True},
                    headers=headers, timeout=args.timeout)),
            ]
            selected = [(n, fn) for n, fn in trio if n in wanted]
            if args.parallel:
                verdicts = run_parallel(selected)
            else:
                verdicts = [time_verdict(fn) for _n, fn in selected]
        report = build_report(verdicts)
        print_report(report, args.json)
        _write_junit(report, args.junit)
        return report["exit_code"]

    if args.command == "pagination-concurrent":
        headers, _ev = _auth_for(args)
        try:
            payload = json.loads(args.write_payload)
        except ValueError:
            payload = args.write_payload
        return _report(run_pagination_concurrent_check(
            url=args.url, page_size=args.page_size, style=args.style,
            id_field=args.id_field, page_param=args.page_param,
            size_param=args.size_param, offset_param=args.offset_param,
            limit_param=args.limit_param, cursor_param=args.cursor_param,
            write_url=args.write_url,
            write_method=args.write_method, write_payload=payload,
            write_after_page=args.write_after_page, headers=headers, timeout=args.timeout,
            max_pages=args.max_pages, allow_remote=args.allow_remote),
            args.json, getattr(args, "junit", None))

    if args.command == "pagination-consistency":
        headers, _ev = _auth_for(args)
        return _report(run_pagination_consistency_check(
            url=args.url, page_size_a=args.page_size_a,
            page_size_b=args.page_size_b, style=args.style,
            id_field=args.id_field, headers=headers,
            page_param=args.page_param, size_param=args.size_param,
            offset_param=args.offset_param, limit_param=args.limit_param,
            cursor_param=args.cursor_param,
            timeout=args.timeout, max_pages=args.max_pages), args.json, getattr(args, "junit", None))

    if args.command == "burst":
        headers, _ev = _auth_for(args)
        if not is_localhost(args.url) and args.allow_remote:
            print(LOCALHOST_BANNER)
        try:
            payload = json.loads(args.payload)
        except ValueError:
            payload = args.payload
        verdict = run_burst_check(
            url=args.url, method=args.method, payload=payload,
            headers=headers, n=args.n, rounds=args.rounds,
            verify_cmd=args.verify_cmd, verify_url=args.verify_url,
            expect_delta=args.expect_delta, allow_remote=args.allow_remote, timeout=args.timeout,
            verify_headers=_verify_headers_for(args),
            idempotency_key=args.idempotency_key,
            idempotency_header=args.idempotency_header,
            compare_responses=args.compare_responses,
            ignore_fields=args.ignore_field,
        )
        return _report(verdict, args.json, getattr(args, "junit", None))

    if args.command == "pagination":
        headers, _ev = _auth_for(args)
        verdict = run_pagination_check(
            url=args.url, page_size=args.page_size, style=args.style,
            id_field=args.id_field, headers=headers, timeout=args.timeout,
            max_pages=args.max_pages, page_param=args.page_param,
            size_param=args.size_param, offset_param=args.offset_param,
            limit_param=args.limit_param, cursor_param=args.cursor_param)
        return _report(verdict, args.json, getattr(args, "junit", None))

    if args.command == "errors":
        headers, _ev = _auth_for(args)
        try:
            invalid = json.loads(args.invalid_payload)
        except ValueError:
            invalid = args.invalid_payload
        verdict = run_error_shape_check(
            targets=[_parse_endpoint(e) for e in args.endpoint],
            invalid_payload=invalid, headers=headers,
            require_rfc7807=args.require_rfc7807,
            require_rfc9457_strict=args.require_rfc9457, timeout=args.timeout)
        return _report(verdict, args.json, getattr(args, "junit", None))

    if args.command == "error-variants":
        headers, _ev = _auth_for(args)
        try:
            payloads = [json.loads(raw) for raw in (args.payload or [])] \
                if args.payload else None
        except ValueError:
            print("apiverdict: --payload must be valid JSON", file=sys.stderr)
            return 2
        return _report(run_error_variants_check(
            url=args.url, method=args.method, payloads=payloads,
            headers=headers, timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "auth-matrix":
        try:
            roles = _load_json_arg(args.roles, "roles")
            targets = _load_json_arg(args.targets, "targets")
        except ValueError as exc:
            print(f"apiverdict: {exc}", file=sys.stderr)
            return 2
        return _report(run_auth_matrix_check(roles=roles, targets=targets, timeout=args.timeout, allow_remote=args.allow_remote), args.json, getattr(args, "junit", None))

    if args.command == "rate-limit":
        headers, _ev = _auth_for(args)
        try:
            payload = json.loads(args.payload) if args.payload else None
        except ValueError:
            payload = args.payload
        return _report(run_rate_limit_check(
            url=args.url, method=args.method, payload=payload, headers=headers,
            requests=args.requests, expect_limit=args.expect_limit, timeout=args.timeout,
            allow_remote=args.allow_remote), args.json, getattr(args, "junit", None))

    if args.command == "idempotency":
        headers, _ev = _auth_for(args)
        try:
            payload = json.loads(args.payload)
        except ValueError:
            payload = args.payload
        return _report(run_idempotency_check(
            url=args.url, method=args.method, payload=payload, headers=headers,
            idempotency_key=args.key, idempotency_header=args.key_header,
            verify_cmd=args.verify_cmd, verify_url=args.verify_url,
            expect_delta=args.expect_delta,
            verify_headers=_verify_headers_for(args),
            compare_responses=args.compare_responses,
            ignore_fields=args.ignore_field,
            allow_remote=args.allow_remote, timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "idempotency-variants":
        headers, _ev = _auth_for(args)
        try:
            payload = json.loads(args.payload)
        except ValueError:
            payload = args.payload
        try:
            alt = json.loads(args.alt_payload)
        except ValueError:
            alt = args.alt_payload
        return _report(run_idempotency_variants_check(
            url=args.url, method=args.method, payload=payload,
            alt_payload=alt, headers=headers,
            idempotency_key=args.key, idempotency_header=args.key_header,
            verify_cmd=args.verify_cmd, verify_url=args.verify_url,
            verify_headers=_verify_headers_for(args),
            allow_remote=args.allow_remote, timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "idempotency-scope":
        headers, _ev = _auth_for(args)
        try:
            payload_a = json.loads(args.payload_a)
        except ValueError:
            payload_a = args.payload_a
        try:
            payload_b = json.loads(args.payload_b)
        except ValueError:
            payload_b = args.payload_b
        return _report(run_idempotency_scope_check(
            url_a=args.url_a, url_b=args.url_b, method=args.method,
            payload_a=payload_a, payload_b=payload_b, headers=headers,
            idempotency_key=args.key, idempotency_header=args.key_header,
            verify_cmd_a=args.verify_cmd_a, verify_url_a=args.verify_url_a,
            verify_cmd_b=args.verify_cmd_b, verify_url_b=args.verify_url_b,
            verify_headers=_verify_headers_for(args),
            allow_remote=args.allow_remote, timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "webhook":
        headers, _ev = _auth_for(args)
        try:
            body = json.loads(args.body) if args.body else None
        except ValueError:
            body = args.body
        if args.secret and args.secret_env:
            print("apiverdict: supply exactly one of --secret and "
                  "--secret-env", file=sys.stderr)
            return 2
        secret = args.secret
        if args.secret_env:
            secret = os.environ.get(args.secret_env)
            if not secret:
                print(f"apiverdict: webhook secret env var {args.secret_env!r} "
                      "is not set", file=sys.stderr)
                return 2
        if not secret:
            print("apiverdict: webhook needs --secret or --secret-env",
                  file=sys.stderr)
            return 2
        return _report(run_webhook_check(
            url=args.url, secret=secret, event_id=args.event_id,
            body=body,
            verify_cmd=args.verify_cmd, verify_url=args.verify_url,
            verify_headers=_verify_headers_for(args),
            headers=headers, allow_remote=args.allow_remote, timeout=args.timeout,
            scheme=args.scheme,
            enforce_timestamp=not args.no_enforce_timestamp,
            timestamp_tolerance_s=args.timestamp_tolerance), args.json, getattr(args, "junit", None))

    if args.command == "error-leak":
        headers, _ev = _auth_for(args)
        try:
            invalid = json.loads(args.invalid_payload)
        except ValueError:
            invalid = args.invalid_payload
        return _report(run_error_leak_check(
            targets=[_parse_endpoint(e) for e in args.endpoint],
            invalid_payload=invalid, headers=headers, timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "if-match":
        headers, _ev = _auth_for(args)
        try:
            payload = json.loads(args.write_payload)
        except ValueError:
            payload = args.write_payload
        return _report(run_if_match_check(
            read_url=args.read_url, write_method=args.write_method,
            write_url=args.write_url, write_payload=payload,
            headers=headers, allow_remote=args.allow_remote, timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "cache-etag":
        headers, _ev = _auth_for(args)
        try:
            payload = json.loads(args.write_payload)
        except ValueError:
            payload = args.write_payload
        return _report(run_cache_etag_check(
            read_url=args.read_url, write_method=args.write_method,
            write_url=args.write_url, write_payload=payload,
            headers=headers, allow_remote=args.allow_remote, timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "cache":
        headers, _ev = _auth_for(args)
        try:
            payload = json.loads(args.write_payload)
        except ValueError:
            payload = args.write_payload
        return _report(run_cache_check(
            read_url=args.read_url, write_method=args.write_method,
            write_url=args.write_url, write_payload=payload,
            headers=headers, compare_field=args.compare_field,
            allow_remote=args.allow_remote, timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "suite":
        if args.dry_run:
            try:
                callables = load_suite(args.config)
            except ValueError as exc:
                print(f"apiverdict: {exc}", file=sys.stderr)
                return 2
            for name, _fn in callables:
                print(name)
            return 0
        try:
            verdicts = run_suite(args.config, parallel=args.parallel)
        except ValueError as exc:
            print(f"apiverdict: {exc}", file=sys.stderr)
            return 2
        report = build_report(verdicts)
        print_report(report, args.json)
        _write_junit(report, args.junit)
        return report["exit_code"]

    if args.command == "spec-errors":
        try:
            spec = load_spec(args.spec)
        except ValueError as exc:
            print(f"apiverdict: {exc}", file=sys.stderr)
            return 2
        return _report(run_spec_errors_check(spec), args.json, getattr(args, "junit", None))

    if args.command == "spec-diff":
        try:
            old_spec = load_spec(args.old_spec)
            new_spec = load_spec(args.new_spec)
            waivers = None
            if args.waiver_file:
                import json as _json
                with open(args.waiver_file, encoding="utf-8") as fh:
                    waivers = _json.load(fh)
                if not isinstance(waivers, list):
                    raise ValueError("waiver file must hold a JSON list")
        except ValueError as exc:
            print(f"apiverdict: {exc}", file=sys.stderr)
            return 2
        except (OSError, Exception) as exc:
            print(f"apiverdict: cannot read waiver file: {exc}",
                  file=sys.stderr)
            return 2
        return _report(run_spec_diff_check(old_spec, new_spec, waivers),
                       args.json, getattr(args, "junit", None))

    if args.command == "spec-examples":
        try:
            spec = load_spec(args.spec)
        except ValueError as exc:
            print(f"apiverdict: {exc}", file=sys.stderr)
            return 2
        return _report(run_spec_examples_check(spec), args.json, getattr(args, "junit", None))

    if args.command == "auth-schemes":
        import os as _os
        if args.token and args.token_env:
            print("apiverdict: supply exactly one of --token and "
                  "--token-env", file=sys.stderr)
            return 2
        token = _os.environ.get(args.token_env) if args.token_env else args.token
        if not token:
            print("apiverdict: --token or --token-env is required",
                  file=sys.stderr)
            return 2
        return _report(run_auth_schemes_check(
            url=args.url, token=token, scheme=args.scheme,
            timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "redirect":
        return _report(run_redirect_check(
            url=args.url, max_hops=args.max_hops,
            timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "filter":
        headers, _evidence = _auth_for(args)
        return _report(run_filter_check(
            url=args.url, field=args.field, value=args.value,
            page_size=args.page_size, style=args.style,
            max_pages=args.max_pages, id_field=args.id_field,
            headers=headers, timeout=args.timeout,
            page_param=args.page_param, size_param=args.size_param,
            offset_param=args.offset_param, limit_param=args.limit_param,
            cursor_param=args.cursor_param),
            args.json, getattr(args, "junit", None))

    if args.command == "cache-privacy":
        return _report(run_cache_privacy_check(
            url=args.url,
            headers_a=_parse_headers(args.header_a),
            headers_b=_parse_headers(args.header_b),
            ignore_fields=args.ignore_field,
            timeout=args.timeout),
            args.json, getattr(args, "junit", None))

    if args.command == "method-guards":
        headers, _ev = _auth_for(args)
        targets = []
        for raw in args.endpoint:
            parsed = _parse_endpoint(raw)
            targets.append({"method": parsed["method"], "url": parsed["url"]})
        return _report(run_method_guards_check(
            targets=targets, headers=headers,
            allow_remote=args.allow_remote, timeout=args.timeout),
            args.json, getattr(args, "junit", None))

    if args.command == "detail-superset":
        headers, _ev = _auth_for(args)
        return _report(run_detail_superset_check(
            list_url=args.list_url, detail_url=args.detail_url,
            id_field=args.id_field, headers=headers,
            timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "patch-preserves":
        headers, _ev = _auth_for(args)
        try:
            value = json.loads(args.value)
        except ValueError:
            value = args.value
        return _report(run_patch_preserves_check(
            url=args.url, field=args.field, value=value, headers=headers,
            allow_remote=args.allow_remote, timeout=args.timeout),
            args.json, getattr(args, "junit", None))

    if args.command == "delete-replay":
        headers, _ev = _auth_for(args)
        return _report(run_delete_replay_check(
            url=args.url, headers=headers,
            verify_cmd=args.verify_cmd, verify_url=args.verify_url,
            verify_headers=_verify_headers_for(args),
            allow_remote=args.allow_remote, timeout=args.timeout),
            args.json, getattr(args, "junit", None))

    if args.command == "cors":
        headers, _ev = _auth_for(args)
        return _report(run_cors_check(
            urls=args.url, method=args.method, origin=args.origin,
            headers=headers, timeout=args.timeout),
            args.json, getattr(args, "junit", None))

    if args.command == "head":
        headers, _ev = _auth_for(args)
        return _report(run_head_consistency_check(
            urls=args.url, headers=headers,
            timeout=args.timeout), args.json, getattr(args, "junit", None))

    if args.command == "spec-check":
        headers, _ev = _auth_for(args)
        try:
            spec = load_spec(args.spec)
        except ValueError as exc:
            print(f"apiverdict: {exc}", file=sys.stderr)
            return 2
        base_url = args.base_url or spec_base_url(spec)
        if not base_url:
            print("apiverdict: no --base-url and the spec names no server URL",
                  file=sys.stderr)
            return 2
        return _report(run_spec_conformance_check(spec, base_url, headers=headers, timeout=args.timeout),
                       args.json, getattr(args, "junit", None))

    if args.command == "openapi":
        headers, _ev = _auth_for(args)
        try:
            spec = load_spec(args.spec)
        except ValueError as exc:
            print(f"apiverdict: {exc}", file=sys.stderr)
            return 2
        base_url = args.base_url or spec_base_url(spec)
        if not base_url:
            print("apiverdict: no --base-url and the spec names no server URL",
                  file=sys.stderr)
            return 2
        if args.plan_only:
            # Pure spec parsing, no network: what WOULD run, so a
            # user can review generated checks before CI runs them.
            plan = generate_plan(spec, base_url)
            for cand in plan["pagination"]:
                detail = cand["reason"]
                if cand.get("style") and cand["style"] != "auto":
                    detail += f", style {cand['style']}"
                for key in ("page_param", "size_param", "offset_param",
                            "limit_param", "cursor_param"):
                    if cand.get(key):
                        detail += f", {key}={cand[key]}"
                print(f"pagination: GET {cand['url']} ({detail})")
            for cand in plan["error_targets"]:
                print(f"error-shape: {cand['method']} {cand['url']}")
            for cand in plan["burst_candidates"]:
                print(f"burst candidate (not auto-run): {cand['method']} {cand['url']}")
            for cand in plan["detail_candidates"]:
                print(f"detail-superset candidate (not auto-run): "
                      f"{cand['list_url']} -> {cand['detail_url']}")
            if not (plan["pagination"] or plan["error_targets"]):
                print("no runnable checks generated from this spec")
            return 0
        verdicts = run_openapi_checks(spec, base_url, headers=headers,
                                      page_size=args.page_size, timeout=args.timeout)
        report = build_report(verdicts)
        print_report(report, args.json)
        _write_junit(report, args.junit)
        if not args.json:
            # Burst candidates are computed by the plan but never
            # run (a burst needs a verify URL/command from the
            # user — generating one would invent evidence). They
            # used to be invisible in CLI output; list them as
            # information, not verdicts, so --json stays clean.
            full_plan = generate_plan(spec, base_url)
            candidates = full_plan["burst_candidates"]
            if candidates:
                print("\nBurst candidates (not auto-run — supply a "
                      "verify URL/command with `apiverdict burst`):")
                for cand in candidates:
                    print(f"  {cand['method']} {cand['url']}")
            detail_cands = full_plan["detail_candidates"]
            if detail_cands:
                print("\nDetail candidates (not auto-run — run with "
                      "`apiverdict detail-superset`):")
                for cand in detail_cands:
                    print(f"  {cand['list_url']} -> {cand['detail_url']}")
        return report["exit_code"]

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
