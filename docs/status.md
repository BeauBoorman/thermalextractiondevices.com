# Thermal Extraction Devices — Current Status

Last verified: 2026-08-13
Base commit reviewed: 39a5589

> **Open PII exposure — verified 2026-08-13, needs GitHub Support.** `main` was
> force-pushed on 2026-08-12T18:04:56Z to purge the California DCC bulk payloads
> from git history, and `origin/main` is clean. GitHub still serves them: 43 of
> 44 `refs/pull/N/head` refs point at pre-rewrite history, the repository is
> public, and the licensee registry is still fetchable by an unauthenticated
> client. Specifics are held with the maintainers and deliberately not recorded in
> this public repository; the shape and the remediation path are in
> docs/history-cleanup-plan.md. Private vulnerability reporting is currently
> DISABLED on this repository, so there is no private channel to file them in —
> enabling it is part of the fix. This is not something the release gate can see.
>
> Mechanics: every SHA quoted below from before 2026-08-12 refers to pre-rewrite
> history and no longer resolves upstream. Re-clone or hard-reset to
> `origin/main`; rebase any older branch before opening a pull request.

This is the current coordination snapshot. It is intentionally shorter-lived
than docs/roadmap.md and more operational than the public changelog.

## Status vocabulary

- **Complete** — acceptance criteria met and verified.
- **In progress** — active implementation exists, but required work remains.
- **Blocked** — work cannot proceed until the named dependency is resolved.
- **Parked** — intentionally deferred.
- **Ready** — scoped and unblocked for an agent.

## Current workstream matrix

| Workstream | Status | Current evidence | Main blocker | Next deliverable |
| --- | --- | --- | --- | --- |
| Roadmap and coordination | Complete | docs/roadmap.md, this file, and state lanes exist | None | Keep status current as work lands |
| Multi-state ingestion architecture | In progress | California scripts and shared Massachusetts package coexist; Boris validates canonical page-ID shape/uniqueness while TED owns domain allocation and migration policy | One documented TED allocation boundary across the shared content map and state natural-key maps | Consolidate or explicitly scope the two TED ID maps |
| California DCC program | In progress | DCC scripts and content collections are on main; the bulk `data/dcc` payloads were removed from the tree and purged from git history on 2026-08-12, leaving only `manifest.json`, `schema-report.md` and one sync report tracked | Whether California is re-fetched through the shared adapter or documented as a frozen legacy snapshot | Reconcile or explicitly document the legacy path |
| Massachusetts CCC program | Complete | Live sync verified (15 datasets, ~954k rows); 118 source-backed pages published; IDs reconciled with the shared collections (Massachusetts = jurisdictions/TJUR-0022) | None. Massachusetts contributes zero audit findings and always did | Keep manifests current as CCC publishes |
| Michigan CRA program | In progress | The Michigan evidence wave merged in PR #31 as jurisdictions/TJUR-0023: 26 Michigan records plus two collection index pages updated, covering TLIC-0031..0033, TORG-0063..0068, TSTL-0029..0031, TDTS-0023..0027, TREQ-0003, TRCL-0007..0009, TPRD-0003..0005 and TCNT-0017. Lane document added 2026-08-13 | No adapter, no tests, no re-sync path: CRA sources are Accela search and DOCX aggregates, so Michigan evidence ages silently | Decide whether Michigan gets a document-source adapter or stays a curated wave — see docs/status/states/michigan.md |
| Device encyclopedia | In progress | 43 device records and four manufacturer records; the Cannabis Hardware catalog is complete and fully classified (TCHG-0004) | Coverage of the remaining manufacturers | Apply the Cannabis Hardware completion pattern to the next manufacturer |
| Laboratory and batch/COA graph | Parked | California laboratory collections and demonstration records exist | Canonical batch/report/analyte model | Define the minimum batch/COA schema |
| Profile intelligence | Parked | Terpene and evidence reference pages exist | Measured batch corpus and normalization | Start after batch/COA model |
| Public release readiness | Blocked | `origin/main` itself is clean — a fresh clone's `.git` is 3.2 MiB with no blob above 2 MiB and no `data/dcc` payload reachable — and the 14 medium audit findings were adjudicated on 2026-08-13, leaving 35 active (20 `PII-005` low, 15 `REV-001` informational) with nothing at medium or above | The purged licensee registry is still anonymously downloadable from GitHub through 43 pre-rewrite `refs/pull/N/head` refs, on a public repository | Get GitHub Support to drop the stale pull refs and expire the objects, then decide on licensee notification. Licence terms and the security contact remain open behind that |
| Static build reproducibility | Complete | Re-baselined 2026-08-13: two pinned builds produced 494 identical files, 10,149,589 bytes, aggregate SHA-256 `d19089d9…`; `reports/static-build-reproducibility.md` accounts for all four files that appeared or disappeared since the 496-path baseline | None | Run the monthly/manual reproducibility workflow and investigate any drift |

## Immediate priorities

1. Close the GitHub pull-ref exposure. The licensee registry is downloadable
   today, without credentials, from a public repository. Open a GitHub Support
   request to drop the stale `refs/pull/*` refs and expire the unreachable
   objects, then decide whether the affected licensees need notifying. Nothing
   else on this list matters as much, and no local change can fix it.
2. Settle the licence terms and the security contact — the other two items
   between the repository and a public release decision.
3. Establish one safe multi-state ingestion contract without regressing
   California. Michigan is the forcing case: it is live content with no
   adapter, because its sources are documents rather than bulk datasets.
4. Make ID allocation global across all generated state collections.
5. Decide whether California is migrated into the shared adapter architecture
   or remains a documented legacy path.
6. Define the minimum product → batch → laboratory report → analyte model
   before scaling data ingestion.
7. Answer issue #34 — bounded versus native rendering for Boris relation slots.
   It gates whether the 174-line crosslink pagination layer can retire.

## Parallel-work contract

- The roadmap records direction and acceptance criteria; do not use it as a
  daily task log.
- Each state or workstream owns its file under docs/status/states/.
- Update the top-level status matrix only when a milestone, dependency, or
  status meaningfully changes.
- Changelog records are append-only and describe completed merged work or
  durable architectural decisions.
- Do not invent a parallel TCHG ID. Use an ID reserved here or ask the
  integrator to allocate one at merge time.
- Do not hand-edit metadata/id-map.jsonl. Run the approved ID tooling after
  content changes.
- Do not commit raw snapshots, build output, credentials, or local compiler
  binaries.
- `main` was rewritten and force-pushed on 2026-08-12. Any branch whose
  merge-base predates that rewrite reintroduces the purged `data/dcc` payloads if
  merged. Rebase it onto the current `origin/main`, or recreate the change, before
  opening a pull request.

## Changelog reservations

| ID | Reservation | Lane |
| --- | --- | --- |
| changelog/TCHG-0002 | Roadmap, status, and parallel-work coordination | This documentation PR |
| changelog/TCHG-0003 | Massachusetts CCC end-to-end integration | Massachusetts implementation agent |
| changelog/TCHG-0004 | Cannabis Hardware corpus completion | Merged — this lane |

Unused reservations may be released by the integrator; canonical IDs are
immutable once a record is merged.

## Known documentation state

The August 5 Massachusetts audit and implementation report are historical
snapshots. They contain useful design evidence, but their statements about
California being unreachable and Massachusetts work being uncommitted no
longer describe main. This status file is the current summary; stale
operational wording should be corrected as part of the relevant implementation
lane.

## Verification notes

Re-run 2026-08-13. The graph and audit figures are measured on the branch that
carries this file, not on `39a5589` itself; upstream CI (`CI & Graph Validation`,
`Deploy to Cloudflare Pages`) is separately green on `39a5589`.

`./bin/validate_graph.sh` completed cleanly. The source-only ID check validated
445 pages without changing files. The device taxonomy, COA, cannabinoid
thermal-property, and record-completeness audits each report 0 errors and 0
warnings. The cultivar identity registry validated 15 claims against 445
entities. The crosslink validator reports 445 entities, 1,614 edges and 1 COA
record with no problems; publication derived navigation for 345 entity pages and
19 index pages, and the HTML-ID audit found 0 duplicate IDs. The Boris graph
diagnostics pass outright. `python3 -m unittest discover -s tests -t .` ran 368
tests with 6 skips and no failures — 359 of those exist on `39a5589`, and this
branch adds nine in `tests/test_audit_suppressions.py`.

Static build reproducibility was re-baselined on 2026-08-13: two pinned builds
produced 494 identical paths, 10,149,589 bytes, and aggregate SHA-256
`d19089d96c9fde1aa72bef97224bc8227830bdfbe362ecd36669186342a7f0c5`. The output
shrank by two files against the 496-path 2026-08-09 baseline while the corpus
grew by one entity; `reports/static-build-reproducibility.md` names all four
files that appeared or disappeared and traces the three removals to PR #44's
`CXL-03` rule against direct cultivar → compound edges. No entity page was lost.
That report compares file *lists*, not file contents, and the Boris pin also
moved between the two baselines, so the 16,350-byte growth is not attributed.

## Archive integrity update

Verified 2026-08-09, still current. The Arizer Solo II and Solo III CPSC recalls
are modeled as `recalls/TRCL-0010` and `recalls/TRCL-0011`, with source-backed
relations to the manufacturer and Solo III device record. The crosslink layer
rejects a fully isolated satellite collection (`CXL-13`), and the DCC generator
emits conservative organization → laboratory/recall reverse edges when unique
license-number matches exist.

Device-recall coverage was extended on 2026-09-05 with `recalls/TRCL-0012`: the
2017 CPSC Firewood 4 battery-fire recall (17-761, ≈400 units) now has its own
entity linked to `devices/TED-0086` and `manufacturers/TMFR-0028`, closing the
recall-coverage gap flagged by the device-catalog adversarial review. A sweep
of the other Milestone-A manufacturers against the CPSC / SaferProducts recall
database found no additional device recalls to model: PAX, Puffco, and Dr.
Dabber each show no CPSC recall on record (the review's "PAX Era CPSC 2025"
candidate does not exist), and no other catalog manufacturer appears in the
vaporizer recall set. A June 2026 Colorado MED recall of PAX-branded vape oil
is a state cannabis-product recall in a jurisdiction the archive does not yet
model (California, Massachusetts, and Michigan only) — a candidate for the
jurisdiction-expansion lane, not the device-recall graph.

## Publication hardening update

The publication-hardening work merged, and the git-history rewrite it depended on
was executed and force-pushed on 2026-08-12T18:04:56Z. Verified 2026-08-13
against a fresh clone of `origin/main`: `.git` is 3.2 MiB, no blob exceeds
2 MiB, and `git log origin/main -- data/dcc/license-registry/latest.json`
returns no commits.

That is a statement about `origin/main` only. **The payload is still served by
GitHub** through 43 pre-rewrite `refs/pull/N/head` refs, on a public repository,
to an unauthenticated fetch. The release gate cannot see this: `LARGE-003` and
`LARGE-004` scan `git rev-list --all` in the local clone, and a CI checkout has
no `refs/pull/*`. A green gate is therefore not evidence the exposure is closed.
See docs/history-cleanup-plan.md for the remediation path. The reproduction is not
recorded in this repository; ask the maintainers.

A working clone made before the rewrite also reports `LARGE-00x` findings,
because its own stale branches and tags keep the purged payloads reachable
locally. Delete the stale refs and `git gc --prune=now` before treating one of
those as real.

The rewrite did not, by itself, unblock the release audit — that is worth stating
plainly because two earlier drafts of this file implied it did. The audit already
passed before the rewrite: the tracked payloads had been untracked in `6d740f4`,
and PR #41 graded the deleted-but-reachable history findings `medium`, below the
`high` fail threshold. CI is green on the pre-rewrite head `6e693a8` with every
blob still reachable. The rewrite removed the data; it did not change a gate
result.

On a clean clone of `39a5589` the audit reports 79 findings, 49 active — 15
`REV-001` informational, 20 `PII-005` low, 10 `PII-007` medium and 4 `PII-005`
medium. After the 2026-08-13 adjudication it reports 75 findings, 35 active — 20
`PII-005` low and 15 `REV-001` informational — with nothing at medium or above.
The mediums were suppressed with recorded rationale, not removed.

All 14 were declarations rather than data. `PII-007` is a bare substring scan
with no value allowlist, so it fired wherever the prohibited-field denylist is
defined or generated: `EXCLUDED_FIELD_NAMES` in `scripts/ingest/validation.py`,
the generated `privacy-spec.md` and `source-catalog.json`, one dated sync report
recording which fields the privacy gate blocked, and a report describing the
California fields the pipeline drops. Five exact-path suppressions cover those;
`docs/audit-config.json` was already suppressed for the same reason. The
placeholder EIN is a value-scoped entry in `allowlist.tax_ids` instead, which is
narrower than silencing a whole file.

A directory-prefix suppression form was written for the dated `sync-reports/`
directory and then removed the same day. Sync reports embed a 100-character
window of real page content around each blocked field, there is no name detector
anywhere in the audit, and the business-owner-name field has no other tripwire — so
pre-authorising every future file in that directory would have removed the only
control that could catch an owner name. Suppression is exact-match only, and
`tests/test_audit_suppressions.py` now also asserts that every shipped entry
names a real file and that no bare-code entry silences a rule able to block the
gate.

Behind the pull-ref exposure, the remaining release blockers are the licensing
and security-contact decisions, plus the 20 low `PII-005` items, which are long
numeric runs flagged for human review by design.

Validation must be rerun on the completed branch. Do not use an audit bypass as
a release result.
