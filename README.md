# apiverdict — API behaviour toolkit

**One-line pitch:** A behaviour suite for APIs, not another request client. Point it at an API and it runs invariant checks you didn't have to hand-write — then fails CI if one breaks.

Postman and Bruno send requests and check the assertions you wrote. `apiverdict` checks the behaviours you forgot to assert: does a burst of identical requests create exactly one side effect? Does paging a list return every row exactly once, in stable order? Do all your endpoints fail the same way?

## 60-second demo

No real API, no auth, no data risk — the demo runs against a bundled localhost fixture:

```bash
python -m apiverdict demo
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

## 3-step quick start

```bash
# 1. No install step in v1: Python 3.10+ stdlib only, zero dependencies.
cd api-behaviour-toolkit

# 2. See it fail, then pass, on the fixture:
python -m apiverdict demo

# 3. Point it at your own API (localhost, or staging with --allow-remote):
python -m apiverdict check --base-url http://127.0.0.1:8000
```

## The three v1 checks

### 1. `burst` — simultaneous requests, final-state verification

Fires N identical state-changing requests through a threading barrier gate, repeats for R rounds, and compares the *final state* before and after each burst against `--expect-delta` (default 1). Final state comes from `--verify-cmd` (a shell command printing a single number) or `--verify-url` (a URL returning a count).

```bash
python -m apiverdict burst \
  --url http://127.0.0.1:8000/api/book \
  --payload '{"slot": "slot-{round}"}' \
  --n 20 --rounds 3 \
  --verify-url 'http://127.0.0.1:8000/api/count?slot=slot-{round}' \
  --expect-delta 1
```

`{round}` is substituted per round (1-based) in the payload, verify command, and verify URL, so each round uses a fresh unique field and a correctly deduplicating API can pass every round. Status codes are reported but never trusted: a double-applied operation can still answer 201 every time. A burst with no state check is `INCONCLUSIVE`, never a pass.

### 2. `pagination` — style-agnostic list walker

Walks a list endpoint to the end and checks for duplicate rows, missing rows vs the declared total, and order changes between two walks. Styles are auto-detected: offset/limit, page/size, cursor, and `Link: rel="next"` headers.

```bash
python -m apiverdict pagination --url http://127.0.0.1:8000/api/items --page-size 10
```

### 3. `error-shape` — error consistency, not just non-200s

Sends an invalid payload to each supplied endpoint and clusters responses by (status, sorted top-level keys, error-field name). One cluster is a pass; more than one is a fail, with each cluster listed. An endpoint that accepts invalid input with a 2xx is also a fail.

```bash
python -m apiverdict errors \
  --endpoint POST:http://127.0.0.1:8000/api/book \
  --endpoint POST:http://127.0.0.1:8000/api/order
```

## CI snippet

```yaml
- name: API behaviour checks
  run: python -m apiverdict check --base-url http://127.0.0.1:8000 --json
```

Exit codes: **0** pass, **1** a check failed, **2** could-not-verify. A check that cannot verify never counts as a pass — a skipped check must not turn a build green.

## Safety

- Localhost targets work with no flag. Any other host requires `--allow-remote`, which prints a banner warning.
- **This tool creates resources on purpose and never deletes anything.** Use staging / test data only. There is no cleanup or rollback, by design — deleting is the user's job, stated plainly, not hidden.
- The bundled fixture server binds localhost only.
- No telemetry, no LLM calls, no network calls except to the target API under test.

## What it will NOT do

- **Load / performance testing.** No throughput, latency, or p95 metrics — k6 already does that, and a load test passing can hide 20 duplicate bookings.
- **HTTP/2 single-packet timing.** v1 uses a thread gate over pre-opened connections and reports client send skew honestly; it does not claim single-packet precision (that's Burp Turbo Intruder territory).
- **Multi-endpoint chains** (cart → checkout, redeem → confirm). Single-endpoint bursts only in v1.
- **Automatic cleanup / rollback** of created resources. Never deletes, by design.
- **Fixing your API.** This tool reports behaviour; it does not rewrite code, and it does not delete dead CSS, prune data, or patch handlers.
- **Anything requiring a browser, a paid API, or a key to try it.** Stdlib only, $0.

## Adding a check (plugin seam)

Each check is a separate module under `apiverdict/` returning a `Verdict` (`PASS` / `FAIL` / `INCONCLUSIVE`), registered in `apiverdict/checks.py` and wired into the `check` command. A new check needs no change to the core runner's shape.

## Roadmap (plugins, deliberately out of v1)

- Auth-matrix check (role × endpoint)
- Cache-invalidation check (stale-after-write)
- Rate-limit behaviour check
- Webhook handler check (signature, duplicate, replay)
- Idempotency-key replay check (same key, response convergence — note: the burst check's final-state verification is the broader form of this)

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests start the fixture server on an ephemeral localhost port and assert: naive fails, fixed passes, verify-command failure gives `INCONCLUSIVE` (not a pass), non-localhost without `--allow-remote` is refused, and the demo reports FAIL on naive and PASS on fixed.
