# apiverdict — API behaviour toolkit

**One-line pitch:** A behaviour suite for APIs, not another request client. Point it at an API and it runs invariant checks you didn't have to hand-write — then fails CI if one breaks.

Postman and Bruno send requests and check the assertions you wrote. `apiverdict` checks the behaviours you forgot to assert: does a burst of identical requests create exactly one side effect? Does paging a list return every row exactly once, in stable order? Do all your endpoints fail the same way? Can a user role reach an admin endpoint? Does your rate limit actually limit, consistently? Does a retried payment double-charge?

## Install

Python 3.10+ stdlib only, zero dependencies, $0:

```bash
pip install .
apiverdict --version   # apiverdict 0.4.0
# or, without installing:
python -m apiverdict demo
```

## 60-second demo

No real API, no auth, no data risk — the demo runs against a bundled localhost fixture:

```bash
apiverdict demo
```

Expected output (abbreviated):

```
--- demo: fixture mode = naive ---
apiverdict — overall: FAIL (exit 1)
  [FAIL] burst: duplicate side effects in round(s) [1, 2]: largest delta 10, expected 1
  [FAIL] pagination: 10 duplicate row(s); 10 missing row(s) vs total 30
  [FAIL] error-shape: 2 distinct error shapes across 2 endpoint(s)

--- demo: fixture mode = fixed ---
apiverdict — overall: PASS (exit 0)
  [PASS] burst: exactly 1 side effect(s) per round across 2 round(s)
  [PASS] pagination: walked 3 page(s), 30 unique row(s), no duplicates, no gaps, stable order
  [PASS] error-shape: all 2 endpoint(s) share one error shape
```

The naive FAIL is the point of the demo: it proves the checks can catch the bugs, not just pass.

## 5-minute reproduction (stranger test)

No install, no keys, no real API — everything below runs against bundled localhost fixtures:

```bash
sh examples/repro-5min.sh
```

It runs the demo, the library case study, the shop case study, and the concurrent-pagination proof, and prints the expected exit code after each step. Runs in seconds end-to-end (5.7s measured 2026-11-04, well under 5 minutes). If the FAIL/PASS pattern matches, every claim in this README reproduces on your machine. The concurrent part is worth watching closely: the offset run FAILs with 1 duplicate row, and the cursor run PASSes with all 20 baseline rows exactly once — and the cursor walk correctly does *not* include the newly written row, because it was inserted ahead of the anchor. Requiring it would fail correct cursors, so apiverdict deliberately does not.

## Case study: a richer fixture, 8 bugs, 8 catches

```bash
apiverdict casestudy
```

The case-study fixture is a second, more lifelike localhost shop (bookings, products, payments, settings, webhooks, admin reports). It is **not a real API** and is never presented as one — it is the battle-test substitute, with seeded bugs documented here:

| Check | Naive bug it caught |
|---|---|
| burst | duplicate bookings for the same ref |
| pagination | page 2 repeats page 1, and the second walk returns rows in reverse order |
| error-shape | bookings return RFC 7807 problem details, payments return a plain 422 shape (mixed usage) |
| auth-matrix | admin report leaks to user and anonymous roles |
| rate-limit | search endpoint never limits |
| idempotency | payments ignore `Idempotency-Key` (replay double-charges) |
| webhook | tampered bodies accepted, stale replays accepted, duplicates double-applied |
| cache | settings GET after POST serves the old value |

In fixed mode all eight pass. Run it yourself: `apiverdict casestudy --mode naive` then `--mode fixed`.

A second case study in a different domain proves the suite is not
over-fitted to one fixture — a public library (books catalogue,
loans, holds, availability, librarian report, catalogue search,
overdue webhook), with the same eight bug classes seeded:

```bash
apiverdict library
```

Naive: all 8 FAIL (burst delta 10, pagination 10 duplicates /
10 missing + order change, mixed error shapes, 2 auth leaks, no
rate limit, idempotency delta 2, webhook tampered + duplicate +
stale, stale cache). Fixed: all 8 PASS.

## Validated against real public APIs (read-only)

Fixture results are never presented as real-API results. The
non-mutating checks have also run against public demo APIs
(GET-only, a handful of requests each):

| Target | Check | Result |
|---|---|---|
| Petstore demo (`petstore3.swagger.io`) | spec-conformance | FAIL: a pet missing required `name`; an order `status` outside its enum (shared-demo data pollution, almost certainly — but the check located both violations exactly) |
| Petstore demo | error-shape (GET) | FAIL: not-found is plain text, bad input is JSON — 2 shapes where 1 is required |
| Petstore demo | spec-errors / spec-examples / spec-diff (self) | PASS / PASS with 0 examples / PASS with 0 breaking — the contract documents errors everywhere while live data violates it |
| JSONPlaceholder (`jsonplaceholder.typicode.com`) | pagination (`_page`/`_limit`) | PASS: 100 rows, total 100, no duplicates or gaps |
| JSONPlaceholder | pagination-consistency (sizes 10 vs 5) | PASS: same 100-row sequence, totals agree |
| JSONPlaceholder | pagination (Link headers) | PASS: 10 pages, 100 rows, stable order |
| JSONPlaceholder | pagination (auto, `offset`/`limit`) | FAIL, correctly: the API ignores those params and dumps all 100 rows on page 1 — the limit-ignored rule catches it in 1 request |
| JSONPlaceholder | head-consistency (`/posts/1`, `/posts`) | PASS: HEAD 200, headers agree with GET |
| JSONPlaceholder | detail-superset (`/posts` + `/posts/{id}`) | PASS: first page's row covered by its detail response, 4 fields, matching types |
| JSONPlaceholder | cors (OPTIONS preflight, POST) | PASS: preflight allows POST from any origin (wildcard) |
| httpbin (`httpbin.org`) | redirect (`/redirect/2`) | PASS: 2 hops via relative Locations, ends HTTP 200 |
| httpbin | head-consistency (`/get`) | FAIL by 1 byte: HEAD claims Content-Length 224, GET returns 223 — httpbin echoes the request method in its body, so the HEAD rendering is longer than any GET rendering (demo quirk, but a true positive for the contract) |
| httpbin | cors (OPTIONS preflight, GET) | PASS: preflight allows GET from the origin |
| JSONPlaceholder | detail-superset (`/users` + `/users/{id}`) | PASS: all 8 list-row fields covered, matching types |
| JSONPlaceholder | filter (`userId=1`) | PASS: all 10 returned rows satisfy the predicate |

Mutating checks (burst, idempotency, webhook writes, cache
writes) have only ever run against localhost fixtures, by
design: this tool creates resources on purpose and never
deletes anything, so pointing it at a shared demo would write
junk into someone else's data. Full detail, including what
each run could not claim, is in
`docs/REAL-WORLD-VALIDATION.md`.

## The checks

### 1. `burst` — simultaneous requests, final-state verification

Fires N identical state-changing requests through a threading barrier gate, repeats for R rounds, and compares the *final state* before and after each burst against `--expect-delta` (default 1). Final state comes from `--verify-cmd` (a shell command printing a single number) or `--verify-url` (a URL returning a count).

```bash
apiverdict burst \
  --url http://127.0.0.1:8000/api/book \
  --payload '{"slot": "slot-{round}"}' \
  --n 20 --rounds 3 \
  --verify-url 'http://127.0.0.1:8000/api/count?slot=slot-{round}' \
  --expect-delta 1
```

`{round}` is substituted per round (1-based) in the payload, verify command, and verify URL. Status codes are reported but never trusted: a double-applied operation can still answer 201 every time. A burst with no state check is `INCONCLUSIVE`, never a pass. A burst PASS is evidence, not proof — it proves the tested rounds deduplicated, not that every code path does.

### 2. `pagination` — style-agnostic list walker

Walks a list endpoint to the end and checks for duplicate rows, missing rows vs the declared total, order changes between two walks, and overstated page counts (2+ empty pages inside a declared range). Styles are auto-detected: offset/limit, page/size, cursor, and `Link: rel="next"` headers (the Link style has its own fixture endpoint, `/api/items-link`, and its own test).

```bash
apiverdict pagination --url http://127.0.0.1:8000/api/items --page-size 10
```

Why order stability matters: a sort without a unique tie-breaker (e.g. sorting only by a low-cardinality field) lets the database return tied rows in a different order on the next page, producing duplicates and gaps with no error anywhere. This check catches that without needing to know the sort.

### 2b. `pagination-concurrent` — the same walk, with a write mid-walk

Offset pagination is a *position*, and positions move when rows are inserted ahead of the reader. This check walks once for a baseline, then walks again, synchronously writing one row after page N (`--write-after-page`, default 1, `--write-payload` supports `{seq}`). It then requires: no duplicates, and every baseline row present at least once. Total drift is expected and is not a failure; the new row is not required to appear (a correct cursor walk anchored before the insert may legitimately finish without it).

This is deterministic, not a timing race: a background writer thread would be flaky by construction (a fast walk may finish before the writer fires). One synchronous mid-walk write already proves the drift.

```bash
# Against a running fixed fixture (apiverdict fixture --mode fixed --port 8765):
apiverdict pagination-concurrent \
  --url http://127.0.0.1:8765/api/feed --page-size 10 --style offset \
  --write-url http://127.0.0.1:8765/api/feed \
  --write-payload '{"name": "feed-new-{seq}"}' --write-after-page 1
# -> FAIL, 1 duplicate row. The same command with --style cursor -> PASS.
```

An offset FAIL here is the *demonstration*, not a server bug to patch by changing the server — the fix is to switch the endpoint to cursor/keyset pagination. One write is covered; multiple writes, deletes, and updates mid-walk are the same mechanism and are not separately covered.

### 2c. `pagination-consistency` — same list, two page sizes

```bash
apiverdict pagination-consistency --url http://127.0.0.1:8000/api/items \
  --page-size-a 10 --page-size-b 5
```

Same endpoint walked at two page sizes must return the same full
id sequence and the same declared total, with no duplicates or
gaps at either size. Catches totals computed as pages × page
size, per-page re-sorts, and cursors that drift differently by
size — bugs the single-size walker cannot see. A page-size cap
alone is not a failure if the walk still returns the same
sequence.

### 3. `error-shape` (`errors` command) — error consistency, not just non-200s

Sends an invalid payload to each supplied endpoint and clusters responses by (status, sorted top-level keys, error-field name). One cluster is a pass; more than one is a fail, with each cluster listed. An endpoint that accepts invalid input with a 2xx is also a fail. Mixed RFC 7807 / RFC 9457 problem-details usage (some endpoints return `type`/`title`/`status`/`detail`, others don't) is flagged even when key sets coincide, and `--require-rfc7807` fails unless every endpoint returns problem details. `--require-rfc9457` adds strict checks: the `status` member must equal the HTTP status, `type` must be an absolute URI or `about:blank`, and `title` must be non-empty (RFC 9457 obsoletes RFC 7807 with the same shape, so the two flags differ only in strictness).

When run via `apiverdict openapi`, the invalid payload is no longer the same generic payload for every endpoint: it is derived per endpoint from that endpoint's requestBody schema — drop a required field if there is one, else wrong-type a typed field — and the probe reason per endpoint is recorded in the report evidence. A generic payload that every endpoint answers with the same 400 hides validation differences; a targeted one does not.

```bash
apiverdict errors \
  --endpoint POST:http://127.0.0.1:8000/api/book \
  --endpoint POST:http://127.0.0.1:8000/api/order
```

### 4. `auth-matrix` — role × endpoint, in one sweep

```bash
apiverdict auth-matrix \
  --roles '[{"name":"admin","headers":{"X-Role":"admin"}},{"name":"user","headers":{"X-Role":"user"}},{"name":"anonymous","headers":{}}]' \
  --targets '[{"method":"GET","url":"http://127.0.0.1:8000/api/admin","allowed_roles":["admin"]}]'
```

A role reaching an endpoint it shouldn't (2xx) is a privilege leak — the highest-severity finding. An allowed role getting 401/403 is also a fail, and any other status is reported as unexpected, not silently treated as "denied". A transport failure in any cell makes the whole check `INCONCLUSIVE`.

### 5. `rate-limit` — behaviour, not a load test

Sends N identical requests **sequentially** and reports where limiting kicks in and whether it stays consistent. No limiting when expected is a fail; limiting that lifts mid-run (429, then 200 again) is a fail; consistent limiting with `Retry-After` present is a pass. Use `--no-limit` to invert the expectation for endpoints documented as unlimited. This is not throughput/latency testing — k6 already does that.

```bash
apiverdict rate-limit --url http://127.0.0.1:8000/api/search --requests 30
```

### 6. `idempotency` — sequential replay, not a burst

Burst tests simultaneity; this tests the other half of the same bug class: send the same request twice, sequentially, with the same `Idempotency-Key`, and compare final state. Delta 2 is a fail (a client retry double-charges). No verify step means `INCONCLUSIVE`, same rule as burst. This matters more as agent-driven clients retry aggressively — a retry without a honoured key is a duplicate charge waiting to happen.

```bash
apiverdict idempotency --url http://127.0.0.1:8000/api/payments \
  --payload '{"order": "ord-1"}' \
  --verify-url 'http://127.0.0.1:8000/api/payments/count?order=ord-1'
```

`idempotency-variants` adds the second half: same key, then same key with a *different* payload. Reusing a key must never create a second side effect, even when the retry carries changed parameters — returning the original response and returning a 4xx key-conflict error both pass; creating a second resource is the fail (that is how a retried payment with an edited amount double-charges). A different key with the same payload is deliberately not asserted: some APIs deduplicate by payload and some do not, so neither delta is universal there.

```bash
apiverdict idempotency-variants --url http://127.0.0.1:8000/api/payments \
  --payload '{"order": "ord-1"}' --alt-payload '{"order": "ord-1", "amount": 999}' \
  --verify-url 'http://127.0.0.1:8000/api/payments/count?order=ord-1'
```

### 7. `webhook` — signature, duplicate, replay

Sends a valid signed event (HMAC-SHA256, `X-Signature-256`), a tampered body with the original signature, the same event again, and a stale replay. Valid must be acked, tampered must be 401/403, stale must be 401/403, and a duplicate must be acked without double-applying — the last part only proven when a `--verify-url`/`--verify-cmd` is supplied; without one, the duplicate cell is reported as ack-only with the side effect unverified.

### 8. `cache` — stale-after-write

Read, write, read again. If the second read is identical to the first after a successful write, the read path is serving stale data. A failed write or read is `INCONCLUSIVE`, not a guess.

```bash
apiverdict cache --read-url http://127.0.0.1:8000/api/settings \
  --write-payload '{"theme": "dark"}'
```

### 9. `spec-check` (`spec-conformance` in `list` and suite configs) — live responses vs the OpenAPI schema

For each GET endpoint, checks the 200 response against its schema (or the 2XX wildcard's, when no 200 schema is declared): required fields present, declared primitive types matching (integer rejects booleans), `enum` values honoured, and nested objects / arrays of objects checked recursively (depth capped at 10). Local `$ref`s (`#/components/schemas/...`) are resolved in-document — remote refs are never fetched, and an endpoint whose schema is only a remote ref is skipped, not judged. Templated paths (`/users/{userId}`) are filled from parameter examples, defaults, or type placeholders; a filled path that 404s is `INCONCLUSIVE`, not a conformance fail, because the filler cannot prove the id exists.

Still deliberately narrow: no format assertions, no `allOf`/`anyOf`/`oneOf` composition — full JSON Schema validation would need a real validator, and pretending otherwise would be a false promise. Scalar bounds (pattern, minimum/maximum, minLength/maxLength, minItems/maxItems, minProperties/maxProperties) ARE checked. What it catches in CI is the common breaking change: a field renamed, dropped, retyped, or moved, while the endpoint still returns 200.

```bash
apiverdict spec-check --spec api.json --base-url http://127.0.0.1:8000
```

### More checks — one line each, full detail in the user guide

- `error-variants` — one endpoint, many invalid payload kinds (missing field, wrong type, unknown field): one error shape, or fail; plus a malformed-JSON probe (must 4xx, never 2xx/5xx; shape-exempt).
- `error-leak` — error bodies AND headers scanned for tracebacks, server paths, and SQL fragments.
- `idempotency-variants` — same key, different payload: must add 0 more side effects.
- `idempotency-scope` — same key on two different endpoints: must not silently replay endpoint A's result on B.
- `delete-replay` — DELETE twice: first drops the count by exactly 1, replay must not change state again.
- `patch-preserves` — PATCH one field: only that field may change; wiped or altered siblings fail.
- `cache-etag` — a changed resource must change its ETag (or drop it); an unchanged ETag after a write is stale.
- `head-consistency` — HEAD vs GET for the same URL: status, Content-Type, and Content-Length must agree.
- `cors` — OPTIONS preflight must return 2xx plus `Access-Control-Allow-Origin` and list the requested method (browser-facing APIs only).
- `detail-superset` — a detail response must cover its list row: no dropped or retyped top-level fields.
- `method-guards` — methods declared rejected must return 405 with an `Allow` header not listing the probed method.
- `cache-privacy` — the same URL as two callers must not return identical bodies on a user-specific endpoint.
- `redirect` — a redirect chain must be finite, acyclic, and never downgrade https to http.
- `auth-schemes` — a token must work under its own scheme only, never under Basic/Digest/Token/bare.
- `filter` — every row of a filtered list must satisfy the filter predicate.
- `if-match` — a current If-Match must apply, a stale If-Match must be rejected (412/409) without moving state.

### Static spec checks (no network)

- `spec-errors` — every operation documents an error response (4xx/5xx or `default`).
- `spec-diff` — breaking changes between two specs (removed paths/operations/webhooks/response codes/properties, new required inputs, type changes, shrunk enums); additive changes never fail.
- `spec-examples` — the spec's own examples match its own schemas, with spec-check's rules.

`apiverdict list` shows all 27 live checks (including
`error-variants`, `error-leak`, `cache-etag`, and
`idempotency-variants`, plus `idempotency-scope`, each covered
in the user guide); the
three static checks above are separate commands and suite
entries, not live checks.

## Auth and environments

Most commands accept `--config` and `--env`. The config names the credential's env var explicitly — apiverdict never auto-scrapes the environment for plausible-looking token variables, and never prints a credential value in output or reports (evidence names the env var, never its value). A missing env var is an error, not an empty header silently sent (an empty `Authorization` header can masquerade as anonymous access and give a false auth-matrix PASS).

```json
{
  "default_environment": "local",
  "environments": {
    "local": {
      "base_url": "http://127.0.0.1:8765",
      "auth": {"type": "bearer", "env_var": "APIVERDICT_TOKEN"}
    },
    "staging": {
      "base_url": "https://staging.example.invalid",
      "auth": {"type": "api-key", "env_var": "APIVERDICT_API_KEY", "header": "X-Api-Key"}
    }
  }
}
```

```bash
APIVERDICT_TOKEN=... apiverdict check --base-url http://127.0.0.1:8765 \
  --config apiverdict.json --env local
```

An example config is in `examples/apiverdict.json`. Auth types: `bearer`, `api-key`, `basic`.

## OpenAPI-driven checks

```bash
apiverdict openapi --spec api.json --base-url http://127.0.0.1:8000
```

Reads a **JSON** OpenAPI 3.x spec and generates: pagination candidates (GET operations with offset/limit/page/size/cursor params, or an array + total in the 200 / 2XX schema), error-shape targets (every POST/PUT/PATCH/QUERY with a requestBody), detail-superset candidates (a GET collection paired with a GET detail path — listed, not auto-run), and burst candidates (POST operations — listed, not auto-run, because a burst still needs a verify URL/command from the user; generating one would be inventing evidence).

Companion commands for the same spec, deliberately separate
from the generated run: `spec-check` (live GET responses vs
schemas), `spec-errors` (error responses documented),
`spec-examples` (examples match schemas), and `spec-diff`
(breaking changes vs an older spec).

**YAML specs are not parsed.** YAML is a large language and a half-broken parser would silently misread a spec and generate wrong checks — worse than refusing. Convert YAML to JSON first with a YAML tool, then feed the JSON in. The error message says so plainly. A sample spec is in `examples/openapi-sample.json`.

## CI snippet

```yaml
# The v1 trio is wired to fixture-shaped endpoints, so CI runs it
# against the bundled fixture; a real API gets spec-check and the
# single-check commands instead (see examples/ci/).
- name: API behaviour checks
  run: |
    python -m apiverdict fixture --mode fixed --port 8765 &
    sleep 1
    apiverdict check --base-url http://127.0.0.1:8765 --json
```

Exit codes: **0** pass, **1** a check failed, **2** could-not-verify. A check that cannot verify never counts as a pass — a skipped check must not turn a build green. JSON reports carry `schema_version` ("2" in v2; v1 reports had no version field, treat as "1").

## Safety

- Localhost targets work with no flag. Any other host requires `--allow-remote`, which prints a banner warning.
- The remote guard applies to every mutating check, not just burst: burst, idempotency, idempotency-variants, idempotency-scope, delete-replay, patch-preserves, webhook, cache, cache-etag, if-match, pagination-concurrent (its mid-walk write), rate-limit with a mutating method, auth-matrix with a mutating-method target, and method-guards with a mutating-method probe all refuse a non-localhost target without `--allow-remote` (returning `INCONCLUSIVE`, never a silent remote write). Read methods — GET, HEAD, OPTIONS, QUERY (RFC 10008, safe and idempotent) — never need the flag. Read-only checks (pagination, auth-matrix GETs, spec-conformance GETs) are not gated — they never write. Error-shape probes are also ungated, but with a qualification: they send invalid payloads meant to be rejected, so they write only when the server wrongly accepts invalid input — which is exactly the failure the check reports.
- Webhook secrets: prefer `--secret-env` (env var) over `--secret` — a `--secret` value is visible in shell history and process lists. The secret is never printed in reports or evidence either way.
- **This tool creates resources on purpose and never deletes anything.** Use staging / test data only. There is no cleanup or rollback, by design — deleting is the user's job, stated plainly, not hidden.
- Bursts are only ever run against localhost fixtures in this repo's own tests and demos. Never point a burst at a production API.
- The bundled fixture servers bind localhost only.
- No telemetry, no LLM calls, no network calls except to the target API under test.

## What it will NOT do

- **Load / performance testing.** No throughput, latency, or p95 metrics — k6 already does that, and a load test passing can hide 20 duplicate bookings.
- **Full JSON Schema validation.** Spec-check is required-fields + primitive types + enums + scalar bounds (pattern, numeric bounds, length/item/property counts) + nested objects/arrays + local `$ref`s + writeOnly-leak + additionalProperties values + error-schema judging + `uniqueItems`/`const`/`prefixItems`/`dependentRequired`; format assertions and `allOf`/`anyOf`/`oneOf` are out of scope by design.
- **GraphQL.** No clean stdlib path was found that wouldn't be a half-broken query parser; REST/OpenAPI only for now.
- **HTTP/2 single-packet timing.** v1 uses a thread gate over pre-opened connections and reports client send skew honestly; it does not claim single-packet precision (that's Burp Turbo Intruder territory).
- **Multi-endpoint chains** (cart → checkout, redeem → confirm). Single-endpoint bursts only.
- **Automatic cleanup / rollback** of created resources. Never deletes, by design.
- **Fixing your API.** This tool reports behaviour; it does not rewrite code.
- **Anything requiring a browser, a paid API, or a key to try it.** Stdlib only, $0.

## How this differs from neighbouring tools

This is a design comparison, not a benchmark — apiverdict has not been run against these tools' test suites, and no claim is made about their quality:

| Tool | What it proves | What apiverdict adds |
|---|---|---|
| Postman / Bruno | Hand-written assertions on responses you chose | Invariants you didn't hand-write, run as a suite with CI exit codes |
| Schemathesis / spec fuzzers | Schema violations, 5xx crashes, accepted bad input, generated from a spec | Business invariants the spec cannot express: burst final-state, pagination under a mid-walk write, auth-matrix leaks, idempotency-key reuse, webhook duplicate/replay, cache staleness |
| Pact (consumer-driven contracts) | Provider honours a consumer's recorded contract | Behaviour across roles, limits, retries, and writes — not just one consumer's happy path |
| Dredd / spec validators | Every documented request returns a response matching the spec | Behaviour the spec cannot state at all: what happens when the same request arrives twice at once, or a list is walked while it changes |
| oasdiff (spec-vs-spec diff) | Breaking changes between two spec versions, with hundreds of rules | A narrower built-in `spec-diff` (JSON only, core rules: removals, new required inputs, type changes, shrunk enums, server/security changes) that shares apiverdict's unified report and exit codes — use oasdiff when you need its full rule set |
| IdemCheck (Go CLI) | Same-key concurrent bursts with settle/replay/verdict and CI exit codes | Burst final-state verification via a read-back command/URL (`--verify-cmd`/`--verify-url`) instead of trusting responses alone, plus the same key tested sequentially, across endpoints (scope), and for replay-response equality — all inside one unified report with the other 26 checks |

One deliberate difference worth stating: a transport failure or an unverifiable check in apiverdict is `INCONCLUSIVE` with exit code 2, never a pass. Some fuzzing tools report transport errors separately from the exit code; here, "could not verify" cannot turn a build green.

A note on why idempotency sits this high in the list: as agent-driven
clients retry aggressively — often without a developer explicitly
asking for a retry — a `POST` that is not safe to retry is a
duplicate charge waiting to happen. The `Idempotency-Key` pattern
is standardised in an IETF Internet-Draft (not yet an RFC, and
long-established in payment APIs); apiverdict tests the promise,
not just the header's presence.

## Adding a check (plugin seam)

Each check is a separate module under `apiverdict/` returning a `Verdict` (`PASS` / `FAIL` / `INCONCLUSIVE`), registered in `apiverdict/checks.py` and wired into the CLI. A new check needs no change to the core runner's shape.

## CLI helpers

```bash
apiverdict list                          # every check, and what each proves
apiverdict check --base-url ... --only burst,pagination   # subset of the v1 trio
apiverdict check --base-url ... --parallel   # trio concurrently; only for
                                             # independent endpoints (output
                                             # order is still trio order)
apiverdict check --base-url ... --timeout 5  # per-request timeout (default 10s)
apiverdict suite --config examples/suite.json  # all configured checks, one report
apiverdict openapi --spec openapi.json --base-url ... --plan-only  # what would run, no network
apiverdict config-check --file apiverdict.json --env staging  # validate config + auth wiring
```

Runner-mediated reports (`demo`, `casestudy`, `library`, `suite`,
`check`) carry per-check `duration_ms` in evidence, and plain-text
reports end with a one-line failure summary (counts, failed and
unjudged check names) — the line a CI reader scans first. Every
command also accepts `--junit` for a JUnit XML report. For the
full workflow, troubleshooting, and what each check cannot prove,
see `docs/USER-GUIDE.md`; for the integration pack (GitHub
Actions workflow to paste, pre-commit script, JUnit snippet), see
`examples/ci/`.

Plain-text reports truncate long evidence lists (first 10 + a count); the `--json` report is never truncated, so CI logs stay readable while the full evidence is still machine-readable. JSON reports carry `schema_version` ("2"); the top-level keys (`tool`, `schema_version`, `overall`, `exit_code`, `checks`) and per-check keys (`check`, `status`, `summary`, `evidence`) are locked by test — new evidence fields may be added, existing keys are not renamed or removed within a schema version.

## Roadmap (deliberately out of v2)

- GraphQL, if a clean stdlib path emerges
- OpenAPI 4.0 / AsyncAPI support
- Multi-write / delete / update mid-walk pagination (one deterministic write already proves the drift mechanism)
- PyPI publish (name `apiverdict` still to be re-verified at publish time)

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests start fixture servers on ephemeral localhost ports and assert: naive fails, fixed passes, verify-command failure gives `INCONCLUSIVE` (not a pass), non-localhost without `--allow-remote` is refused, Link-header pagination works, auth-matrix leaks are caught, rate limits are consistent, idempotency keys are honoured, webhooks reject tampering, caches don't go stale, and the demo reports FAIL on naive and PASS on fixed. Newer coverage adds: spec-diff breaking-change rules (including template renames, type widening, response-optional, media-type removal, operation servers, and operationId renames), spec-examples consistency (including parameter examples and remote-ref skipping), spec-conformance undocumented-status failures, webhook Stripe/GitHub schemes, pagination cursor aliases, dotted id fields, custom query-param names, `X-Total-Count` totals, and limit-ignored detection with early stop, OpenAPI 3.1 type lists, scalar bounds, RFC 9457 media-type strictness, idempotency send-status rules, verify-header plumbing (including the distinct `--verify-header` flag), write-method and side-effect-method guards, remote guards on every writer, error-leak header scanning, HTTP QUERY as a read method (RFC 10008: safe and idempotent, so QUERY probes need no remote guard), and `$ref` handling (request bodies, responses, and parameters).
Latest coverage adds: idempotency-scope (same key across
endpoints), 5xx-on-invalid-input failures for error-shape and
error-variants, spec-conformance response Content-Type rules,
pagination row-retype detection and under-reported-total
failures, and opt-in idempotency replay-response comparison
(with `--ignore-field` for volatile fields).
Newest coverage adds: burst replay-fidelity with
`--idempotency-key`, head-consistency, cors preflight,
delete-replay, patch-preserves, detail-superset,
method-guards, cache-privacy, auth-schemes, redirect chains,
spec-diff waivers (exact rule+location match), the
spec-conformance writeOnly-leak rule and `nullable: true`
fix, and replay-status equality.
And since then: the filter check (every filtered row must
satisfy the predicate), spec-conformance `uniqueItems` /
`const` / `prefixItems` / `dependentRequired` rules, the `if-match`
optimistic-concurrency check, spec-diff coverage of
the top-level `webhooks` map,
digit-string paging metadata coercion, unknown-role and
method validation, exactly-one secret-source rules, IETF
rate-limit header names, a relative-Link fixture endpoint,
and the page-style empty-pages rule with total_pages drift,
the error-variants malformed-JSON probe, and the spec-conformance /
openapi-plan 2XX-wildcard fallbacks
detection.
