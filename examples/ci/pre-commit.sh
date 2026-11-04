#!/bin/sh
# apiverdict pre-commit-style smoke check.
# Fast on purpose: it proves the tool still catches its own seeded
# bugs (naive must FAIL, fixed must PASS) before a commit goes out.
# It does NOT run the full test suite — run that separately:
#   python -m unittest discover -s tests
# Usage: sh examples/ci/pre-commit.sh   (from the repo root)
set -u
cd "$(dirname "$0")/../.." || exit 1
PY="${PY:-python3}"

fail() { echo "pre-commit: $1" >&2; exit 1; }

$PY -m apiverdict demo --mode naive >/dev/null 2>&1
test $? -eq 1 || fail "demo naive did not FAIL (exit 1) — the tool is not catching its own bugs"
$PY -m apiverdict demo --mode fixed >/dev/null 2>&1
test $? -eq 0 || fail "demo fixed did not PASS (exit 0)"
$PY -m apiverdict library --mode fixed >/dev/null 2>&1
test $? -eq 0 || fail "library fixed did not PASS (exit 0)"
$PY -m apiverdict casestudy --mode fixed >/dev/null 2>&1
test $? -eq 0 || fail "casestudy fixed did not PASS (exit 0)"
$PY -m apiverdict suite --config examples/suite.json --dry-run >/dev/null 2>&1
test $? -eq 0 || fail "example suite config did not validate (dry-run)"
echo "pre-commit: apiverdict smoke checks OK (naive FAIL / fixed PASS)"
