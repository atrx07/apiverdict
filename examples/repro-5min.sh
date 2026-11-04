#!/bin/sh
# apiverdict 5-minute reproduction — no install, no keys, no real API.
# Runs: demo (naive FAIL / fixed PASS), casestudy (8 bugs, naive FAIL /
# fixed PASS), library (8 bugs, naive FAIL / fixed PASS), and the
# concurrent-pagination proof (offset FAIL / cursor PASS against the
# same fixture server, same mid-walk write).
# Expected total: well under 5 minutes (typically under 30 seconds).
# Usage: sh examples/repro-5min.sh   (from the repo root)
set -u
cd "$(dirname "$0")/.." || exit 1
PY="${PY:-python3}"
echo "== 1. demo: naive must FAIL, fixed must PASS =="
$PY -m apiverdict demo --mode naive; echo "demo naive exit: $?"
$PY -m apiverdict demo --mode fixed; echo "demo fixed exit: $?"
echo "== 2. library: 8 checks, naive all FAIL, fixed all PASS =="
$PY -m apiverdict library --mode naive; echo "library naive exit: $?"
$PY -m apiverdict library --mode fixed; echo "library fixed exit: $?"
echo "== 3. casestudy: 8 checks, naive all FAIL, fixed all PASS =="
$PY -m apiverdict casestudy --mode naive; echo "casestudy naive exit: $?"
$PY -m apiverdict casestudy --mode fixed; echo "casestudy fixed exit: $?"
echo "== 4. concurrent pagination: same write, two styles =="
start_fixture() {
  $PY -m apiverdict fixture --mode fixed --port 8765 >/dev/null 2>&1 &
  FIXTURE_PID=$!
  # Wait for the fixture to accept connections (max ~10s).
  i=0
  while [ $i -lt 20 ]; do
    if $PY -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/feed?limit=1', timeout=1)" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5; i=$((i + 1))
  done
}
trap 'kill $FIXTURE_PID 2>/dev/null || true' EXIT INT TERM
start_fixture
echo "-- offset + mid-walk write: expect FAIL (duplicates) --"
$PY -m apiverdict pagination-concurrent \
  --url http://127.0.0.1:8765/api/feed --page-size 10 --style offset \
  --write-url http://127.0.0.1:8765/api/feed \
  --write-payload '{"name": "feed-new-{seq}"}' --write-after-page 1
echo "offset concurrent exit: $?"
# Restart so the cursor run starts from the same clean 20-row baseline
# (the offset run's write would otherwise persist into it).
kill $FIXTURE_PID 2>/dev/null || true
sleep 0.5
start_fixture
echo "-- cursor + same mid-walk write: expect PASS --"
$PY -m apiverdict pagination-concurrent \
  --url http://127.0.0.1:8765/api/feed --page-size 10 --style cursor \
  --write-url http://127.0.0.1:8765/api/feed \
  --write-payload '{"name": "feed-new-{seq}"}' --write-after-page 1
echo "cursor concurrent exit: $?"
echo "== done: if the FAIL/PASS pattern above matches, the claims reproduce =="
