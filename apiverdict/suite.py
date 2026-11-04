"""Suite runner — one command, all configured checks, one report.

The single-check commands are for exploring; CI wants one command
that runs everything and fails on any break. A suite config is a
JSON file listing checks in order, each with the kwargs its
run_*_check function takes:

    {"checks": [
      {"check": "burst", "url": "http://127.0.0.1:8765/api/book",
       "payload": {"slot": "suite-{round}"}, "n": 10, "rounds": 2,
       "verify_url": "http://127.0.0.1:8765/api/count?slot=suite-{round}",
       "expect_delta": 1},
      {"check": "pagination", "url": "http://127.0.0.1:8765/api/items",
       "page_size": 10}
    ]}

Rules, all fail-fast at load time (exit 2 from the CLI), never
mid-run:
  * unknown check name -> error listing the valid names;
  * unknown kwarg for that check -> error (checked against the
    function's signature, so a typo like "pageSize" cannot silently
    become a default);
  * spec-conformance takes "spec" (a JSON spec file path, resolved
    relative to the config file) plus "base_url"; spec-errors takes
    "spec" only; spec-diff takes "old_spec" and "new_spec" (both
    resolved the same way). These are the only checks whose first
    arguments are not kwargs.
  * webhook "secret_env" names an environment variable; the secret
    itself must never be written into the config file. A missing
    variable is a load error, not an empty secret.
  * top-level "config" (an apiverdict config file path, resolved
    relative to the suite config) plus optional "env" applies
    that environment's auth headers to every check that accepts
    a "headers" argument, unless the entry sets its own headers
    (entry wins). Secrets still live in environment variables
    via the config file, never in the suite file itself.

Order is config order, in sequential and parallel runs alike
(parallel output is submission order — see runner.run_parallel).
Mutating checks keep their own remote guards; a suite does not
bypass them, and does not add a global --allow-remote: each
mutating entry must carry its own allow_remote flag, so a suite
cannot accidentally arm every write at once.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

from .auth_matrix import run_auth_matrix_check
from .config import get_environment, load_config, resolve_headers
from .burst import run_burst_check
from .cache import run_cache_check
from .cache_etag import run_cache_etag_check
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
from .if_match import run_if_match_check
from .error_leak import run_error_leak_check
from .error_shape import run_error_shape_check
from .error_variants import run_error_variants_check
from .idempotency import (run_idempotency_check,
                            run_idempotency_scope_check,
                            run_idempotency_variants_check)
from .openapi import load_spec
from .pagination import run_pagination_check
from .pagination_concurrent import run_pagination_concurrent_check
from .pagination_consistency import run_pagination_consistency_check
from .rate_limit import run_rate_limit_check
from .runner import Verdict, run_parallel, time_verdict
from .spec_conformance import run_spec_conformance_check
from .spec_diff import run_spec_diff_check
from .spec_examples import run_spec_examples_check
from .spec_errors import run_spec_errors_check
from .webhook import run_webhook_check

SUITE_CHECKS = {
    "burst": run_burst_check,
    "pagination": run_pagination_check,
    "pagination-consistency": run_pagination_consistency_check,
    "pagination-concurrent": run_pagination_concurrent_check,
    "error-shape": run_error_shape_check,
    "error-variants": run_error_variants_check,
    "error-leak": run_error_leak_check,
    "auth-matrix": run_auth_matrix_check,
    "rate-limit": run_rate_limit_check,
    "idempotency": run_idempotency_check,
    "idempotency-variants": run_idempotency_variants_check,
    "idempotency-scope": run_idempotency_scope_check,
    "webhook": run_webhook_check,
    "cache": run_cache_check,
    "cache-etag": run_cache_etag_check,
    "head-consistency": run_head_consistency_check,
    "cors": run_cors_check,
    "delete-replay": run_delete_replay_check,
    "patch-preserves": run_patch_preserves_check,
    "detail-superset": run_detail_superset_check,
    "method-guards": run_method_guards_check,
    "cache-privacy": run_cache_privacy_check,
    "auth-schemes": run_auth_schemes_check,
    "redirect": run_redirect_check,
    "filter": run_filter_check,
    "if-match": run_if_match_check,
}


def _resolve_spec(path: str, config_dir: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return config_dir / candidate


def load_suite(path: str | Path) -> list[tuple[str, callable]]:
    """Load and validate a suite config. Returns (name, callable)
    pairs in config order. Raises ValueError on any config problem."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ValueError(f"suite config not found: {path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"invalid JSON in suite config {path}: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("checks"), list):
        raise ValueError("suite config must be an object with a 'checks' list")
    if not config["checks"]:
        # An empty suite must not report PASS (see runner: an
        # empty verdict list is INCONCLUSIVE). Fail fast at load
        # so the mistake is visible as a config error, exit 2.
        raise ValueError("suite config has no checks; refusing an "
                         "empty suite that would report nothing")
    config_dir = config_path.parent
    suite_headers: dict = {}
    if config.get("config"):
        cfg = load_config(_resolve_spec(config["config"], config_dir))
        env = get_environment(cfg, config.get("env"))
        suite_headers = resolve_headers(env)
    callables: list[tuple[str, callable]] = []
    for index, entry in enumerate(config["checks"]):
        if not isinstance(entry, dict) or "check" not in entry:
            raise ValueError(f"checks[{index}] must be an object with a 'check' name")
        name = entry["check"]
        kwargs = {k: v for k, v in entry.items() if k != "check"}
        if name == "spec-conformance":
            spec_file = kwargs.pop("spec", None)
            if not spec_file:
                raise ValueError(f"checks[{index}] (spec-conformance) needs a 'spec' file")
            spec = load_spec(_resolve_spec(spec_file, config_dir))
            base_url = kwargs.pop("base_url", None)
            if not base_url:
                from .openapi import spec_base_url
                base_url = spec_base_url(spec)
            if not base_url:
                raise ValueError(f"checks[{index}] (spec-conformance) needs a "
                                 "'base_url' or a spec with a server URL")
            if suite_headers and "headers" not in kwargs:
                kwargs["headers"] = suite_headers
            callables.append((name, lambda s=spec, b=base_url, kw=kwargs:
                              run_spec_conformance_check(s, b, **kw)))
            continue
        if name == "spec-diff":
            old_file = kwargs.pop("old_spec", None)
            new_file = kwargs.pop("new_spec", None)
            if not old_file or not new_file:
                raise ValueError(f"checks[{index}] (spec-diff) needs "
                                 "'old_spec' and 'new_spec' files")
            waiver_file = kwargs.pop("waiver_file", None)
            if kwargs:
                raise ValueError(f"checks[{index}] (spec-diff) takes no "
                                 f"extra arguments, got {sorted(kwargs)}")
            old = load_spec(_resolve_spec(old_file, config_dir))
            new = load_spec(_resolve_spec(new_file, config_dir))
            waivers = None
            if waiver_file:
                import json as _json
                with open(_resolve_spec(waiver_file, config_dir),
                          encoding="utf-8") as fh:
                    waivers = _json.load(fh)
                if not isinstance(waivers, list):
                    raise ValueError(f"checks[{index}] (spec-diff) "
                                     "waiver file must hold a JSON list")
            callables.append((name, lambda o=old, n=new, w=waivers:
                              run_spec_diff_check(o, n, w)))
            continue
        if name == "spec-examples":
            spec_file = kwargs.pop("spec", None)
            if not spec_file:
                raise ValueError(f"checks[{index}] (spec-examples) needs a 'spec' file")
            spec = load_spec(_resolve_spec(spec_file, config_dir))
            if kwargs:
                raise ValueError(f"checks[{index}] (spec-examples) takes no "
                                 f"extra arguments, got {sorted(kwargs)}")
            callables.append((name, lambda s=spec: run_spec_examples_check(s)))
            continue
        if name == "spec-errors":
            spec_file = kwargs.pop("spec", None)
            if not spec_file:
                raise ValueError(f"checks[{index}] (spec-errors) needs a 'spec' file")
            spec = load_spec(_resolve_spec(spec_file, config_dir))
            if kwargs:
                raise ValueError(f"checks[{index}] (spec-errors) takes no "
                                 f"extra arguments, got {sorted(kwargs)}")
            callables.append((name, lambda s=spec: run_spec_errors_check(s)))
            continue
        fn = SUITE_CHECKS.get(name)
        if fn is None:
            raise ValueError(f"checks[{index}]: unknown check {name!r} "
                             f"(valid: {', '.join(sorted(SUITE_CHECKS))}, "
                             "spec-conformance, spec-errors, spec-diff, spec-examples)")
        if name == "auth-schemes" and "token_env" in kwargs:
            env_var = kwargs.pop("token_env")
            token = os.environ.get(env_var)
            if not token:
                raise ValueError(f"checks[{index}] (auth-schemes): env var "
                                 f"{env_var!r} is not set")
            kwargs["token"] = token
        if name == "webhook" and "secret_env" in kwargs:
            env_var = kwargs.pop("secret_env")
            secret = os.environ.get(env_var)
            if not secret:
                raise ValueError(f"checks[{index}] (webhook): env var "
                                 f"{env_var!r} is not set")
            kwargs["secret"] = secret
        if suite_headers and "headers" not in kwargs and \
                "headers" in inspect.signature(fn).parameters:
            kwargs["headers"] = suite_headers
        allowed = set(inspect.signature(fn).parameters)
        unknown = sorted(set(kwargs) - allowed)
        if unknown:
            raise ValueError(f"checks[{index}] ({name}): unknown argument(s) "
                             f"{unknown} (valid: {sorted(allowed)})")
        # Fail fast on MISSING required arguments too: without this,
        # a suite entry like {"check": "pagination"} with no url only
        # surfaced as an INCONCLUSIVE at run time (a TypeError inside
        # time_verdict), hiding a config error behind a verdict.
        params = inspect.signature(fn).parameters
        required = [pname for pname, param in params.items()
                    if param.default is inspect.Parameter.empty
                    and param.kind not in (inspect.Parameter.VAR_POSITIONAL,
                                           inspect.Parameter.VAR_KEYWORD)]
        missing = sorted(set(required) - set(kwargs))
        if missing:
            raise ValueError(f"checks[{index}] ({name}): missing required "
                             f"argument(s) {missing}")
        callables.append((name, lambda f=fn, kw=kwargs: f(**kw)))
    if not callables:
        raise ValueError("suite config has no checks")
    return callables


def run_suite(path: str | Path, parallel: bool = False) -> list[Verdict]:
    """Run a suite config, timed, in config order."""
    callables = load_suite(path)
    if parallel:
        return run_parallel(callables)
    verdicts = []
    for name, fn in callables:
        verdict = time_verdict(fn)
        if verdict.check == "unknown":
            verdict = Verdict(name, verdict.status, verdict.summary,
                              verdict.evidence)
        verdicts.append(verdict)
    return verdicts
