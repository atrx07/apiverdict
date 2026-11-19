# Improvement run, 2026-11-19 (~1 hour) — 0.4.1, local only, not pushed

Baseline (parent-verified 2026-11-04, commit 2cfa3c6): 0.4.0, 27 live
checks, 461 tests OK in ~64s.

## What changed

- **New live check `idempotency-failed-retry` (28 live checks).**
  Research: Stripe/AWS retry guidance and 2026 idempotency write-ups
  agree a sequential replay test alone is half proof — a server can
  honour a key on a clean retry while still recording it as merely
  "seen" on a failed attempt, so a corrected retry replays the
  stored failure. The check sends an invalid request with key K
  (expect 4xx), then the valid request with the same K (must create
  exactly 1). Invalid input accepted (2xx) or a 5xx on the first
  send is INCONCLUSIVE, not a verdict on poisoned state.
  Fixture: naive mode poisons the key on a failed send; fixed mode
  never records a key on a failed send (stores only on 201).
  Wired into the registry, `list`, the `suite` runner, and a CLI
  subcommand. Docs: README, USER-GUIDE, CHANGELOG.
- **Remediation hints.** Every failed check's plain-text summary
  now carries a one-line next step (`-> ...`). JSON report schema
  unchanged; inconclusive lines carry no hint. Every registered
  check has a hint, locked by test.
- **Count reconciliation.** The 0.4.0 "27 vs 28" confusion was a
  miscount: the CLI list's 28th colon-bearing line was the
  "Static commands" footer, not a check; the registry really held
  27. A count-lock test now pins registry, pyproject description,
  README and `apiverdict list` to the same number, and version
  assertions derive from `__version__` instead of a hardcoded
  string. 0.4.0's CHANGELOG header wrongly said "unreleased" —
  corrected to the 2026-11-04 push (commit 2cfa3c6).
- **spec-conformance judges 3.2 QUERY operations** when a valid
  request body can be built from the requestBody schema (example >
  default > type placeholder). A non-200 for that schema-derived
  body is unjudged, never a FAIL; no buildable body stays
  unjudged. The GET judging path was factored into a shared
  helper with no change to GET behaviour.
- **Verified:** 480 tests OK (~72s), demo/casestudy/library all
  naive FAIL (exit 1) / fixed PASS (exit 0), repro script passes
  in ~7s, clean-venv `pip install .` gives `apiverdict 0.4.1`
  with a passing fixed demo. Suite runner accepts the new check:
  run end-to-end against a live fixture, fixed mode PASS exit 0
  (first 400, retry 201, delta 1, JSON schema v2 intact), naive
  mode FAIL exit 1 with the remediation hint in the summary line.
- **Self-review catch:** the spec-conformance refactor first
  passed `skipped` (an int) into a helper that incremented it
  locally, so `evidence["skipped"]` would always have read 0.
  Fixed by returning the new count from the helpers, with a
  regression test pinning `skipped == 1`. The full suite had
  passed with the bug present — no existing test asserted a
  skipped count — which is itself worth remembering: green does
  not mean every evidence field is exercised.

## Research notes (2026-11-19)

- Contract testing is still the gap (Postman 2025 State of the
  API: only 17% of teams practise it vs 67% functional testing).
  OpenAPI 3.2 (Sep 2025, current 3.2.1) is backward compatible
  with 3.1; apiverdict already handled its `query` /
  `additionalOperations` everywhere except spec-conformance
  judging — now closed.
- RFC 9457 (obsoletes 7807) is the current Problem Details
  standard; apiverdict's `--require-rfc9457` strict mode already
  checks `application/problem+json` content type — no change
  needed. Rate-limit Retry-After presence/validity already
  checked — no change needed.
- November 2026 entrants are adjacent, not overlapping:
  Keploy (~19.8K stars) records real traffic via eBPF and
  generates replay tests; apilabs SuperContracts (Nov 2026)
  is a YAML workflow/contract layer for AI agents; API
  Spector and Bifurc are client + mock + contract tools.
  None of them runs barrier-gated burst checks with final-state
  read-back, or a failed-retry idempotency check — the
  invariant lane stays open. Multi-endpoint chains (the
  SuperContracts lane) remain a v3 candidate, not a v1 fix.

## Deliberately left undone

- **Webhook out-of-order delivery.** Needs a design decision
  first: is the correct behaviour to reject a stale-sequence
  event, buffer it, or apply idempotently? No universal verdict
  exists, so it should not be invented in a one-hour run.
- **PyPI publish, real-API/staging battle test, in-repo GitHub
  Actions workflow.** Out of scope for a local-only run (same
  as before); the paste-able workflow stays under examples/ci/.
- **examples/suite.json** was not extended with the new check;
  it is a curated 10-check example and its count is stated in
  the CHANGELOG. The suite runner accepts the new check (verified
  via a temp config).
- **Cursor expiry.** Research (AIP-158, Google/Slack guidance)
  says an expired cursor must be rejected with a stable error,
  never silently treated as page one. Not built: on a real API
  the check would need a user-supplied expired cursor value to
  send, which the tool cannot manufacture — a fixture-only
  version would overstate it. The adjacent failure (invalid
  cursor silently restarting at page one) IS already caught by
  the walker's duplicate detection, and tie-breaker failures
  show up as order instability across the two walks.
