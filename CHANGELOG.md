## 0.4.0 — unreleased (local only, not pushed, not published)

- Link-header parsing now accepts rel="next" in any link-param position
  (RFC 8288 allows params in any order); a server emitting
  `<url>; title="Next"; rel="next"` was previously missed, and the walker
  read end-of-list.

Since the 0.3.0 cut: `list` grew 14 -> 25 — 10 new live
checks (idempotency-scope, head-consistency, cors,
delete-replay, patch-preserves, detail-superset,
method-guards, cache-privacy, auth-schemes, redirect) plus
the idempotency-variants registry correction; spec-diff
`--waiver-file` (exact rule+location match, waived breaks
stay in evidence); spec-conformance writeOnly-leak rule and
the OpenAPI 3.0 `nullable: true` false-FAIL fix;
idempotency replay-response comparison now covers status as
well as body; the example suite is 10 checks (live + a
spec-diff identity entry + a filter entry); README checks section completed.
Full cycle-by-cycle detail is in the "Third continuation"
sections below.

Also in 0.4.0, since that summary was written: openapi plan pagination candidates now also recognise a 2XX-only array+total schema (same wildcard fallback as spec-conformance); spec-conformance now judges a 200 body against a 2XX-wildcard schema (including its declared Content-Type) when no 200 schema is declared (previously skipped, never judged); BEHAVIOUR CHANGE: error-variants now always probes a malformed JSON body (must 4xx, never 2xx/5xx; shape-exempt, recorded as malformed_status) — a server that crashes on unparseable input now FAILs where it previously passed on shape agreement alone; spec-diff covers the top-level `webhooks` map (removed webhook/operation breaks, added passes); the `if-match` optimistic-concurrency check (current If-Match applies, stale If-Match rejected 412/409 without moving state, final read-back; 27 live checks); rate-limit evidence records the draft's current combined `RateLimit` header (alongside the older Limit/Remaining/Reset trio and Policy); `filter`
(26th live check) with fixture filter support and a filter
entry in the example suite (now 10 checks);
spec-conformance `uniqueItems`, `const`, and 3.1
`prefixItems` rules; digit-string paging metadata coerced
and judged ("30" means 30); auth-matrix unknown-role
validation; library-layer method validation on every
method-taking check; webhook/auth-schemes exactly-one
secret-source rule; rate-limit IETF header names in
evidence; relative-Link fixture endpoint; page-style
empty-pages rule (2+ empty pages fail) and total_pages
drift abort; CI workflow proof for all three fixture
suites; guide flags reference completed; spec-conformance
`dependentRequired` rule (trigger present without its
dependent field fails, nested included); spec-diff
covers 3.2 `query` operations and
`additionalOperations` entries (removed = breaking,
added = non-breaking); spec-errors and
spec-examples judge the same operations (via a
shared operations_of() helper); openapi plan
error targets include QUERY operations with a
requestBody (QUERY carries a body by RFC 10008;
never a burst candidate, being safe);
spec-conformance lists QUERY operations in
`unjudged` with the reason instead of ignoring
them silently.

## 0.3.0 — unreleased (local only, not pushed, not published)
- New runner: `apiverdict suite --config suite.json` (one command,
  one report for any configured check set, with fail-fast config
  validation and optional parallel execution).
- New checks: pagination-consistency, error-variants, spec-errors
  (static), webhook future-timestamp rejection.
- Pagination: relative Link URLs resolved; malformed paging
  metadata (string total/next_offset, non-string cursor) is
  INCONCLUSIVE instead of crashing or silently skipping.
- Rate-limit: unparseable Retry-After values now FAIL (RFC 9110).
- OpenAPI commands: --base-url defaults to the spec's first
  server URL when omitted.
- Spec-conformance now validates additionalProperties values.

### 0.3.0 continued (2026-11-04, same timebox, cycles 8–15)
- `check --only` fixed: it previously ran the whole trio (burst
  included, a mutating check) and filtered afterwards; now only
  the wanted checks run, and an unknown name exits 2 first.
- Pagination: `--max-pages` (default 100) on pagination,
  pagination-consistency, and pagination-concurrent; cursor
  tokens accepted under `next_page_token`/`cursor`, and body
  `next`/`next_url` URLs followed verbatim (with a fix for the
  offset-fallback loop that first surfaced on the last page).
- JUnit output on every command, including single-check commands
  (was demo/casestudy/check/openapi only).
- Fixture servers: listen backlog raised (stdlib default 5
  overflowed under a 10-thread burst plus parallel checks and
  reset one connection — a fixture artefact, not a burst
  finding).
- Real-world validation: the error-shape check itself, run
  GET-only against Petstore, FAILs with the live inconsistency
  (404s plain text, 400 JSON); see docs/REAL-WORLD-VALIDATION.md.
- Repro script re-measured: all headline claims in ~6s.
- Spec-conformance: OpenAPI 3.1 type lists honoured
  (["string", "null"] fields were passing vacuously).
- Webhook: --scheme stripe/github in addition to the default
  apiverdict scheme (real-handler-compatible signatures).
- Pagination: --id-field accepts dotted paths ("item.id").
- New static check: spec-diff — breaking changes between two
  OpenAPI specs (CLI + suite entry).
- New static check: spec-examples — a spec's own examples
  must match its own schemas (CLI + suite entry).
- spec-diff: path-template renames ({id} -> {userId}) are
  non-breaking, not path-removed breaks.
- spec-diff: removed server URLs are breaking, added ones
  non-breaking.
- spec-diff: operations gaining a security requirement are
  breaking, losing one is non-breaking.
- spec-diff: type widening (string -> ["string", "null"]) is
  non-breaking, not a breaking type change.
- error-shape --require-rfc9457 now also requires the
  application/problem+json media type.
- rate-limit: X-RateLimit-* headers recorded in evidence.
- suite --dry-run: validate a config and list its checks
  without running anything (names only, secrets never printed).
- error-payloads: $ref properties resolved before generating
  targeted invalid payloads.
- webhook: a 409 on the duplicate send counts as a
  recognised duplicate, not a failure.
- cache: --compare-field accepts dotted paths ("meta.version").
- pagination: unknown --style is refused (CLI exit 2;
  library INCONCLUSIVE) instead of silently judging one page.
- suite: top-level "config"/"env" applies that environment's
  auth headers to every check that accepts headers.
- openapi CLI lists burst candidates after the report
  (plain-text only; never auto-run).
- pagination: configurable query parameter names
  (--page-param/--size-param/--offset-param/--limit-param/
  --cursor-param) and X-Total-Count header as the total.
- pagination: a page returning more rows than the requested
  limit FAILs (limit-ignored detection).
- idempotency: failed first send is INCONCLUSIVE, an erroring
  replay is a FAIL even with a matching delta.
- spec-conformance: scalar bounds (pattern, numeric bounds,
  length/item/property counts); still not full JSON Schema.
- spec-diff: a response property becoming optional is now
  BREAKING (was non-breaking); request loosening unchanged.
- openapi --plan-only: print the generated check plan
  without running anything.
- new command config-check: validate config + auth wiring
  without running checks; secret values never printed.
- verify sources: passing both --verify-cmd and --verify-url
  is now INCONCLUSIVE instead of silently preferring the
  command.
- spec-diff: removed request/response media types are now
  BREAKING (a JSON -> XML swap was silently missed).
- pagination: the walk stops at the first oversize page
  instead of burning max_pages requests (observed live).
- openapi plan: pagination style + query-param names
  inferred from the spec (_page/_limit now works).
- pagination: the order-stability second walk now uses the
  custom query-param names (it previously re-ran with
  defaults and reported order_stable None).
- fixture on a busy port exits 2 with a clean message
  instead of a socket traceback.
- spec-examples: parameter examples checked; remote-ref
  schemas counted as skipped, not checked.
- error-payloads: $ref'd requestBodies resolved before
  generating the invalid payload.
- spec-conformance: $ref'd 200 response objects resolved
  (were skipped as "no schema").
- $ref'd parameters resolved in spec-conformance fills and
  the openapi plan (paging params via components/parameters
  now generate checks).
- webhook CLI: --timestamp-tolerance and
  --no-enforce-timestamp (library already supported both).
- idempotency CLI: --expect-delta (was library-only).
- empty suites rejected: an empty verdict list is now
  INCONCLUSIVE (was PASS exit 0).
- check --only with a blank value is rejected (exit 2)
  instead of running zero checks.
- list now shows all 15 live checks (idempotency-variants
  was missing from the registry; README said 14).
- registry invariant test: list covers every suite check
  plus spec-conformance.
- CLI validation: --timestamp-tolerance must be >= 0.
- burst --n capped at 1000 (thread-exhaustion guard).
- verify reads in idempotency/webhook now carry the
  check's headers (burst already did).
- distinct verify_headers on idempotency/variants/
  webhook, falling back to headers.
- CLI --verify-header on burst/idempotency/variants/
  webhook (defaults to the check headers).
- spec-diff: operation-level servers diffed.
- spec-conformance: undocumented response statuses FAIL.
- auth-matrix: unknown target methods INCONCLUSIVE;
  methods uppercased before sending.
- write methods must mutate (cache, cache-etag,
  pagination-concurrent): GET/HEAD refused.
- burst/idempotency methods must mutate; GET refused.
- remote guard extended to pagination-concurrent writes
  and mutating-method rate-limit.
- remote guard extended to mutating-method auth-matrix
  targets.
- error-leak now scans response headers too.
- HTTP QUERY method (RFC 10008) accepted as a read
  method.
- spec-diff: operationId renames are breaking.
- runner: non-Verdict check returns are INCONCLUSIVE.
- runner: unknown check statuses are INCONCLUSIVE.
- report --json tolerates non-JSON plugin evidence.
- webhook CLI --body flag; non-object bodies refused.
- webhook CLI --scheme now reaches the check (was
  silently dropped).
- --mode both exits non-zero unless naive FAILs and
  fixed PASSes.
- spec-examples: $ref'd examples resolved and judged.
- path-item $refs resolved in all spec consumers.
- library numeric guards: burst rounds/delta,
  pagination page-size/max-pages, idempotency delta,
  webhook tolerance.
- error-variants needs at least 2 payload kinds.
- Tests: 97 (baseline) → 299.

### Third continuation (2026-11-04, 6-hour timebox)
- Hardening: per-request timeouts configurable via --timeout on every
  check, 2MB response-size guard, CLI argument validation (exit 2),
  unreachable targets report INCONCLUSIVE instead of crashing.
- Second case study: `apiverdict library` — library API with 8 seeded
  behaviour bugs; naive 8 FAIL / fixed 8 PASS, documented in README.
- Runner UX: `check --parallel` (deterministic order), per-check
  `duration_ms` in evidence, plain-language failure summaries,
  JUnit time attributes; exit-code contract unchanged.
- Docs: full user guide (docs/USER-GUIDE.md), real-world Petstore
  validation report (docs/REAL-WORLD-VALIDATION.md), integration
  pack (examples/ci/: GitHub Actions workflow to paste, pre-commit
  smoke script, JUnit snippet).
- Spec-conformance: required query params filled, unjudged
  endpoints recorded instead of aborting the whole check.

### Third continuation, continued (2026-11-04, same timebox, cycle 160+)
- New live check: `idempotency-scope` (16th in `list`) — same key
  to two different endpoints; B must create its own resource
  (delta 1, PASS), never silently replay A's result (2xx + delta
  0, FAIL). A B rejection is INCONCLUSIVE: it cannot be told
  apart from a validation rejection, so Stripe-model servers
  (same key + different request -> 422) land there, not PASS
  and not FAIL. CLI `idempotency-scope`, suite entry, guide
  section. Proven with fake transports (global-dedupe FAIL,
  scoped PASS) and a throwaway local server via the CLI
  (scoped exit 0, global-dedupe exit 1). No fixture mode: the
  existing naive fixtures ignore keys, so they would PASS this
  check — a fixture proof would be misleading.
- Tests: 299 -> 303.

- error-shape / error-variants: a 5xx on an invalid-input probe
  is now a FAIL even when the shape is consistent across
  endpoints/payload kinds — consistency must not mask a crash
  (evidence `server_errors`). Stale spec-conformance docstring
  line about bounds corrected.

- spec-conformance: a 200 response's Content-Type must now be
  one of the media types the spec declares for it (charset
  parameters ignored); a missing Content-Type is not judged.

- pagination: a row field that changes JSON type across pages
  now FAILs (nulls exempt — nullable fields may vary by row).

- idempotency: opt-in --compare-responses requires the replay
  response to equal the original (with repeatable
  --ignore-field for volatile top-level fields); a replay that
  mints a new id while the delta stays 1 now fails.

- pagination / pagination-consistency: unique rows exceeding
  the declared total now FAILs (previously only missing rows
  failed, so an under-reported total passed silently).

- burst: opt-in --compare-responses (with --idempotency-key)
  requires all N responses in a round to be identical; a
  server that dedupes the write but mints fresh ids per
  response now fails (with --ignore-field for volatile keys).

- New live check: `head-consistency` (17th in `list`) — HEAD
  vs GET for the same URL must agree on status, Content-Type,
  and Content-Length; HEAD 405 is unjudged, not a failure.
  Also fixed: CLI URL validation now handles repeatable
  --url lists (it rejected every `head` run as empty).

- spec-diff: path-item-level servers are now diffed (removed
  breaking, added non-breaking), completing the global /
  operation / path-item server coverage.

- New live check: `cors` (18th in `list`) — OPTIONS preflight
  must return 2xx + Allow-Origin + Allow-Methods covering the
  requested method; opt-in for browser-facing APIs only.

- spec-conformance: documented non-200 responses are now
  judged against their declared error schemas (exact status,
  wildcard, then default); schema-less error statuses stay
  unjudged.

- suite: missing required check arguments now fail fast at
  load (exit 2, naming the arguments) instead of surfacing
  as a run-time INCONCLUSIVE.

- New live check: `delete-replay` (19th in `list`) — DELETE
  twice; the first must drop state by exactly 1, the replay
  must be 2xx or 404/410 with no further state change.

- New live check: `patch-preserves` (20th in `list`) — PATCH
  one field; only that field may change, all others preserved
  (a PATCH acting like PUT fails, naming wiped fields).

- New live check: `detail-superset` (21st in `list`) — a
  detail response must cover its list row (no dropped or
  retyped fields; first row only, documented as a smoke
  signal).

- New live check: `method-guards` (22nd in `list`) —
  declared-rejected methods must return 405 with an Allow
  header not listing the probed method.

- New live check: `cache-privacy` (23rd in `list`) — same
  URL as two callers must not return identical bodies on a
  user-specific endpoint (URL-keyed cache leak).

- openapi plan: collection/detail path pairs are now listed
  as detail-superset candidates (not auto-run); the detail
  check substitutes any single {..} template segment.

- New live check: `auth-schemes` (24th in `list`) — a token
  must work under its own scheme only, never under
  Basic/Digest/Token/bare.

- spec-conformance: a writeOnly property present in a live
  response now FAILs (sensitive-data leak); spec-examples
  deliberately exempts the rule (request examples should
  contain writeOnly fields).

- spec-conformance false-FAIL fixed: OpenAPI 3.0
  `nullable: true` fields reading null no longer fail.

- New live check: `redirect` (25th in `list`) — a redirect
  chain must be finite, acyclic, and https-preserving.

- spec-diff: `--waiver-file` (and suite `waiver_file`) blesses
  intentional breaks by exact rule+location match; waived
  breaks stay in evidence, unmatched waivers are reported,
  never silently applied.

- spec-conformance: `uniqueItems: true` arrays with
  duplicates now FAIL (same scalar-rule family as bounds).

- New live check: `filter` (26th in `list`) — every row of
  a filtered list must satisfy the filter predicate.


## 0.2.0 — unreleased (local only, not pushed, not published)

### Continuation run (2026-11-04, after the first v2 pass)
- Safety: mutating checks (idempotency, idempotency-variants, webhook,
  cache) now share burst's remote guard; webhook `--secret-env`.
- Spec-conformance: recursive nested/array checks, enum, local $ref
  resolution (remote refs never fetched), path-param filling.
- Error-shape: per-endpoint invalid payloads from requestBody schemas
  in OpenAPI runs; `--require-rfc9457` strict problem-details checks.
- New check: `pagination-concurrent` — deterministic mid-walk write;
  offset FAILs with duplicates, cursor PASSes (fixture /api/feed).
- New check variant: `idempotency-variants` — same key + different
  payload must add 0 side effects.
- CLI: `apiverdict list`, `check --only`, `pagination-concurrent`
  subcommand, truncated plain-text evidence (JSON never truncated).
- Reports: top-level + per-check keys locked by test at schema_version "2".
- Docs: `examples/repro-5min.sh` reproduces every headline claim in
  ~13 seconds (demo, casestudy, concurrent pagination); README
  updated for the new checks (spec-check $ref/nested, concurrent
  pagination, RFC 9457 strict, safety guards).
- Tests: 52 → 85 and counting (see V2-NOTES.md for the running count).
- JUnit XML output (--junit on every command;
  INCONCLUSIVE maps to <skipped>, exit code stays authoritative).
- Burst with a shared Idempotency-Key per round (--idempotency-key).
- New checks: `error-leak` (tracebacks/paths/SQL in error bodies),
  `cache-etag` (304 on match, ETag changes after write).
- Auth-matrix targets accept a payload for POST/PUT/PATCH.
- README: honest comparison with Postman/Bruno, Schemathesis, Pact
  (design differences, not benchmarks).

- Packaging: `pyproject.toml`, console entry point `apiverdict`,
  `--version`, version 0.2.0. Stale "working name" docstrings removed.
- New checks, all on the same runner/registry/report and exit codes:
  auth-matrix, rate-limit (sequential, not a load test), idempotency-key
  replay, webhook (signature, duplicate, replay), cache-invalidation,
  spec-conformance (live GET responses vs OpenAPI 200 schemas, required
  fields + primitive types only — no $ref, no deep validation, stated).
- Auth/env: config file (JSON; TOML on Python 3.11+), named environments,
  bearer/API-key/basic headers from explicitly named env vars only.
  Secrets never appear in reports or logs; a missing env var is an
  error, not an empty header.
- OpenAPI: JSON specs only (YAML refused plainly, convert to JSON first);
  generates pagination candidates, error-shape targets, and burst
  candidates (listed, not auto-run — a burst needs a user verify step).
  Sample spec in `examples/openapi-sample.json`.
- Fixture: `/api/items-link` Link-header pagination (with test),
  `/api/admin` auth-matrix target, `/api/rate-limited`, and
  Idempotency-Key handling on `/api/book`.
- Error-shape: RFC 7807 / RFC 9457 problem-details detection, mixed-usage
  flag, and `--require-rfc7807`.
- Case study: `apiverdict casestudy` — a second, lifelike localhost shop
  with 8 seeded bugs, all caught in naive mode, all passing in fixed
  mode. Documented as a fixture, NOT a real API.
- Reports: `schema_version` added ("2"; v1 reports had no version field).
- Tests: 17 → 52, all passing. Demo still naive FAIL / fixed PASS.

## 0.1.0 — 2026-11-04 (pushed to atrx07/apiverdict, commit 658feb45)

- Initial release: burst, pagination, error-shape checks, bundled
  localhost fixture with naive/fixed modes, unified JSON report,
  exit codes 0/1/2, never green on a skipped check.
