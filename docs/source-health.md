# Source Health Auditing (P16)

External evidence URLs decay. `scripts/audit_source_health.py` checks every URL
recorded in `metadata/jurisdiction-sources.jsonl`, follows redirects, and separates
**confirmed decay** from **transient trouble** so a rate-limited regulator site is
never mistaken for a dead citation.

## Run it

```bash
python3 scripts/audit_source_health.py
```

Stdlib only — no dependencies. The run writes `reports/source-health-report.json`
and prints a summary. Exit codes follow the repo audit convention: `0` healthy or
transient-only, `1` confirmed-dead sources, `2` auditor error.

CI can run it as a non-blocking scheduled audit (see the P16 card in
`docs/next-pass-cards.md`); locally it is **not** part of `bin/validate_graph.sh`
and never blocks an ordinary build.

## Report schema

Each URL entry carries: `url`, `jurisdiction_id`, `source_type`, `status`,
`last_ok`, `first_seen_bad`, `redirected_to`, `domain_changed`, `last_reason`.

| Status | Meaning |
| --- | --- |
| `ok` | Probe succeeded (HEAD, or GET where HEAD is refused) |
| `transient` | 429/403/5xx/timeout — alive-but-annoyed or flaky network; never treated as content failure |
| `failing-new` | First failure inside the confirm window (24 h default); re-run later to confirm |
| `dead` | Same non-transient failure persisting past the confirm window (404/410, DNS gone) |
| `unknown` | Never checked (fresh state, or `--no-network` with empty state) |

`redirected_to` records where a URL finally landed after following redirects;
`domain_changed` is true when that landing domain differs from the original —
review those rows and update the JSONL when the redirect is permanent.

## State and caching

Per-repo convention (same shape as `data/dcc/`), network state lives under
`data/source-health/state.json`:

- A URL recorded **ok** within the cache TTL (7 days default) is not re-checked.
- Failures are always re-checked — decay detection stays live.
- `first_seen_bad` is persisted so "dead" requires the failure to have persisted
  for the confirm window (24 h default), not a single bad probe.

The directory is gitignored (see the `data/` rules in `.gitignore`); delete it to
force a full re-check. Useful flags: `--limit N` (smoke), `--no-network`
(report from state only), `--min-delay`, `--timeout`, `--cache-ttl-days`,
`--confirm-window-hours`.

## Politeness

One request at a time, ≥ 2 s between requests by default, a descriptive
User-Agent identifying the audit, and HEAD-first probes (falling back to a
1-byte ranged GET on 405/501) to keep payload off regulator servers.

## Remediation workflow

1. `dead` → replace the source or record an archived copy; then update
   `metadata/jurisdiction-sources.jsonl` and delete the URL's state entry.
2. `transient` → do nothing on the content side; re-run the auditor later.
3. `failing-new` → wait out the confirm window, re-run, then treat as (1) if
   it confirms.
4. `redirected_to` / `domain_changed` → update the JSONL entry when permanent.

Mocked HTTP tests (redirects, 429/403/404/410, timeout, HEAD-refusal, decay
confirmation across runs) live in `tests/test_audit_source_health.py` and run
with the standard suite: `python3 -m unittest discover -s tests -t .`
