# One-command CI snippet (JUnit output)

The whole point of the JUnit output is that CI systems already know
how to display it — no custom dashboard needed. One command, one
report file, and the exit code is still authoritative (JUnit is a
display format, not the verdict).

```bash
apiverdict openapi \
  --spec openapi.json \
  --base-url https://staging.example.com \
  --junit reports/apiverdict.xml
```

Exit codes, unchanged by `--junit`:

- `0` — every check passed
- `1` — at least one check failed
- `2` — no failures, but at least one check could not verify
  (INCONCLUSIVE maps to `<skipped>` in the XML, never to a pass)

For a single check instead of a whole spec:

```bash
# NB: `check` is the v1 trio wired to fixture-shaped endpoints
# (/api/book, /api/items, /api/order, /api/count) — run it against
# the bundled fixture, not staging. Staging gets spec-check and
# the single-check commands:
python -m apiverdict fixture --mode fixed --port 8765 &
sleep 1
apiverdict check --base-url http://127.0.0.1:8765 --junit reports/apiverdict.xml
apiverdict spec-check --spec openapi.json --base-url https://staging.example.com --junit reports/apiverdict.xml
```

In GitHub Actions, upload the file so failures are visible per-check:

```yaml
- name: Upload JUnit report
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: apiverdict-junit
    path: reports/apiverdict.xml
```

For the full workflow file, see `github-actions.yml` in this
directory — paste it into `.github/workflows/` yourself, because
the agent push route cannot create workflow files (403).
