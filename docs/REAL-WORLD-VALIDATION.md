# Real-world validation attempt (2026-11-04)

This is the closest the local-only run gets to a real-API test — and
it is deliberately limited, so read the caveats before the results.

## What was actually run

- Target: Swagger Petstore v3 demo server, `https://petstore3.swagger.io/api/v3`
  (a public demo server, not a real pet retailer; its data is shared
  and writable by anyone).
- Spec: the server's own `openapi.json`, fetched 2026-11-04 (13 paths,
  OpenAPI 3.0.x). Only GET endpoints were probed — a handful of
  requests, each with a 15s timeout.
- Command:
  `python -m apiverdict spec-check --spec /tmp/petstore-spec.json --base-url https://petstore3.swagger.io/api/v3 --timeout 15`

## What was NOT run, and why

- No POST/PUT/DELETE probes against Petstore. The error-shape check
  sends invalid payloads to write endpoints, and the burst/idempotency/
  webhook/cache checks create resources on purpose. Running those
  against a shared demo server would write junk into other people's
  demo data, so they were not run. The fixture case studies remain
  the only place those checks have been exercised.
- No pagination walk against Petstore: its list endpoints have no
  paging parameters in the spec, so a walker would be testing
  parameters the API does not document.

## Result: spec-conformance FAIL (exit 1), 3 endpoints judged

A second, static check on the same spec passes cleanly, and the
contrast is the point:

```bash
python -m apiverdict spec-errors --spec /tmp/petstore-spec.json
# PASS: all 19 operation(s) document an error response
```

Petstore's contract documents errors; its live demo data violates
its schemas. The spec is honest, the data is polluted — two
different verdicts, from two different checks, and neither one
substitutes for the other.

Checked (3):
- `GET /pet/findByStatus?status=available` — FAIL finding: one pet in
  the returned array (index 17) is missing the required field `name`.
- `GET /store/inventory` — conforms, now genuinely value-checked:
  the schema is an object with `additionalProperties: integer`, and
  the check validates every inventory value against that schema
  (this was added during this run — before it, inventory "passed"
  as a bare object, checking nothing; see the note below about the
  first version of this check).
- `GET /store/order/1` — FAIL finding: `status` is
  `orignal76a67b53<>"'&`, not in the enum
  `['placed', 'approved', 'delivered']`.

Unjudged (2, correctly not counted as failures):
- `GET /pet/1` — HTTP 404 (the filler id does not exist on the demo).
- `GET /user/1` — HTTP 500 from the demo server. Both operations
  document a `default` response, so these statuses are documented
  and stay unjudged; since this run, the check FAILs on a status
  the spec does not document at all (unit-tested — Petstore's
  `default` responses mean the live verdict is unchanged).

Skipped (3): `GET /pet/findByTags` (returned an empty array, so there
was nothing to check against the item schema), `GET /user/login`
(string schema — the check judges objects/arrays), `GET /user/logout`
(no 200 JSON schema).

## Honest interpretation

Both FAIL findings are almost certainly **demo-data pollution, not
server code bugs**: Petstore accepts whatever anyone POSTs, so a pet
without a `name` and an order with a junk status are exactly what a
shared, writable demo accumulates. What the run does prove is the
useful, narrower claim: the spec-conformance check, pointed at a
real public spec and server, judges real responses, reports real
schema violations with exact locations, and does not turn filler-id
misses into false failures.

Findings about the tool itself from this run, all fixed in this
codebase now: the first version of this check aborted on the first
non-200 endpoint, did not fill required query params (so
`findByStatus` without `?status=...` returned 400 and killed the
run), and did not validate `additionalProperties` values (so
`/store/inventory` "passed" as a bare object, checking nothing).
Now: required query params are filled from examples/defaults/
enums, unjudged endpoints are recorded while the check continues,
and additionalProperties values are checked (inventory passes
that stronger check too).

## GET error probes — now run through the error-shape check itself

GET-only, non-mutating (the check's `GET:` endpoints; no POST
probes were run remotely, for the reasons above). Command:

```
apiverdict errors \
  --endpoint GET:https://petstore3.swagger.io/api/v3/pet/999999999 \
  --endpoint GET:https://petstore3.swagger.io/api/v3/pet/abc \
  --endpoint GET:https://petstore3.swagger.io/api/v3/store/order/999999999 \
  --timeout 15
```

Result, 2026-11-04: **FAIL, exit 1, 2 distinct error shapes
across 3 endpoints** — `(404, ('<non-json>',), None)` for both
not-found URLs (plain-text bodies `Pet not found` /
`Order not found`) vs `(400, ('code', 'message'), 'code')` for
the bad-input URL (JSON). So the check itself, not just a manual
probe, catches the demo's GET-error inconsistency: not-found is
plain text, bad input is JSON. POST error-shape probes were still
not run remotely (they would write junk into shared demo data);
the POST side of the check is proven only on fixtures.

## Static checks against the same spec (2026-11-04)

No network beyond fetching the spec itself:

- `spec-errors`: PASS — all 19 operations document an error
  response. Combined with the live findings above, the story
  sharpens: the contract documents errors everywhere, but the
  live error shapes are inconsistent (plain-text 404s vs JSON
  400) and the live data violates two schemas.
- `spec-examples`: PASS with 0 examples checked — the Petstore
  spec carries no examples at all, so the check correctly
  reports nothing judged rather than a hollow pass. (A spec
  with examples would be judged field by field.)
- `spec-diff` of the spec against itself: PASS, 0 breaking —
  the expected identity result, confirming the differ does not
  false-flag a real-world spec's own constructs.

## Live pagination walk: JSONPlaceholder (2026-11-04)

GET-only, non-mutating, 11 requests total:

```
apiverdict pagination --url https://jsonplaceholder.typicode.com/posts \
  --style page --page-param _page --size-param _limit \
  --page-size 10 --timeout 15
```

Result: PASS, exit 0 — walked 11 pages (10 full pages plus the
empty 11th that ends the walk), 100 unique rows, no duplicates,
no gaps, total 100 (from the X-Total-Count header, since the
body is a bare array with no total field). A later fix
(cycle 73) also turned order_stable from None to True here:
the order-stability second walk had been re-running with
default param names instead of _page/_limit. This is the first
live pagination proof: previously pagination was fixture-only,
because the walker hardcoded page/size names and could not
speak _page/_limit APIs at all. The 11th empty page is
expected page-style behaviour (the walk advances while a page
is full and stops on the short page), not an extra fetch bug.

Same target, pagination-consistency (page sizes 10 vs 5):
PASS - same 100-row sequence at both sizes, totals agree
(100/100), no duplicates or gaps at either size.

Link-header walk, same target (first URL carries _page=1
& _limit=10; the walker then follows the Link headers
verbatim): PASS - 10 pages, 100 unique rows, stable
order, total 100. Link pagination is no longer
fixture-only; it is live-verified.

Bounds re-check on Petstore (2026-11-04, after the scalar-
bounds feature landed): the spec declares zero bound
keywords (no minimum/maximum/pattern/minLength/maxLength/
minItems anywhere), so the bounds code is NOT exercised
by this spec. Verdict identical to before: same 2
violations, same 2 unjudged. Bounds stay unit-tested
only until a bounds-declaring public spec is found;
this run does not claim otherwise.

Auto-style against the same target (offset/limit params
the API does not know): FAIL - server ignored the
requested limit, page 1 returned 100 rows for limit 10.
The first version of this rule then walked 100 pages
before giving up; the walk now stops at the first
oversize page (1 request, 0.5s). A limit-ignoring server
is a FAIL in pagination, pagination-consistency, and
pagination-concurrent alike.

## JSONPlaceholder CORS (read-only OPTIONS probe)

`cors --url https://jsonplaceholder.typicode.com/posts
--method POST --origin https://example.com` PASSES: the demo
API's preflight allows POST from any origin (wildcard). This
is the cors check's first live proof; it judges the demo's
public CORS posture only.

## httpbin redirect chain (read-only GETs)

`redirect --url https://httpbin.org/redirect/2` PASSES: the
chain runs 2 hops (relative Locations, resolved against each
fetched URL) and ends at HTTP 200. This is the redirect
check's first live proof; cycles, downgrades, and hop limits
stay fake-transport-tested, since no public demo offers a
looping chain to probe.

## httpbin HEAD + CORS, JSONPlaceholder /users detail (read-only)

- `head --url https://httpbin.org/get` FAILS, by exactly 1
  byte: HEAD claims Content-Length 224, GET returns 223.
  Cause (verified by fetching both): httpbin's /get echoes
  the request method in its body, so the HEAD rendering
  ("method": "HEAD") is 1 byte longer than any GET rendering
  ("method": "GET"). A demo-API quirk, not a meaningful
  server bug — but a true positive for the check's
  contract: HEAD and GET Content-Lengths disagree, and the
  check located the disagreement exactly.
- `cors --url https://httpbin.org/get --method GET
  --origin https://example.com` PASSES (preflight allows
  GET from the origin).
- `detail-superset --list-url
  https://jsonplaceholder.typicode.com/users?_limit=5
  --detail-url .../users/{id}` PASSES: all 8 list-row fields
  covered by the detail response with matching types,
  including nested-object fields (address, company compared
  by type).
- `filter --url
  https://jsonplaceholder.typicode.com/posts?userId=1
  --field userId --value 1` PASSES: all 10 returned rows
  satisfy the predicate (JSONPlaceholder filters exactly
  on every field, so it cannot demonstrate the FAIL
  direction — that direction is fixture-proven).
