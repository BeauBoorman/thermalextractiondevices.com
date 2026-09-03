# P12 trunk-copy verification — 2026-09-03 (dme-bti third item)

Sweep of every trunk page on `origin/main` @ `ecf57ae` for the P12 card's
stale-state claims ("current sample is synthetic", "contains only", old hard
counts, California-only descriptions, dishonest empty trunks):

- **No CA-only prose anywhere** (regex sweep for California-only/only
  California/solely California/limited to California across all trunk pages:
  zero hits). The jurisdiction trunk correctly describes the multistate
  reality (76 satellites; CA/MA/MI deep-data implementations named).
- **No stale hard counts.** The only corpus-state count in any trunk is
  `terpenes.md:23` "19 compounds are indexed below" — verified against
  `metadata/id-map.jsonl`: exactly 19 terpene satellites, and 19 table rows.
  All other counts found in trunks are **source-study** counts (e.g. "3 of 19
  cultivars" in a cited Thai survey), which are properties of the cited
  literature, not corpus state — correctly not touched.
- **Honest empty markers.** `specs.md` (0 satellites) carries the explicit
  "No specification records have been published yet" Aside — accurate.
  Sweep for trunks that are empty *without* an honest marker: zero.
  Sweep for stale *empty-markers* on non-empty trunks: zero.
- **index.md "synthetic demonstrations"** phrasing is the honest labeling
  convention for demo COAs (matches `lab-results.md` prose and the
  first-party-provenance warnings), not the P12 "sample is synthetic" lie.

Verdict: the P12 complaints described an earlier corpus state; subsequent
waves (Milestone A #50, multistate expansion #22/#31/#40, cannabinoid series
grooming) landed without introducing new trunk drift. **No content change
required on main today.** The regression risk P12 guards against is now
partially covered by the new `scripts/audit_cultivar_chemotype.py` gate
(structural half); a future count-drift linter could reuse the
id-map-vs-prose cross-check documented here.

— dme-worker-2 (ci-1v7n), verified during dme-bti
