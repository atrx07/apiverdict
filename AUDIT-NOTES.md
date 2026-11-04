# Test-suite audit (2026-11-04) — strip of unnecessary tests

Baseline (parent-verified): 467 tests OK in ~63s, version 0.4.0.
After audit: **461 tests OK in ~64s** (see below — the removed tests were
fast unit tests; suite time is dominated by fixture servers and
subprocess CLI tests, so time is essentially unchanged).

Method: every test file was inventoried and read (full reads for all
large/suspicious files; name-plus-body scan for the rest). An
exact-body duplicate scan found zero exact duplicates, so redundancy
was semantic, not copy-paste. Removal rule: delete/merge only with a
concrete category and a named covering test. Where the varying
dimension was the feature under test, the test was KEPT (see below).

## Removal log

| # | File | Removed test | Category | Covered by (remaining test) |
|---|------|--------------|----------|------------------------------|
| 1 | tests/test_spec_diff.py | test_query_operation_removed_is_breaking | (e) near-duplicate variant | test_operation_removed_is_breaking (same test now asserts QUERY and COPY removal) |
| 2 | tests/test_spec_diff.py | test_additional_operation_removed_is_breaking | (e) | test_operation_removed_is_breaking (as above) |
| 3 | tests/test_spec_diff.py | test_query_operation_added_is_not_breaking | (e) | test_operation_added_is_not_breaking (asserts QUERY and COPY addition) |
| 4 | tests/test_spec_diff.py | test_additional_operation_added_is_not_breaking | (e) | test_operation_added_is_not_breaking (as above) |
| 5 | tests/test_spec_errors.py | test_query_operation_without_error_response_fails | (e) | test_nonstandard_operations_without_error_response_fail (one spec lists both QUERY /search and COPY /files as undocumented) |
| 6 | tests/test_spec_errors.py | test_additional_operation_without_error_response_fails | (e) | test_nonstandard_operations_without_error_response_fail (as above) |
| 7 | tests/test_spec_examples.py | test_query_response_example_checked | (e) | test_nonstandard_operation_examples_checked (QUERY violating example + additionalOperations valid example in one test) |
| 8 | tests/test_spec_examples.py | test_additional_operation_example_checked | (e) | test_nonstandard_operation_examples_checked (as above) |
| 9 | tests/test_cursor_aliases.py | TestLimitIgnoredEarlyStop.test_walk_stops_at_first_oversize_page | (c) trivial re-verification | test_oversize_page_fails (now also asserts calls == 1 and pages == 1) |
| 10 | tests/test_link_relative.py (file deleted) | test_relative_link_walk_collects_all_rows | (a) exact duplicate behaviour | test_pagination_link.TestRelativeLinkPagination.test_relative_link_fixed_passes (real fixture endpoint /api/items-link-relative, same 30 rows, PASS) |

Net: 467 -> 461 tests (-6: spec_diff merge saves 2, spec_errors merge
saves 1, spec_examples merge saves 1, cursor merge saves 1, link-file
deletion saves 1; merges keep both asserted behaviours, so coverage
loss is zero by construction). No test file was deleted
except test_link_relative.py (1 test, covered as above).
54 test files -> 53 test files.

## Tests deliberately KEPT despite looking redundant

- Per-check "typo method is INCONCLUSIVE" tests (burst, auth-matrix,
  error-shape, error-variants, idempotency, leak/etag, rate-limit,
  method-guards): method validation lives in each check module, not
  one shared function, and several also assert no network call was
  made. Removing them would lose per-check wiring coverage.
- Per-check remote-guard tests (burst, idempotency, webhook, cache,
  auth-matrix, rate-limit, cache-etag, pagination-concurrent): same
  reasoning — the shared mutating_guard helper is wired per check.
- Check-level INCONCLUSIVE vs CLI exit-2 pairs (e.g. burst GET,
  webhook negative tolerance, cache GET write, pagination bogus
  style): two layers (check guard vs CLI validation), not duplicates.
- Naive/fixed PASS/FAIL pairs everywhere: required proof per check,
  never stripped.
- Pass/fail pairs for opposite directions (server removed vs added,
  type widened vs narrowed, const match vs mismatch, etc.): opposite
  branches, not variants.
- Property-style tests (hardening, redirect random DAG): they cover
  invariants the deterministic cases do not; deterministic cases are
  the readable proofs, properties are the breadth. Neither replaces
  the other.
- Weak-but-merged help tests (webhook --body, idempotency
  --verify-header on 4 commands): each already asserts the flag on
  every relevant command in one test; they guard flag registration
  and were not deleted for being weak.
- test_check_only.test_max_pages_caps_the_walk: lives in the wrong
  file (it is a pagination max-pages test, not a check --only test)
  but tests a distinct branch; kept, misplacement noted, not worth
  a move that would churn the log.

## Flags (not deletions)

- Flaky/network-dependent: test_metadata_guards
  .test_rate_limit_get_remote_not_gated makes a real GET to
  example.com, but its assertion is egress-tolerant (only asserts the
  guard did not fire), so it is not flaky in practice. No change.
- Slow for a reason: test_hardening.test_slow_server_times_out
  (~2s, server sleeps 2s) — that sleep IS the timeout proof. Kept.
- test_cli_ux.test_pagination_concurrent_cli_offset_fails exercises
  the CLI against a dead target, not the fixture (comment in the test
  says so); it proves CLI wiring/exit code only. Kept, flagged as
  wiring-only.
- No tautologies, no tests for removed behaviour, and no TODOs were
  found in tests/ or apiverdict/.

## Production code

Untouched. No test exposed a real bug, so no production fix was made.

## Verification after audit

- python3 -m unittest discover -s tests: 461 tests, OK (~64s)
- python3 -m apiverdict demo: naive FAIL (exit 1) / fixed PASS (exit 0)
- python3 -m apiverdict casestudy: naive FAIL / fixed PASS
- python3 -m apiverdict library: naive FAIL / fixed PASS
- No README/CHANGELOG numeric test count was stated anywhere, so no
  count text needed updating (grep for 467 found nothing).
- V2-NOTES.md was NOT touched (to be trimmed separately before push).
