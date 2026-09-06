# Research Corpus Quality Audit

**Agent 10 — Corpus Quality Boss (integration review)**
**Branch:** `agent/corpus-quality-audit`
**Date:** 2026-08-08
**Baseline:** `fa0d525` (latest `github/main` at start of pass; `git fetch github` confirmed `main` → `fa0d525`)

## Scope & method

Integration review over the research-driven work: research corpus integrity, site content
against the corpus, source provenance, graph relationships, ID stability, and Boris
compilation. Audit areas from the agent brief:

1. **Corpus integrity** — every manifest record verified against disk (path, SHA-256, byte
   size), uniqueness, duplicate-group consistency, archive dispositions.
2. **Site content vs corpus** — the specific scientific / device / data / provenance mistake
   classes listed in the brief, checked across `content/`, `data/`, and `metadata/`.
3. **Graph relationships** — Boris graph diagnostics, relation targets, connectedness.
4. **ID stability** — `metadata/id-map.jsonl` vs content frontmatter, `ted_ids.py` validation.
5. **Boris compilation** — `./bin/validate_graph.sh` (IDs + taxonomy + Boris check + build).

Only clearly scoped BLOCKER/HIGH findings were fixed. Larger architectural decisions are
left as recommendations below.

---

## 1. Findings

### BLOCKER (pre-existing, already tracked — not fixed here)

**B-1. Public-release audit fails on `data/dcc/**` registry PII — 172,488 blocking
findings.** `./bin/validate_graph.sh` runs `ted-build.sh`, which invokes
`scripts/audit_public_release.py --config docs/audit-config.json`. That audit reports
172,488 blocking findings at the `high` threshold: PII-001 (emails, 82,946), PII-002
(phones, 83,292), PII-003 (street addresses, 5,848), PII-004 (coordinates, 402), PII-005
(tax/parcel identifiers, 141), PII-007 (prohibited field names, 86), plus LARGE-001/002/003
(giant tracked blobs / duplicate dataset copies) — all in `data/dcc/license-registry/`
(`raw.json`, `normalized.json`, `latest.json`, `previous.json`).

This is **pre-existing and intentional**: `docs/pre-publication-checklist.md` documents it
verbatim ("currently exits 1: 172,562 blocking findings (PII in `data/dcc/**`). Resolve the
`data/` disposition first") and lists a `[BLOCKER]` item: *decide the disposition of
`data/dcc/**`* (move raw/normalized payloads to private external artifact storage, strip PII
at ingest, or obtain consent for republication). The repository is private; the deploy path
(`scripts/cloudflare-build.sh`) runs the same gate with `SKIP_RELEASE_AUDIT=1`. No change was
made here — this requires a maintainer decision, not a scoped content fix. The local gate
passes with the documented bypass (`SKIP_RELEASE_AUDIT=1`, exactly what the deploy path uses);
the audit itself fails on this pre-existing blocker. Full details in
`docs/pre-publication-checklist.md`.

### HIGH (fixed)

**H-1. Stale snapshot-file citations on all 8 law-and-use pages (`TLAW-0002`–`TLAW-0009`).**
Each page's `Source` section cited `data/dcc/licenses-all.csv` (and TLAW-0009 cited
`data/dcc/licenses-active-los-angeles.csv`) — files that **do not exist anywhere in the
repo**. The actual archived snapshot is
`data/dcc/license-registry/2026-08-04/normalized.json` (the layout documented in
`data/dcc/schema-report.md` and used by `datasets/TDTS-0001` and the `testing-laboratories/TSTL-*`
pages). This broke the claim→source linkage even though every published count is accurate.

Verification before fixing: recomputed every category/status/type distribution from
`data/dcc/license-registry/2026-08-04/normalized.json` (20,821 rows) and confirmed all 8
pages' totals and tables match the data exactly — Cultivation 13,244 · Distributor 2,439 ·
Event Organizer 193 · Manufacturing 1,557 · Microbusiness 665 · Retailer 2,647 · Testing
Laboratory 76 · Active Los Angeles 1,264 (type-level tables also match).

Fix: updated the `Snapshot file:` line on all 8 pages to the real archived path, noting the
segment. Also updated the generator `scripts/dcc_sync.py` (`build_record`) so regenerated
pages emit the same corrected line (verified by smoke test: generated output matches the
committed pages byte-for-byte on the snapshot line).

**H-2. `content/cannabinoids/cbda.md` cited Perplexity research exports for material
claims.** Footnotes `[^2]` and `[^4]` cited
`research/compounds/cannabinoids/cbda/source/2026-08-08-perplexity.md` as the source for
physical-property claims (GC-vs-LC behavior, decarboxylation, the "≈120 °C is
decarboxylation onset, not boiling point" correction, biosynthesis/chemotype) and for the
biological-evidence framing; footnote `[^3]` pointed at "the CBD dossier" for occurrence
ranges. This violates the corpus rule *"Never treat a Perplexity research report as primary
evidence"* — the same claim types on the sibling pages (`cbd.md`, `thca.md`, `cbga.md`,
`thcv.md`) are cited to primary literature. This was the only compound page with the
pattern.

Fix: re-pointed the footnotes to the underlying primary sources (the same sources the CBD
page uses for identical claims, each verified to exist and match the claim):

- GC-injector decarboxylation / LC requirement / degradation products → García-Valverde
  et al. 2022, *Front Chem.* 10:1038729, doi:10.3389/fchem.2022.1038729.
- Decarboxylation ≈105–120 °C, tens of minutes → Wang et al. 2016, *Cannabis Cannabinoid
  Res.* 1(1):262–271, doi:10.1089/can.2016.0020, PMID 28861498.
- Boiling-point correction (no intact-acid BP at 1 atm; ≈120 °C = decarboxylation onset) →
  Eyal et al. 2023, *Cannabis Cannabinoid Res.* 8(3):414–425, doi:10.1089/can.2021.0173,
  PMID 35442765.
- CBDAS/THCAS allele balance and Type I/II/III chemotype → de Meijer et al. 2003, *Genetics*
  163(1):335–346, PMID 12586720.
- Occurrence ranges → Stack et al. 2023, *Plant Direct.* 7(6):e503, doi:10.1002/pld3.503,
  PMID 37347078; Jikomes & Zoorob 2018, *Sci Rep.* 8:4519, doi:10.1038/s41598-018-22755-2.
- Biological evidence (mouse anxiolytic/antinociceptive, in vitro COX-2; no controlled human
  trials) → Formato et al. 2020, *Molecules.* 25(11):2638, doi:10.3390/molecules25112638,
  PMID 32517131; Takeda et al. 2008, *Drug Metab Dispos.* 36(9):1917–1921,
  doi:10.1124/dmd.108.020909, PMID 18556441.

The last two were verified via PubMed/PMC before publishing.

### MEDIUM (not fixed — recommendations)

- **M-1. `research/README.md` phrasing.** "Built from 195 Perplexity deep-research Markdown
  exports" — the manifest distinguishes 142 exports + 44 artifacts + 9 archived-redundant
  files, and artifacts are structured reports, not raw exports. Recommend rewording to
  "195 research records (142 Perplexity exports, 44 structured artifacts, 9 archived
  duplicates)".
- **M-2. Corpus packaging is out-of-band.** Only `research/README.md` and 3 of 6 `_index/`
  files are tracked in git; the 195 corpus files plus `inventory.md`, `duplicate-groups.md`,
  and `unresolved.md` are untracked in the main worktree. If the corpus is meant to be
  reproducible from git, its packaging (artifact storage vs git, with checksums) needs a
  decision. The manifest's SHA-256/size columns make external hosting verifiable.
- **M-3. 19 `unverified` corpus records.** Most are Priority-3; Pharmacopeia/Inhalater and
  the US-regulatory-data subject are explicitly unverified with explanatory notes. None are
  cited by published content for material claims — confirmed by scan. Leave as-is; verify
  before any ingestion.
- **M-4. `data/dcc/harvest` and `monthly-sales` marked `unstable`.** Correctly handled by the
  aggregate-only dataset records (`TDTS-0002`/`TDTS-0003`) which refuse to publish
  numbers they cannot source-trace. No change needed — recorded for awareness.

### LOW / EDITORIAL (not fixed)

- **L-1.** Terpene pages' biological sections cite "Research-corpus dossier … not re-verified
  in this wave" — this is an honest limitation label (evidence class disclosure), not a claim
  of primary evidence; consistent with TREF-0003. Fine as-is.
- **L-2.** `inventory.md` lists Goboof / Mig Vapor / Vaporfection as artifact-only subjects
  (no export) — informational gap, no content impact.
- **L-3.** The demo records (`TLAB-0001`, `TPRD-0001`, `TCUL-0001`'s
  demo linkage) are all explicitly labeled demonstration and are never used as verified
  evidence (verified by inspection + schema: `record_kind` enum in
  `metadata/coa-measurement.schema.json`).

### Audit areas with NO findings (clean)

- **Scientific mistakes** — no boiling-point-as-setpoint, no cultivar-as-fixed-chemistry,
  no anecdotal-as-clinical, no acid/neutral merging, no stereoisomer collapse, no
  correlation-as-causation in published content. Terpene pages pressure-reference every
  boiling point (TREF-0001); cannabinoid pages state explicitly that acids/neutrals are
  distinct identities (e.g., `cbda.md` "decarboxylation product is CBD"); claim grammar
  (TREF-0003) forbids causal phrasing; unresolved biological claims remain labeled
  "unresolved" (linalool, ocimene, β-pinene, α-pinene, β-caryophyllene, etc.).
- **Device mistakes** — no collapsed revisions (revision runs labeled per manufacturer
  evidence, e.g., DynaVap M7 2024 vs M7 XL; TinyMight "1.5" marked community-designated);
  no retailer-bundle-as-model; no manufacturer/retailer identity confusion; "isolated air
  path" always flagged as marketing (`includes/manufacturer-claim-note.md`, `TED-0001`,
  `TMFR-0002`); materials claims attributed (316 grade not assumed on M7); every
  discontinued/current status carries dated evidence or is labeled approximate
  (e.g., `TED-0028`/`TED-0029`/`TED-0030` distinguish manufacturer-stated vs delisting
  evidence).
- **Data mistakes** — ND is never rendered as zero (`guides/reading-a-cannabis-coa.md`,
  COA schema `state` enum); unit conversions require an audit trail
  (`conversionAudit` in the schema); batch measurements are batch-attached, never cultivar
  identity (cultivar disclaimer on every cultivar page); state rules are dated snapshots
  with `data_through`/`retrieval_date` and versioned `latest.json`/`previous.json`
  (effective-date handling present); lab measurements are not stripped (schema preserves
  `reported_value` verbatim).
- **Provenance mistakes** — no Perplexity citation as a primary source remains in content
  (H-2 removed the last one); research-artifact URLs are not used as sources for material
  claims (the many `research/...artifact.md` references in device/manufacturer pages are all
  explicitly framed as *internal provenance for approximate/uncertain rows*, with material
  claims cited to manufacturer/primary sources); no claims lacking source linkage
  (substantive pages carry Sources sections; fixtures carry demonstration labels).

---

## 2. Corpus integrity results

| Check | Result |
| --- | --- |
| Manifest records | 195 — all parse as JSON |
| `normalized_path` exists on disk | 195/195 |
| SHA-256 matches manifest | 195/195 |
| Byte size matches manifest | 195/195 |
| Duplicate `normalized_path` in manifest | 0 |
| On-disk corpus files not in manifest | 0 (only `README.md` + `_index/*`, expected) |
| Archived-redundant records | 9, each with a `duplicate_group`; group docs consistent |
| Dispositions | keep 186 · archived-redundant 9 |
| Verification status | primary-sources-verified 176 · unverified 19 |
| Ingestion status | queued 150 · incorporated 20 · needs-review 14 · not-started 11 |
| Priority | P1 32 · P2 147 · P3 16 |

Note: the corpus files are not tracked in git (see M-2); for this audit they were mirrored
byte-for-byte from the main worktree (`diff -rq` clean apart from `.DS_Store`).

---

## 3. Graph, IDs, Boris

| Check | Result |
| --- | --- |
| `python3 scripts/ted_ids.py --root content --map metadata/id-map.jsonl` | PASS — 207 pages, no files changed |
| ID map ↔ content frontmatter | 207/207 aligned; no duplicates; no orphans |
| Device architecture taxonomy audit | 0 errors, 0 warnings |
| Boris graph diagnostics (`bin/boris check --input content --format json`) | PASS — 0 unexpected findings; only baseline `unreferenced_page` diagnostics (tolerated by `bin/validate_graph.sh`) |
| Boris compilation (Cantilever build) | PASS — `dist/cantilever` built; publication checks green; `_headers` copied |
| `python3 scripts/audit_markdown_links.py content` | PASS — all local links resolve |
| `python3 -m unittest discover -s tests` | PASS — 154 tests OK, 4 skipped (network-dependent) |
| `./bin/validate_graph.sh` | PASS with documented `SKIP_RELEASE_AUDIT=1` bypass (identical to `cloudflare-build.sh`); fails without it on pre-existing B-1 |

No new graph relationships were created and no entities were created by this pass (fixes
were citation/source edits only; no frontmatter schema changes, no body-claim changes).

---

## 4. Files added / modified

### Files added

- `reports/research-corpus-quality-audit.md` (this report)

### Files modified

- `content/law-and-use/TLAW-0002.md` — corrected snapshot file reference (H-1)
- `content/law-and-use/TLAW-0003.md` — corrected snapshot file reference (H-1)
- `content/law-and-use/TLAW-0004.md` — corrected snapshot file reference (H-1)
- `content/law-and-use/TLAW-0005.md` — corrected snapshot file reference (H-1)
- `content/law-and-use/TLAW-0006.md` — corrected snapshot file reference (H-1)
- `content/law-and-use/TLAW-0007.md` — corrected snapshot file reference (H-1)
- `content/law-and-use/TLAW-0008.md` — corrected snapshot file reference (H-1)
- `content/law-and-use/TLAW-0009.md` — corrected snapshot file reference (H-1)
- `content/cannabinoids/cbda.md` — footnotes `[^2]`/`[^3]`/`[^4]` re-pointed from Perplexity
  exports to verified primary literature (H-2)
- `scripts/dcc_sync.py` — generator emits the corrected archived snapshot path (H-1;
  verified to match committed output)

### Entities created

None.

### Graph relationships created

None (citation/source edits only).

---

## 5. Primary sources verified

- DCC license registry snapshot `data/dcc/license-registry/2026-08-04/normalized.json`
  (20,821 rows) — recomputed all TLAW-0002..0009 category/status/type/county distributions;
  every published count matched exactly.
- García-Valverde et al. 2022 (*Front Chem.* 10:1038729) — GC-injector degradation of acidic
  cannabinoids; confirmed cited on sibling `cbd.md`.
- Wang et al. 2016 (*Cannabis Cannabinoid Res.* 1(1):262–271, PMID 28861498) —
  decarboxylation of acidic cannabinoids.
- Eyal et al. 2023 (*Cannabis Cannabinoid Res.* 8(3):414–425, PMID 35442765) — boiling-point
  corrections; confirmed used on `cbd.md`/`thca.md`/`cbg.md`/`thcv.md`/`cbdv.md`.
- de Meijer et al. 2003 (*Genetics* 163(1):335–346, PMID 12586720) — chemotype inheritance.
- Stack et al. 2023 (*Plant Direct.* 7(6):e503, PMID 37347078) — high-CBD hemp ranges.
- Jikomes & Zoorob 2018 (*Sci Rep.* 8:4519) — Washington State COA dataset cannabinoid
  content.
- Formato et al. 2020 (*Molecules.* 25(11):2638, doi:10.3390/molecules25112638,
  PMID 32517131) — verified via PubMed/PMC.
- Takeda et al. 2008 (*Drug Metab Dispos.* 36(9):1917–1921, doi:10.1124/dmd.108.020909,
  PMID 18556441) — verified via PubMed.

---

## 6. Uncertain claims left unresolved

- **B-1 data/ disposition** — the `data/dcc/**` PII release blocker (maintainer decision
  required; documented in `docs/pre-publication-checklist.md`).
- The 19 `unverified` corpus records (incl. Pharmacopeia/Inhalater, US regulatory data
  subject) — untouched; must be verified before ingestion.
- The `needs-review` corpus subjects' unresolved biological claims (linalool CNS-depressant,
  ocimene antifungal, β-pinene cytotoxic/antioxidant) — remain labeled unresolved in
  content; not promoted.
- Known corpus-ledger errata (CBD/THCA InChIKeys, aroma-chemistry CIDs, sabinene/camphene
  CAS, α-bisabolol CIDs, Cuboo parentage) — already carried in
  `_index/verification-ledger.md` + `queue_notes`; apply at ingestion.
- Spec-table minutiae (battery mAh, presets, dimensions) remain ingestion-level checks per
  the verification ledger.

---

## 7. Research corpus records consumed

- `research/_index/manifest.jsonl` (195 records) — integrity re-verification, subject
  inventory, verification/ingestion status, duplicate groups.
- `research/_index/verification-ledger.md` — errata, entity confirmations, unresolved claims.
- `research/_index/ingestion-queue.md` — priority rubric, `incorporated`/`needs-review`
  pipeline state.
- `research/_index/inventory.md`, `duplicate-groups.md`, `unresolved.md` — counts, duplicate
  groups, identity resolutions.
- `research/compounds/cannabinoids/cbda/source/2026-08-08-perplexity.md` — source ledger used
  to trace the CBDA page's physical-property/biological claims to primary literature (H-2).
- `research/README.md` — corpus conventions and provenance chain.

---

## 8. Suggested next work

1. **Resolve B-1** (maintainer): decide `data/dcc/**` disposition per
   `docs/pre-publication-checklist.md` (external artifact storage + manifests, or PII strip
   at ingest, or consent), then re-enable the release audit in the local gate and CI.
2. **Apply the verification-ledger identifier errata** at ingestion time (CBD/THCA
   InChIKeys, aroma-chemistry CIDs/CAS, sabinene/camphene CAS, α-bisabolol CIDs) via a
   machine-readable errata file so scripts can apply corrections programmatically
   (verification-ledger's own next-work item).
3. **Verify the 19 `unverified` records** (Priority-3 wave) before any ingestion; at minimum
   Pharmacopeia/Inhalater and the US-regulatory-data subject.
4. **Reconcile the 13 multi-run subjects** before ingestion (7th Floor, DaVinci, Ditanium,
   EpicVape, Eucalyptol, Lotus, Ocimene, Smiss, Vapvana, Wulf Mods, Zeus Arsenal, α-Humulene,
   CBGA) — the manifest already carries "reconcile before ingestion" queue notes.
5. **Packaging decision for the corpus** (M-2): document where the 195 corpus files live and
   how they are hash-verified, so downstream agents and CI can re-run the integrity check.
6. **Create device-recall entities** for the Arizer Solo II / Solo III CPSC recalls and add
   parent-company organization entities (INHALE, TopGreen, Thermodyne, Verdampftnochmal,
   Slang Worldwide) — the two highest-value graph gaps from `reports/graph-connectivity.md`.
