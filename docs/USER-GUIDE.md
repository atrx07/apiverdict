# apiverdict user guide

A behaviour suite for APIs, not another request client. You do not
write assertions for each endpoint; you run invariant checks and the
tool fails CI when a behaviour breaks. This guide is the whole
workflow: install, first run, spec-to-CI, every check, and what each
check cannot prove (that last part matters more than the first).

## Install

Python 3.10+ only, stdlib only, no dependencies, no keys, no
telemetry, no network calls except to the target API.

```bash
pip install .
apiverdict --version
# or, without installing:
python -m apiverdict --version
```

## First run (60 seconds, no real API)

```bash
python -m apiverdict demo
```

You should see the naive fixture FAIL (exit 1) and the fixed
fixture PASS (exit 0). The naive FAIL is the point: if a tool cannot
catch its own seeded bugs, do not trust it against your API.

Two richer fixtures, same proof shape (8 checks each):

```bash
python -m apiverdict casestudy   # shop: bookings, payments, settings
python -m apiverdict library    # library: books, loans, holds, fines
```

Both are localhost fixtures, documented as fixtures, never as real
APIs. `examples/repro-5min.sh` reproduces every headline claim.

## The real workflow: spec to CI

1. **Get your spec as JSON.** apiverdict reads JSON OpenAPI specs
   only. A YAML spec is refused plainly — convert it first with a
   YAML tool of your choice. A half-broken YAML parser that silently
   misread your spec would generate wrong checks, which is worse
   than refusing. OpenAPI 3.x is the supported version: a Swagger
   2.0 spec loads, but body/error checks need 3.x `requestBody`
   and `content` shapes, so a 2.0 spec gets pagination candidates
   and degraded error coverage instead of the full run — convert
   2.0 specs to 3.x for full coverage.
   3.2 specs load the same way; `query` operations
   appear as error-shape targets (their 200 bodies are
   not spec-conformance-judged — judging one needs a
   request body the checker cannot invent, so they are
   listed as unjudged), and `additionalOperations`
   entries are not plan-generated (custom methods sit
   outside the read/write model — the static checks
   judge them).

2. **Generate the plan (no checks run yet):**

   ```bash
   python -m apiverdict openapi --spec openapi.json \
     --base-url https://staging.example.com --json
   ```

   The plan lists pagination candidates (with the reason each was
   picked — a wrong guess is visible, not silent), error-shape
   targets (every POST/PUT/PATCH/QUERY with a requestBody, each
   probed with a payload derived from its own schema), and burst
   candidates (listed, never auto-run — a burst needs a verify
   step from you, and the tool will not invent one).
   It also lists detail-superset candidates (a GET collection
   paired with a GET detail path that is exactly the
   collection plus one template segment), likewise listed,
   not auto-run.

3. **Run the checks and keep the report:**

   ```bash
   apiverdict openapi --spec openapi.json \
     --base-url https://staging.example.com \
     --junit reports/apiverdict.xml
   ```

   Review the plan first with `--plan-only`: it prints the
   generated pagination, error-shape, and burst-candidate
   lines and exits 0 without touching the network, so you can
   see what CI would run before it runs.

   Exit codes are the CI contract, and they do not change:
   `0` pass, `1` a check failed, `2` could not verify. A check that
   cannot verify never counts as a pass — a skipped check must not
   turn a build green. In JUnit XML, INCONCLUSIVE maps to
   `<skipped>`, so a CI job that only reads JUnit must combine it
   with the exit code. Every command accepts `--junit`, including
   single-check commands; runner-mediated reports (`demo`,
   `casestudy`, `library`, `suite`, `check`) additionally carry
   per-check `duration_ms` in evidence, which JUnit surfaces as
   the testcase `time` attribute.
   Fixture runners (`demo`, `casestudy`, `library`) add one
   extra top-level key, `fixture_mode` (naive/fixed/both),
   on top of the locked core report keys; `casestudy`
   and `library` additionally tag the report with
   `case_study` (true / `"library"`) so a report says
   which study it came from.

4. **Wire it into CI.** Copy `examples/ci/github-actions.yml`
   into `.github/workflows/` yourself — the agent push route
   cannot create workflow files (403), so this is a paste, not a
   push. The workflow proves the tool against its own fixtures
   first, then runs your spec checks and uploads the JUnit report.
   For a pre-commit smoke check, see `examples/ci/pre-commit.sh`.

5. **Add the checks the spec cannot generate.** The spec-driven
   run covers pagination, error-shape, and spec-conformance. The
   behaviour checks that need a verify step or a role map are
   configured by hand — see the check list below. That is a
   feature, not a gap: a burst without a final-state check, or an
   idempotency replay without a count, would be green on a skipped
   check, so the tool refuses to pretend.

## Every check: what it proves, what it does not

### burst — simultaneous requests, final-state verification

```bash
apiverdict burst --url http://127.0.0.1:8000/api/book \
  --payload '{"slot": "slot-{round}"}' --n 20 --rounds 3 \
  --verify-url 'http://127.0.0.1:8000/api/count?slot=slot-{round}' \
  --expect-delta 1
```

Fires N identical requests through a barrier gate, repeats for R
rounds, and compares final state before and after each round
(`--verify-cmd` prints one number, or `--verify-url` returns a
count — supply exactly one; passing both is INCONCLUSIVE
rather than silently preferring the command). Verify reads carry
the check's headers by default; `--verify-header` (repeatable)
sends different headers to the verify endpoint when it needs
different auth than the endpoint under test. Status codes are reported but never trusted: a
double-applied operation can answer 201 every time.
**Proves:** the deduplication that actually happened, per round.
**Does not prove:** that no hidden write happened elsewhere — a
PASS is evidence from the state you chose to verify, not proof
about state you did not. With `--idempotency-key`, the same burst
also tests key-based deduplication.
With `--idempotency-key` plus `--compare-responses`, a second
axis applies: all N responses in a round must be identical
(status as well as body) to the one stored response, so a
server that dedupes the write but mints fresh ids per
response still fails; `--ignore-field` strips volatile
top-level fields first. Without a shared key the flag is
INCONCLUSIVE, because differing responses are correct
there.
**Safety:** localhost needs no flag; any other host needs
`--allow-remote` plus a warning banner. This tool creates
resources on purpose and never deletes anything — staging/test
data only, no cleanup, by design.

### pagination — style-agnostic list walker

```bash
apiverdict pagination --url http://127.0.0.1:8000/api/items --page-size 10
```

Walks a list to the end (offset/limit, page/size, cursor, and
`Link: rel="next"` auto-detected — relative Link URLs are resolved
against the page just fetched, as real APIs emit them) and checks duplicates, missing
rows vs the declared total, and order changes between two walks.
Link walks send no paging parameters themselves: if the API
only emits Link headers on a paged request, put the first
page's parameters in the URL
(`.../posts?_page=1&_limit=10 --style link`) and the walker
follows the returned Links verbatim from there.
Cursor tokens are accepted under `next_cursor`, `next_page_token`,
or `cursor`, and a body `next`/`next_url` URL is followed verbatim
(a nested body `links.next`, JSON:API's form, is not followed —
only top-level fields and Link headers are);
`--max-pages` (default 100) caps the walk. Query parameter names
are configurable (`--page-param`, `--size-param`,
`--offset-param`, `--limit-param`, `--cursor-param`) for APIs
that use `_page`/`_limit` or similar, and an `X-Total-Count`
response header is accepted as the total when the body carries
none. A page that returns more rows than the requested limit
fails the walk: a server that dumps the whole list on page 1
must not "pass" pagination without ever paging (the rule only
applies to pages where a limit was actually sent — cursor and
Link walks send none). One honesty limit: for cursor/Link styles,
the absence of a continuation marker IS the end signal by
contract, so a walk that ends after one page passes if that page
is clean. Pick the style matching your API: a wrong style on an
API with no declared total can only judge the first page, and
that PASS means "the first page is clean", not "the list is
complete".
**Proves:** the list as a client sees it is complete and stable.
**Does not prove:** that a cursor is correct under every
concurrent write pattern — see `pagination-concurrent`, which
writes one row mid-walk: offset FAILs with duplicates, cursor
passes, on the same fixture. A cursor also needs a unique sort
tie-breaker (e.g. `ORDER BY created_at, id`); without one, two
rows with equal sort keys can still swap between pages, and that
is a server bug this walker will surface as order instability.

A row field that changes JSON type across pages (an `id` that
is an integer on page 1 and a string on page 2) fails the walk,
because it breaks typed clients. Nulls are exempt: a nullable
field may be an integer on some rows and null on others.

Two or more empty pages inside a server-declared page range
fail the walk: the declared page count overstates the list.
One empty page is tolerated as a boundary artefact (filtered
totals, off-by-one counts). A `total` or `total_pages` value
that changes mid-walk aborts as INCONCLUSIVE — a static
list's metadata must not move, and the concurrent walk is
the place for genuinely dynamic lists.
A non-200 page mid-walk also aborts as INCONCLUSIVE, not
as end-of-list: a 400 there is indistinguishable between
"out of range" and "expired cursor", so the walker refuses
to guess. (When a declared total is already satisfied the
walker stops before fetching the next page, so a
400-beyond-range never arises there.)

### pagination-consistency — same list, two page sizes

```bash
apiverdict pagination-consistency --url http://127.0.0.1:8000/api/items \
  --page-size-a 10 --page-size-b 5
```

Walks the same endpoint at two page sizes and requires the same
full id sequence, the same declared total, and no duplicates or
gaps at either size. This catches bugs the single-size walker
cannot see: a total computed as pages × page_size, a per-page
sort that re-sorts each page independently, and a cursor that
drifts differently at different sizes. A server that caps page
size is not failed for the cap alone — if the walk still returns
the same sequence, the cap is invisible to a walking client.

### pagination-concurrent — one write mid-walk

```bash
apiverdict pagination-concurrent --url http://127.0.0.1:8765/api/feed \
  --page-size 10 --style offset \
  --write-url http://127.0.0.1:8765/api/feed \
  --write-payload '{"name": "feed-new-{seq}"}' --write-after-page 1
```

The generic form of the concurrent-write question: baseline rows
must survive exactly once, whatever the style. Run it once per
style you support; the style that survives is the one to ship.

### error-shape (`errors` command) — error consistency, not just non-200s

```bash
apiverdict errors --endpoint POST:http://127.0.0.1:8000/api/book \
  --endpoint POST:http://127.0.0.1:8000/api/order
```

Sends an invalid payload to each endpoint and clusters responses
by (status, sorted top-level keys, error-field name). One cluster
is a pass; more than one is a fail. A 2xx on invalid input is a
fail, and so is a 5xx: invalid input must not crash the server,
so a consistent 500 shape still fails — consistency must not
mask a crash. The same 5xx rule applies to `error-variants`. `--require-rfc7807` fails unless every endpoint returns
RFC 7807/9457 problem details; `--require-rfc9457` adds strict
checks (status member == HTTP status, type is an absolute URI or
`about:blank`, title non-empty, media type
`application/problem+json` — a problem body served as plain
`application/json` is not RFC 9457 under the strict flag,
whatever its fields say).
**Proves:** clients need one error handler, not one per endpoint.
**Does not prove:** that the errors are good — a single ugly
shape passes, because this check judges consistency, not taste.
Related: `error-leak` fails on tracebacks, filesystem paths, or
SQL in error bodies and headers; `error-payloads` (inside the OpenAPI run)
derives each endpoint's invalid payload from its own requestBody
schema instead of one generic payload. And `error-variants`
(`apiverdict error-variants --url ...`) is the other axis of the
same check: one endpoint, many invalid payload kinds (missing
field, wrong type, unknown field) — one shape required — plus
the malformed-JSON parser probe, which lives there, not here
(one check, one question, composed by the runner). An
endpoint that is consistent across endpoints but not across
payload kinds still forces clients to branch on failure kind.
`error-variants` deliberately takes no RFC flags: RFC 9457
strictness is error-shape's axis (`--require-rfc9457`), and
the suite runs both — one check, one question, composed by
the runner.

### error-variants — one endpoint, many invalid payload kinds

```bash
apiverdict error-variants --url http://127.0.0.1:8000/api/book
```

The other axis of error-shape: one endpoint, many invalid
payload kinds (missing field, wrong type, unknown field by
default; `--payload` JSON, repeatable, overrides the set).
All kinds must share one error shape, and a 5xx on any kind
fails like a 5xx in error-shape. Additionally, a syntactically
malformed JSON body is always probed: it must be rejected
with a 4xx, never accepted (2xx) or crashed on (5xx). Its
shape is exempt from the clustering — only the status class
is judged — because framework parser errors legitimately
differ from app validation shapes. Evidence records
`malformed_status`. This check deliberately
takes no RFC flags: RFC 9457 strictness is error-shape's
axis, and the suite runs both — one check, one question,
composed by the runner.

### error-leak — tracebacks, paths, and SQL in errors

```bash
apiverdict error-leak --endpoint POST:http://127.0.0.1:8000/api/book
```

Sends invalid input and scans error bodies AND headers for
leaked internals (tracebacks, filesystem paths, SQL
fragments) with conservative patterns, so normal errors never
match. A leak fails, naming the header when the leak is in
one. Unlike error-shape, this check does not judge
consistency — a consistent leak is still a leak.

### idempotency-variants — same key, different payload

```bash
apiverdict idempotency-variants --url http://127.0.0.1:8000/api/payments \
  --verify-url http://127.0.0.1:8000/api/payments/count
```

Same key twice (delta must be 1), then the same key with a
different payload (delta must stay 0, and a 5xx on the
alt-payload send fails while a 4xx key-conflict is
acceptable). This is the Stripe/IETF rule: a retry with a
different payload must fail loudly, never silently apply or
silently ignore.

### auth-matrix — every role × every endpoint

```bash
apiverdict auth-matrix --roles @examples/auth-matrix-roles.json \
  --targets @examples/auth-matrix-targets.json
```

Roles are header sets you supply; targets declare which roles are
allowed. Any allowed role denied, or any other role allowed, is a
fail. A transport failure in any cell makes the whole matrix
INCONCLUSIVE — a matrix with a hole must not read as a pass.
A target naming a role not present in `--roles` is a
configuration error (exit 2), not a leak — a typo in a
role name must not read as a privilege finding.
This also expresses ownership: a resource URL with
`allowed_roles` naming only its owner fails if any
other role can read it.
Credentials come from a config file naming environment variables
(`--config apiverdict.json --env staging`); the tool reads only
the named variables, never auto-scrapes the environment, and
never prints a credential value in reports or logs.

### rate-limit — sequential, not a load test

```bash
apiverdict rate-limit --url http://127.0.0.1:8000/api/search --requests 30
```

Sends requests sequentially and reports where 429s start and
whether they stay consistent. No 429 when limiting is expected is
a fail; a 429 followed by a 200 is a fail (a limit that lifts
mid-run is not a limit a client can rely on). A 5xx anywhere is
INCONCLUSIVE — a crashing server is not "rate limited".
Evidence records IETF `RateLimit-Limit` / `Remaining` /
`Reset`, `RateLimit-Policy`, and the draft's current
combined `RateLimit` header when the server sends them,
and a `Retry-After` on a 429 must parse
(unparseable values fail, RFC 9110).
**Does not prove:** throughput, latency, or p95 — that is k6's
job, and a load test passing can hide duplicate bookings.

### idempotency — same key, twice, sequentially

```bash
apiverdict idempotency --url http://127.0.0.1:8000/api/payments \
  --payload '{"order": "ord-1"}' \
  --verify-url 'http://127.0.0.1:8000/api/payments/count?order=ord-1'
```

Delta 1 across both sends is a pass; delta 2 is a fail (a client
retry double-charged). `--compare-responses` adds a second axis:
the replay response must equal the original response, so a
replay that mints a new id while the delta stays 1 still fails;
`--ignore-field` (repeatable) strips volatile top-level fields
such as timestamps before comparing. It is opt-in because the
delta is the core promise. `idempotency-variants` adds the harder
case: same key with a *different* payload must add 0 more side
effects. This matters more every year agents retry on their own:
a retry without a key is a second order, and a contract that
promises idempotency should be tested like a promise.
**Does not prove:** key behaviour under simultaneous sends —
that is the burst check's `--idempotency-key` mode. Either alone
is half the proof.

### idempotency-scope — same key, two different endpoints

```bash
apiverdict idempotency-scope \
  --url-a http://127.0.0.1:8000/api/charges \
  --url-b http://127.0.0.1:8000/api/refunds \
  --verify-url-a http://127.0.0.1:8000/api/charges/count \
  --verify-url-b http://127.0.0.1:8000/api/refunds/count
```

Keys are scoped per operation: a key for /charges must not be
reusable on /refunds. The check sends the same key to both
endpoints and reads both states. B creating its own resource
(delta 1) is a pass; B returning 2xx with delta 0 is a fail — it
silently replayed A's stored result for a different operation.
A B rejection is INCONCLUSIVE, not a pass or a fail: a 4xx
cannot be told apart from a validation rejection without more
signal, so servers in the Stripe model (same key + different
request -> 422) land here. The check proves absence of silent
replay, not scoped creation for reject-model servers.
**Does not prove:** anything about the key on one endpoint
alone — that is `idempotency` and `idempotency-variants`.

### delete-replay — DELETE twice, second must not change state

```bash
apiverdict delete-replay --url http://127.0.0.1:8000/api/items/1 \
  --verify-url http://127.0.0.1:8000/api/items/count
```

DELETE is idempotent by definition, but the idempotency check
assumes creations (delta >= 0) and cannot express it: a correct
second DELETE returns 404 (already gone), not the stored
response. This check DELETES once (state must drop by exactly
1), then DELETES again: the replay must return 2xx or 404/410
and leave the state exactly where the first DELETE left it. A
replay that errors, or that changes state again, fails. A first
DELETE that is not 2xx is INCONCLUSIVE — nothing was deleted
to replay.
**Does not prove:** that the resource is really gone from
every read path — only that the counted state is stable
across the replay.

### patch-preserves — PATCH must not act like PUT

```bash
apiverdict patch-preserves --url http://127.0.0.1:8000/api/items/1 \
  --field name --value '"new name"'
```

Reads the resource, PATCHes exactly one field, reads again:
the patched field must equal the new value, and every other
field present before must be present after with the same
value. A PATCH that wipes or alters fields it was not given
fails, naming them. Server-added keys (timestamps, ...) are
ignored. A failed read or a non-2xx PATCH is INCONCLUSIVE.
**Does not prove:** that the PATCH validation is correct —
only that unspecified fields survive it.

### webhook — signature, duplicate, replay

```bash
apiverdict webhook --url http://127.0.0.1:8000/api/webhooks/receiver \
  --secret-env WEBHOOK_SECRET \
  --verify-url 'http://127.0.0.1:8000/api/webhooks/count?event_id=evt-1'
```

Signing schemes: `--scheme apiverdict` (default, the check's own
scheme, verified against the parsed body), `--scheme stripe`
(`Stripe-Signature: t=<ts>,v1=<hex>` over `<ts>.<body>`, event id
injected as body `id`, signs the exact bytes sent so it works
against real Stripe-style handlers), and `--scheme github`
(`X-Hub-Signature-256` over the raw body; GitHub carries no
timestamp, so the stale/future probes are skipped for it instead
of false-failing). Valid signed event must be acked, tampered body rejected
(401/403), duplicate acked without double-applying (proven only
with a verify step — without one, the duplicate cell is reported
as ack-only and does not upgrade the verdict; a 409 on the
duplicate send counts as a recognised duplicate, not a failure),
stale and future-dated replays
rejected when timestamp enforcement is on (default, 5-minute
tolerance). Signing is HMAC-SHA256 over
`{timestamp}.{event_id}.{body_json}`. Prefer `--secret-env` over
`--secret`: a CLI secret is visible in shell history and process
lists.

### cache / cache-etag — stale-after-write

```bash
apiverdict cache --read-url http://127.0.0.1:8000/api/settings \
  --write-payload '{"theme": "dark"}'
apiverdict cache-etag --read-url http://127.0.0.1:8000/api/settings
```

Read, write, read again: if the second read equals the first
after a successful write, the read path is stale. ETag adds the
conditional-request half: ETag present, `If-None-Match` gives 304,
ETag changes after a write. A fresh ETag with a stale body is
still stale — the verdict rests on the body, not the header.
For the write half of the ETag story — whether a stale
`If-Match` is rejected instead of silently applied — see
`if-match` below.

### if-match — stale writes must be rejected, not silently applied

```bash
apiverdict if-match --read-url http://127.0.0.1:8000/api/settings \
  --write-payload '{"theme": "dark"}'
```

The write half of the ETag story (cache-etag covers the read
-half): GET the resource, write with `If-Match` set to its
current ETag (must succeed — a 412 here fails, the server
rejects its own validator), then write again with the now-stale
ETag (must be rejected with 412, or 409 as a conflict
refusal — a 2xx here fails, the stale precondition was
applied and the first writer's update is silently lost). A
final read confirms the rejected stale write moved nothing:
a moved ETag after a rejected write fails too. No ETag on
the initial read is INCONCLUSIVE, not a fail — an endpoint
that never claims ETag support claims no precondition
contract — and a write that leaves the ETag unchanged is
likewise INCONCLUSIVE (a no-op write cannot judge
staleness). Mutating, so the remote guard applies.
**Does not prove:** distributed locking or versioning across
resources — this judges one resource's precondition contract,
not a multi-resource transaction.

### head-consistency (`head` command) — HEAD must agree with GET

```bash
apiverdict head --url http://127.0.0.1:8000/api/items \
  --url http://127.0.0.1:8000/api/items/1
```

Read-only (GET + HEAD), so no remote guard is needed. For each
URL with a 2xx GET, the HEAD response must agree: any non-405
status disagreement fails, and Content-Type / Content-Length
must match when present on both sides. A HEAD 405 is unjudged,
not a failure — some servers deliberately disable HEAD — and
a missing header on either side is not judged either (a
chunked GET carries no Content-Length to compare). Nothing
judged at all is INCONCLUSIVE, never a vacuous pass.
**Does not prove:** HEAD bodies (there are none by definition)
or any write-path behaviour.

### cors — preflight, for browser-facing APIs only

```bash
apiverdict cors --url http://127.0.0.1:8000/api/items \
  --method POST --origin https://app.example.com
```

Sends a real preflight (OPTIONS + Origin +
Access-Control-Request-Method) and requires a 2xx status,
Access-Control-Allow-Origin (an echo of the request Origin, or
`*`), and Access-Control-Allow-Methods listing the requested
method. Findings name exactly what is absent. Run this only
against APIs meant to be called from browsers: a
server-to-server API that deliberately omits CORS will fail
it, and that is a wrong-check-for-that-API user error, not a
server bug.
**Does not prove:** that the browser actually accepts the
response (credentialed requests, `Allow-Headers`, and
`Vary: Origin` handling are separate concerns).

### detail-superset — detail must cover its list row

```bash
apiverdict detail-superset \
  --list-url http://127.0.0.1:8000/api/items \
  --detail-url http://127.0.0.1:8000/api/items/{id}
```

Fetches a list (first page only) and the first row's detail
response: every list-row field must be present in the detail
object with the same JSON type (nulls exempt on either side).
Value differences are not judged — data may change between
the two GETs — and extra detail fields are fine. An empty
list, an unresolvable id, or a failed detail fetch is
INCONCLUSIVE.
**Does not prove:** that every row's detail agrees — only
the first row is compared, so this is a shape smoke signal,
not a full-list audit.

### method-guards — rejected methods must 405 with Allow

```bash
apiverdict method-guards --endpoint DELETE:http://127.0.0.1:8000/api/items
```

For each user-declared METHOD:url pair (a method you assert
must be rejected there), the response must be 405 with an
Allow header that does not list the rejected method. A 2xx
means the method was accepted though declared rejected; a 405
without Allow, or an Allow listing the method itself, fails.
WARNING: only probe methods you expect rejected, on test
doubles first — probing a method the server does support can
execute it, so mutating-method probes carry the standard
remote guard.
**Does not prove:** anything about methods you did not
declare — this check judges exactly the pairs you give it.

### cache-privacy — one user's response must not serve another

```bash
apiverdict cache-privacy --url http://127.0.0.1:8000/api/me \
  --header-a 'Authorization: Bearer TOKEN_A' \
  --header-b 'Authorization: Bearer TOKEN_B'
```

Fetches the same URL as two different callers and requires
the responses to differ — a cache keyed by URL alone (missing
caller scope, or a missing `Vary: Authorization`) serves A's
response to B. Run this only on endpoints you assert are
user-specific: an endpoint that legitimately returns the same
public data to everyone will fail it, and that is a
wrong-check-for-that-endpoint user error. `--ignore-field`
(repeatable) strips volatile top-level fields before
comparing. A denied second caller is INCONCLUSIVE here —
denials are the auth-matrix check's finding.
**Does not prove:** that the leak is the cache (a shared
account or a broken auth layer gives the same symptom) — it
proves the responses are identical when they must not be.

### redirect — the chain must be finite, acyclic, https-preserving

```bash
apiverdict redirect --url http://127.0.0.1:8000/api/old-path
```

Walks the redirect chain hop by hop (nothing followed
automatically, so cycles stay visible): a cycle, more than
`--max-hops` hops (default 10), an https-to-http downgrade,
or a 3xx with no `Location` fails. The final destination's
status is reported but not judged — a chain landing on a 404
has a dead destination, which is a different question from
whether the chain itself is sane. Read-only, no remote guard.
**Does not prove:** that the final destination is the
intended one, or that the chain is minimal — only that it
is finite, acyclic, and never downgrades https.

### filter — a list filter must actually filter

```bash
apiverdict filter --url http://127.0.0.1:8000/api/items \
  --field status --value active
```

Walks the list with `?status=active` applied and requires
every returned row to satisfy the predicate (top-level
field only; comparison is stringified and casefolded, so 5
matches "5" and True matches "true"). A violating row
PROVES the filter did not apply — unlike sort order, where
identical orders can be coincidence, a predicate violation
is exact. That asymmetry is why this check exists and a
sort-ignored check does not: sort coincidence would
false-FAIL real APIs. INCONCLUSIVE when the walk fails, no
rows come back, or the field appears in no row (a wrong
field name is a config error, not a finding). Read-only, no
remote guard.
**Does not prove:** that the filter works for any other
field or value — only for the one predicate tested — or
that the unfiltered list is complete (that is
pagination's job).

### auth-schemes — a token must work under its own scheme only

```bash
apiverdict auth-schemes --url http://127.0.0.1:8000/api/me \
  --token-env API_TOKEN
```

Sends the token under its correct scheme (default Bearer;
must succeed, else INCONCLUSIVE) and then under Basic,
Digest, Token, and bare. Any 2xx under a wrong scheme fails,
naming the scheme. Supply exactly one of --token and
--token-env; giving both exits 2 (same rule as webhook's
--secret/--secret-env). The token never appears in evidence, and
`--token-env` keeps it out of shell history (suite entries
use `token_env` the same way webhook entries use
`secret_env`).
**Does not prove:** that the token is valid for the endpoint
— a denied baseline is INCONCLUSIVE, and role-level access
is the auth-matrix check's question.

### spec-errors — static: does the contract document errors?

```bash
apiverdict spec-errors --spec openapi.json
```

No network. For every operation, is at least one error response
documented (any 4xx/5xx, or "default")? 3.2 `query`
operations and `additionalOperations` entries count as
operations here, exactly like fixed-field methods. This is opt-in and
opinionated — it is not part of the automatic OpenAPI run, because
some teams document only success responses and rely on a global
error format. If that is your policy, write the global format into
the spec once and this check passes everywhere; what it stops is
the policy eroding one undocumented endpoint at a time.

### spec-diff — static: did the spec change break consumers?

```bash
apiverdict spec-diff --old-spec openapi-v1.json --new-spec openapi-v2.json
```

No network. Compares two specs and FAILs on breaking changes:
removed paths/operations (including 3.2 `query` operations
and `additionalOperations` entries, which compare exactly
like fixed-field operations), removed top-level webhooks
(3.1+) or their operations (locations read `POST webhook
<name>`; subscribers are consumers too)/response codes/server URLs
(global and per-operation), removed request parameters or response
properties, parameters that became required, operations that
gained a security requirement, operationId renames (generated
clients key on the id),
request properties added to required, response properties that
became optional (the server no longer guarantees a field the
client may read), type changes (widening
like string -> ["string", "null"] is not a change that breaks
readers, and neither is a path-template rename like {id} ->
{userId}), and shrunk enums. Purely additive changes (new paths,
operations, optional parameters, response properties, grown
enums) are listed as non-breaking and never fail the check.
Response-code comparison is by exact key: replacing a 200 response with a 2XX wildcard reads as removed-plus-added (review it), not as a silent widening — the wildcard's schema may differ from the old 200 schema, and the diff does not silently assume it matches.
Schema diffs (properties, types, enums, required) examine
`application/json` bodies only; other media types are compared
by presence alone (removed = breaking, added = non-breaking).
This is spec-vs-spec detection, not consumer-aware
verification: it cannot tell you whether anyone reads a removed
field, only that a promised field is gone. In CI, run it on
every spec change and gate the merge on exit 0. Also available
in suite configs as `{"check": "spec-diff", "old_spec": ...,
"new_spec": ...}` (paths resolved relative to the config file).

### spec-diff waivers — blessing an intentional break, loudly

```bash
apiverdict spec-diff --old-spec old.json --new-spec new.json \
  --waiver-file waivers.json
```

`waivers.json` holds a list of `{"rule", "location"}`
objects (optional `"reason"`). Matching is exact on rule AND
location, so a waiver can never hide a break it does not
name: an unmatched waiver is reported as `unused_waivers`,
and a break no waiver names still fails. Waived breaks stay
in evidence under `waived` with their reason — a PASS with
waivers names its waived count in the summary. Webhook
breaks waive the same way (`{"rule": "webhook-removed",
"location": "webhook petAdded"}`). Suite entries
use `waiver_file`, resolved relative to the suite file like
the spec paths. Without waivers, spec-diff fails on any
breaking change; with them, a team can bless an intentional
break without dropping the gate.

### spec-examples — static: do the spec's examples match its schemas?

```bash
apiverdict spec-examples --spec openapi.json
```

No network. Checks every requestBody and response example
(including named `examples` entries and `schema.example`)
against its own schema, with exactly spec-conformance's
rules (see that section above — the list grows as
spec-conformance grows, and the two never diverge).
3.2 `query` operations and `additionalOperations`
entries are checked here too. An example that
violates its schema is a self-inconsistent spec — consumers
copy examples. A spec with no examples passes with 0 checked:
this judges consistency, not coverage. Also available in suite
configs as `{"check": "spec-examples", "spec": ...}`.

### spec-conformance — live responses vs the spec

Documented error responses are judged too: a 404 body that
violates its declared 404 schema fails, exactly like a 200
body does (exact status, then the 4XX/5XX wildcard, then
`default`). A documented status with no declared schema stays
unjudged.

```bash
apiverdict spec-check --spec openapi.json --base-url https://staging.example.com
```

For each GET endpoint with a usable 200 (or 2XX) JSON schema, checks
required fields, primitive types (integer rejects booleans),
enums, scalar bounds (pattern, numeric bounds, length and
item/property counts), `dependentRequired` dependencies (a
trigger field present without its dependent field fails),
and nested objects/arrays recursively
(depth capped at 10). Local `$ref`s are resolved;
remote refs are never fetched and the endpoint is skipped, not
judged. Templated paths and required query params are filled
from examples/defaults/enums/type placeholders; an endpoint that
cannot be fetched is recorded as *unjudged* and the check
continues — one bad endpoint no longer aborts the run, and a
check that judges nothing is INCONCLUSIVE. A non-200 status the
spec documents (exact code, `default`, or a `2XX` wildcard) has
no 200 body to check (a documented error schema is still
judged, as above) — but a status
the spec never documents is a FAIL: the contract did not cover
that response. For a 200 response, a spec that declares only
the 2XX wildcard still promises a success shape: the body is
judged against the wildcard schema, not skipped — and the
same fallback applies to the declared Content-Type rule.
For endpoints that require auth, pass it (`--config` +
`--env`, or `--header`): an unauthenticated probe that gets
a 401 the spec never documents fails as an undocumented
status — which is the probe's missing auth showing up as a
contract gap, so read that FAIL as "authenticate the probe
or document the 401" before blaming the server.
`query` operations (3.2) are listed in `unjudged` with
the reason, never silently dropped: judging a QUERY 200
needs a request body the checker cannot invent.
Values under `additionalProperties` schemas are checked, so an
inventory-style map endpoint is judged on its values, not just on
being an object.
**Does not prove:** full JSON Schema conformance — no format
assertions and no allOf/anyOf/oneOf. That needs a real
validator; this catches the common breaking change (a field
renamed, dropped, or retyped while the endpoint still returns
200). See `docs/REAL-WORLD-VALIDATION.md` for a real public-API
run, including what it found and what it could not claim.

`openapi --plan-only` also lists detail-superset candidates
(a GET collection paired with a GET detail path that is
exactly the collection plus one template segment) and burst
candidates. Both are listed, not auto-run: the run set stays
pinned to pagination + error-shape, and the user runs
`detail-superset` or `burst` explicitly with their own inputs.

## Suite: one command, all configured checks

```bash
# Start a fixture server first (the example suite points at it):
python -m apiverdict fixture --mode fixed --port 8765
apiverdict suite --config examples/suite.json
apiverdict suite --config examples/suite.json --parallel --junit reports/apiverdict.xml
```

A suite file can also carry top-level `config` (an apiverdict
config file path, resolved relative to the suite file) plus
optional `env`: that environment's auth headers are applied to
every check that accepts `headers`, unless the entry sets its
own headers (entry wins). Secrets stay in environment variables
via the config file, never in the suite file.
`suite --dry-run` validates a config and prints the check names
that would run, without running anything (names only, never
arguments, so a resolved webhook secret cannot leak into
output). A suite config is a JSON file with a `checks` list in run order;
each entry names a check and passes that check's kwargs (see
`examples/suite.json`). Config problems fail fast at load time
with exit 2: unknown check names and unknown kwargs (checked
against the function's signature, so a typo like `pageSize`
cannot silently become a default) are errors before any check
runs. `spec-conformance` takes `spec` (a spec file, resolved
relative to the config file) plus `base_url`; `spec-errors`
and `spec-examples` take `spec` only; `spec-diff` takes
`old_spec` and `new_spec` (same relative resolution). Webhook entries use `secret_env`, never a
literal secret in the file.

Two things to know before wiring a suite into CI. First, each
mutating entry must carry its own `allow_remote` flag — there is
no global switch, so a suite cannot accidentally arm every write
at once. Second, run suites against fresh state: burst and
idempotency checks use fixed slots/keys, so a second run against
the same server state reports delta 0 (the slot is already
booked) — that is the check working, not a bug. The bundled
fixture has `POST /api/reset` for exactly this.

Before running a suite (or any `--config` command) against
a new environment, pre-flight the wiring with
`config-check`: it loads the config file, resolves the
named environment (or the default), and prints the
environment, its base URL, and what auth it resolves to —
without running any check and without printing secrets.
A bad file, unknown environment, or unresolvable auth
exits 2 here instead of mid-run.

## Flags at a glance

Every command also documents itself via `--help`. The flags
most often missed on a first read:

- `--header 'Name: value'` (repeatable, every live command):
  extra headers; wins over `--config` values.
- `--verify-header 'Name: value'` (repeatable, burst /
  idempotency / idempotency-variants / idempotency-scope /
  webhook): headers for verify reads only; defaults to the
  check's own headers when omitted.
- `--id-field` (pagination family, detail-superset): row id field, dotted paths
  allowed (`item.id`). `--compare-field` (cache): field to
  compare across reads, dotted paths allowed.
- `--expect-limit` (rate-limit): the limit the server is
  expected to enforce. `--write-method` (cache / cache-etag / if-match /
  pagination-concurrent): the write verb, must mutate.
- `--invalid-payload` (error-shape): the invalid payload sent
  to every endpoint (JSON). `--payload-a` / `--payload-b`
  (idempotency-scope): per-endpoint payloads.
- `--key` / `--key-header` (idempotency family): the key value
  and header name. Burst uses `--idempotency-key` /
  `--idempotency-header` for the same idea. `--verify-cmd-a` / `--verify-url-a` /
  `--verify-cmd-b` / `--verify-url-b` (idempotency-scope):
  one verify source per endpoint, exactly one each.
- `--body` / `--event-id` (webhook): the event body (JSON)
  and id. `--no-enforce-timestamp` (webhook): the
  stale/future probes still run, but their acceptance is
  not judged, for handlers that deliberately do not
  enforce timestamps. `--timestamp-tolerance` (webhook):
  expected tolerance in seconds (default 300).
- `--scheme` (webhook): signing scheme — apiverdict,
  stripe, or github. `--token` / `--token-env`
  (auth-schemes): the credential, exactly one of them.
- `--waiver-file` (spec-diff): JSON list of
  {rule, location} waivers. `--max-hops` (redirect):
  hop limit (default 10). `--field` / `--value` (filter):
  the filter predicate.

## Troubleshooting

- **Everything is INCONCLUSIVE, exit 2.** The target is
  unreachable, or a verify step failed, or a spec produced no
  runnable checks. INCONCLUSIVE is the tool refusing to guess —
  read the per-check summary; it names the failing URL or step.
- **`refused non-localhost target without --allow-remote`.**
  Mutating checks (burst, idempotency, idempotency-variants,
  idempotency-scope, delete-replay, patch-preserves, webhook,
  cache, cache-etag and if-match writes, pagination-concurrent's
  mid-walk write, rate-limit with a mutating method,
  auth-matrix with a mutating-method target, and
  method-guards with a mutating-method probe) refuse remote
  targets by default. Add `--allow-remote` only
  for staging/test data, and only after reading the Safety
  section of the README.
- **A check fails with "response body exceeded 2000000 bytes".**
  The tool refuses bodies over 2 MB rather than reading an
  unbounded dump into memory. That is a guard, not a verdict
  about your API — but an error body or list page over 2 MB is
  worth a look on its own.
- **A request hangs.** Every check takes `--timeout` (default
  10s, per request). A timeout is a transport failure, which
  makes the check INCONCLUSIVE, not a crash.
- **No built-in retries — by design.** A check-level retry
  would mask real flakiness (a server that answers on the
  second try is flaky, and a CI gate should say so). If a run
  is INCONCLUSIVE on a transient failure, retry the CI job,
  not the check.
- **"spec produced no runnable checks".** Your spec has no
  pagination candidate and no POST/PUT/PATCH/QUERY with a requestBody,
  or its GET endpoints have no usable 200 / 2XX schemas. Check the
  plan output — it lists what was found and why.
- **Spec-conformance judges fewer endpoints than you expected.**
  Read `skipped` (no usable schema, empty array response, string
  schema) and `unjudged` (non-200, transport failure) in the
  evidence. Both are reported, not hidden, and both have
  different fixes: a schema problem vs a target problem.
- **Parallel runs disagree with sequential runs.** `check
  --parallel` runs the trio concurrently and is safe only for
  independent endpoints (documented in its help). If two of
  your checks write to the same resource, run them sequentially
  — parallel checks that interact are testing each other.
 - **check against a real API gives INCONCLUSIVE or odd results.**
  `check` is the v1 trio wired to fixture-shaped endpoints
  (`/api/book`, `/api/items`, `/api/order`, `/api/count`). It is
  for the bundled fixture (and fixture-shaped test doubles), not
  for staging: against a real API, use the single-check commands
  (which take your real endpoints) or `spec-check`.
- **CLI argument rejected with exit 2.** Page sizes, burst
  sizes, rounds, and request counts must be positive; URLs must
  be http(s) with a host; methods must be real HTTP methods.
  The CLI validates before any network call, so exit 2 here
  means "fix your command", not "the API failed".
- **A suite run takes very long in CI.** Every request carries
  the `--timeout` (default 10s), and most checks abort on the
  first transport failure (burst records transport errors as
  data instead, and runs its threads in parallel) — but a
  server that responds just under the timeout on every request
  stretches a sequential check to roughly requests × timeout
  (a 30-request rate-limit check at 9.9s per response takes
  ~300s). Lower `--timeout` for slow CI lanes; the timeout is
  per request, not per check, by design (a per-check timeout
  cannot safely kill a hung thread).

## What apiverdict will NOT do

- Load / performance testing (no throughput, latency, p95).
- Full JSON Schema validation, GraphQL, or multi-endpoint chains
  (cart → checkout, redeem → confirm).
- Automatic cleanup / rollback of created resources. Never
  deletes, by design — deleting is the user's job, stated
  plainly, not hidden.
- Anything requiring a browser, a paid API, or a key to try it.
  Stdlib only, $0.
- Security-header / policy scanning (HSTS, CSP,
  X-Content-Type-Options): that is a scanner's job, and
  gateways strip headers often enough to false-FAIL real
  APIs. Same for vulnerability scanning (SQL injection,
  XSS payloads): a scanner's job — error-shape and
  error-variants probe invalid input for consistency,
  not exploitability, and error-leak covers only what
  error responses themselves disclose.

## Adding a check

Each check is a module under `apiverdict/` returning a `Verdict`
(PASS / FAIL / INCONCLUSIVE), registered in `apiverdict/checks.py`,
given its own subcommand, listed in `list`, and added to the
suite registry. A new check needs no change to the core
runner's shape. (The `check` command itself is still the v1
trio wired to fixture-shaped endpoints — it is not the
registry.) Register it, add a fixture mode for it if it
needs one, and add the naive-FAIL / fixed-PASS proof —
against the shared fixture or a throwaway server; a check
without a failing case is a check that has never been seen
to catch anything.
