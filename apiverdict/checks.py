"""Check registry — the plugin seam for future checks.

Each entry is a callable returning a Verdict. Adding a new check is:
  1. write a module with a run_*_check() function,
  2. register it here, give it its own subcommand, list it in `list`,
     and add it to the suite registry (the `check` command itself is
     the v1 trio wired to fixture-shaped endpoints — it is not the
     registry),
  3. prove it with a failing case — against the shared fixture or a
     throwaway server.
No core file has to change its shape to accept a new check.
"""

from __future__ import annotations

from .auth_matrix import run_auth_matrix_check
from .burst import run_burst_check
from .cache import run_cache_check
from .error_shape import run_error_shape_check
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
from .idempotency import (run_idempotency_check,
                            run_idempotency_scope_check,
                            run_idempotency_variants_check)
from .pagination import run_pagination_check
from .pagination_consistency import run_pagination_consistency_check
from .rate_limit import run_rate_limit_check
from .spec_conformance import run_spec_conformance_check
from .webhook import run_webhook_check

CHECKS = ("burst", "pagination", "pagination-concurrent", "pagination-consistency", "error-shape", "error-variants",
          "error-leak", "auth-matrix", "rate-limit", "idempotency", "idempotency-variants", "idempotency-scope", "webhook",
          "cache", "cache-etag", "spec-conformance", "head-consistency", "cors", "delete-replay", "patch-preserves", "detail-superset", "method-guards", "cache-privacy", "auth-schemes", "redirect", "filter", "if-match")

CHECK_DESCRIPTIONS = {
    "burst": "N identical requests through a barrier gate; final-state delta must be 1",
    "pagination": "walk a list to the end; no duplicates, no gaps, stable order",
    "pagination-concurrent": "walk again with one row written mid-walk; baseline rows must survive exactly once",
    "pagination-consistency": "walk the same list at two page sizes; sequence and total must agree",
    "error-shape": "invalid input to many endpoints; all error shapes must agree",
    "error-variants": "many invalid payload kinds to one endpoint; all error shapes must agree",
    "error-leak": "invalid input to many endpoints; error bodies and headers must not leak tracebacks, paths, or SQL",
    "auth-matrix": "every role x every endpoint; no leaks, no denied-allowed",
    "rate-limit": "sequential requests; limiting must start and stay consistent",
    "idempotency": "same key, same payload, twice sequentially; delta must be 1",
    "idempotency-variants": "same key twice, then same key with a different payload; deltas must be 1 then 0",
    "idempotency-scope": "same key to two different endpoints; B must create, never replay A's result",
    "webhook": "signed valid acked, tampered rejected, duplicate not double-applied, stale rejected",
    "cache": "read, write, read again; second read must reflect the write",
    "cache-etag": "ETag present, If-None-Match gives 304, ETag changes after a write",
    "spec-conformance": "live GET responses vs OpenAPI success schemas (200 or 2XX) (nested, $ref, path params)",
    "head-consistency": "HEAD vs GET for the same URL: status, Content-Type, Content-Length must agree",
    "cors": "OPTIONS preflight: Allow-Origin + Allow-Methods must cover the requested method (browser APIs only)",
    "delete-replay": "DELETE twice: first removes exactly one, replay succeeds or 404/410 with no further state change",
    "patch-preserves": "PATCH one field: only that field may change, all others preserved",
    "detail-superset": "detail response must cover its list row: no dropped or retyped fields",
    "method-guards": "declared-rejected methods must return 405 with an Allow header not listing the method",
    "cache-privacy": "same URL as two callers must not return identical bodies (user-specific endpoints only)",
    "auth-schemes": "a token must work under its own scheme only, never under Basic/Digest/Token/bare",
    "redirect": "a redirect chain must be finite, acyclic, and never downgrade https to http",
    "filter": "every row of a filtered list must satisfy the filter predicate (top-level field)",
    "if-match": "current If-Match must apply, stale If-Match must be rejected without moving state",
}

__all__ = [
    "CHECKS",
    "run_auth_matrix_check",
    "run_burst_check",
    "run_cache_check",
    "run_error_shape_check",
    "run_idempotency_check",
    "run_idempotency_variants_check",
    "run_idempotency_scope_check",
    "run_pagination_check",
    "run_rate_limit_check",
    "run_webhook_check",
    "run_spec_conformance_check",
    "run_head_consistency_check",
    "run_cors_check",
    "run_delete_replay_check",
    "run_patch_preserves_check",
    "run_detail_superset_check",
    "run_method_guards_check",
    "run_cache_privacy_check",
    "run_auth_schemes_check",
    "run_redirect_check",
    "run_filter_check",
]
