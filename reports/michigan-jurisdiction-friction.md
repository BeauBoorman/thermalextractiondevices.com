<<<<<<< HEAD
# Michigan Jurisdiction Friction

Research and implementation date: **2026-08-09**. This is a first-class
engineering record, not a list of excuses.

| Class | What happened / evidence | Affected files or entities | Workaround / code avoided | Severity | State #4 likely? | Owner |
| --- | --- | --- | --- | --- | --- | --- |
| SOURCE FRICTION | CRA license verification is an interactive Accela search; periodic licensing reports are DOC/DOCX aggregates, not bulk rows. | `data/michigan-cra/licenses.json`, `TDTS-0025` | Preserved three notice-connected licenses separately and marked coverage partial. No scraper or guessed API added. | high | yes | CRA/public-data architecture |
| SOURCE FRICTION | CRA does not expose a public statewide COA repository; lab pages generally use client portals. | `reports/michigan-coa-source-discovery.md`, `TDTS-0027` | Recorded a negative result and kept the shared COA schema unused rather than manufacturing records. | high | likely | CRA / labs |
| SCHEMA FRICTION | Existing collections separate California `recalls` from Massachusetts `safety-advisories`; Michigan uses both voluntary/mandatory recall and consumer-advisory terminology. | `content/recalls.md`, `TRCL-0007..0009` | Used existing `recalls` records while preserving Michigan event type; documented that the collection is not jurisdiction-neutral. | medium | yes | shared jurisdiction model |
| SCHEMA FRICTION | License, legal entity, DBA, brand, premises, and product are distinct in notices but the human surface has no dedicated brand/premises collections. | licenses, organizations, products | Kept fields separate inside records and relations; no new brand collection. | medium | yes | shared entity model |
| INGESTION FRICTION | Shared Massachusetts adapter assumes a stable CSV/JSON catalog and a live state adapter. Michigan's strongest sources are HTML/PDF/DOCX. | `scripts/state_ingest.py`, Michigan data artifacts | Used compact source-specific normalized artifacts; did not retrofit the MA adapter for document scraping. | medium | yes | shared ingest package/Boris boundary |
| ENTITY-RESOLUTION FRICTION | First-party lab pages name historical/current-looking license identifiers, but do not establish current CRA status or parent-company ownership. | `TSTL-0029..0031`, `TORG-0066..0068` | Retained first-party identity and explicit unresolved status; no ownership inference. | medium | yes | entity resolution layer |
| BORIS FRICTION | Boris frontmatter has a closed relation vocabulary and page-level relation cap; rich confidence/provenance remains in data/reports. | all Michigan pages | Used `relates_to` only and kept confidence/limitations in evidence records and prose. No Boris patch. | medium | yes | Boris |
| DOCUMENTATION FRICTION | CRA labels the current guidance link as v5.1 in one page surface while the fetched PDF is revised 2024-09-23 v5.2. | source manifest, `TREQ-0003` | Recorded the PDF title/version and page-level caveat instead of silently normalizing the label. | low | likely | CRA documentation |
| PROJECT-SPECIFIC | Michigan recalls name MCT oil and CRA guidance names MCT as a target analyte; this is more specific than a generic contamination node. | `TCNT-0017`, `TRCL-0008..0009` | Added one contaminant/target-analyte entity because identity is explicit. | low | state-specific | Michigan adapter |
| AGENT TEMPTATION | The absence of a bulk registry strongly invites a bespoke Accela/Metrc scraper. | not added | Deliberately did not create a second crawler architecture; left the source limitation visible. | high | yes | project maintainers |

## Architectural conclusion

Michigan did not justify a new generic ingestion framework. It did justify
keeping the shared evidence model, source manifests, provenance conventions,
stable IDs, privacy boundaries, and raw-vs-normalized COA semantics. The next
shared improvement should be a documented document-source adapter contract,
not another one-off Michigan crawler.
||||||| parent of c5eb0e1 (feat: deep-ingest Michigan CRA as the third cannabis jurisdiction)
=======
# Michigan Jurisdiction Friction Audit

Recorded during the Michigan deep-implementation pass, 2026-08-09.

Each item is classified by friction type and severity. The report answers:
"Will State #4 hit this too?"

---

## SOURCE FRICTION
*Michigan exposes the data poorly.*

### SF-1: PDF-Dominated Data Surface (HIGH)

**What happened**: Michigan's CRA publishes nearly all regulatory data as PDFs:
monthly reports, recall bulletins, the product registry (Data.pdf), and lab
technical guidance. No structured CSV/JSON open-data catalog exists.

**Evidence**: The CRA Data.pdf product registry required pdftotext extraction;
column positions shifted across pages (Metrc tag column varied from position
65 to 92), requiring regex-anchored parsing rather than fixed-width extraction.

**Affected files**: `data/michigan-cra/facilities.csv`,
`data/michigan-cra/product-registry.csv`

**Workaround**: pdftotext → layout text → regex parser → committed CSV.
Extraction is documented as a one-time extract, not a live pipeline.

**Will State #4 hit this?** **Probably.** Many states publish only PDFs.
The shared ingest infrastructure's fetch → parse → normalize pipeline cannot
handle PDFs without a state-specific extraction step.

**Recommendation**: Consider a `PdfExtractor` abstraction if State #4 is also
PDF-dominated. Do not build it for Michigan alone.

---

### SF-2: Accela Civic Access with ASP.NET Viewstate (MEDIUM)

**What happened**: The CRA license verification portal runs on Accela Civic
Access, which uses ASP.NET postbacks with encrypted viewstate. Automated
search attempts returned "Invalid viewstate" errors — a load-balanced
deployment pattern that defeats simple POST scraping.

**Evidence**: Three attempts to POST the empty search form returned different
viewstate tokens, all rejected. This is a known Accela pattern.

**Affected files**: None (not ingested)

**Workaround**: Used the Data.pdf product registry as the license source
instead. This is a product snapshot, not a license registry, and undercounts
non-producing licensees.

**Will State #4 hit this?** **Possibly.** Several states use Accela. A
generic Accela adapter would be valuable but is a significant engineering
investment.

---

### SF-3: Irregular Monthly Report Filenames (LOW)

**What happened**: CRA monthly reports use inconsistent naming (observed
during the state-expansion-roadmap research). The February 2026 report was
located but automated periodic retrieval would need filename discovery.

**Evidence**: `docs/state-expansion-roadmap.md` §3.8 notes: "monthly reports
are PDFs with irregular filename patterns."

**Workaround**: Manual retrieval and text extraction. The monthly report
fixture captures a point-in-time snapshot.

**Will State #4 hit this?** **Possibly.** Some states have predictable
URLs; others don't.

---

### SF-4: Monthly Report License Counts Are Not Machine-Extractable (MEDIUM)

**What happened**: The February 2026 monthly report contains official
aggregate license counts by category (Grower A/B/C, Processor, etc.), but
the pdftotext dump renders them as dot-leader tables with the category and
count split across lines. The adapter's `parse_monthly_report()` regex
(section header + `Category  Count` on one line) does not match this
layout and conservatively returns empty maps.

**Evidence**: `tests/fixtures/michigan/monthly-report.txt` (e.g. lines
378–400: "Active Licenses / Grower A / Grower B ... 20 / 16 / 1,601");
`scripts/ingest/states/michigan.py` `parse_monthly_report()`.

**Workaround**: The dataset page describes the source honestly without
claiming extracted counts; a unit test pins the empty-map behavior rather
than pretending extraction works. The license overview page is built from
the Data.pdf facility extract instead, so no published page depends on
monthly-report counts.

**Why no bespoke parser was added**: The dot-leader layout is a per-page
PDF-rendering artifact, not a data model Michigan exposes. A regex tuned to
this one report would be brittle across months and would duplicate the
friction SF-1 (PDF surface) rather than resolve it.

**Will State #4 hit this?** **Very likely.** Any PDF-report state with
aggregate tables will need either a layout-tolerant table extractor or an
explicit decision to leave aggregate counts unparsed.

---

## SCHEMA FRICTION
*The jurisdiction evidence model cannot cleanly represent something.*

### SK-1: Dual License Numbering (MEDIUM)

**What happened**: Michigan uses separate prefix systems for adult-use (AU-*)
and medical (PC-, GR-, PT-, SC-, TC-). A single entity may hold both an
adult-use and a medical license for the same premises. The current license
model assigns one license_number per record, requiring separate entities
for the same legal entity.

**Evidence**: Iron Laboratories holds SC-000018 (medical) and AU-S-000018
(adult-use). Both are the same lab at the same premises.

**Workaround**: Each license is a separate entity. The organization entity
can link to both. No automatic dual-license detection exists.

**Will State #4 hit this?** **Likely.** Several states have dual medical/au
systems. A `related_license` or `dual_license_of` relation type would help.

---

### SK-2: Medical-Specific License Categories (LOW)

**What happened**: Medical "Provisioning Centers" don't map cleanly to the
shared "Retailer" / "Dispensary" category. The adapter maps them to
"Dispensary" for aggregation, but the Michigan term is lost.

**Evidence**: PT- prefix = "Medical Provisioning Center" → mapped to
"Dispensary" category.

**Workaround**: Category mapping table in the Michigan adapter preserves the
original license_type alongside the normalized category.

**Will State #4 hit this?** **Yes.** Every state has unique terminology.

---

## INGESTION FRICTION
*Shared tooling cannot consume a source it reasonably should.*

### IF-1: Offline-Only Adapter Architecture (HIGH)

**What happened**: Michigan has no live CSV/JSON endpoints, so the
Massachusetts-style `run_dataset` loop (fetch → parse → normalize → diff)
cannot be used. The Michigan adapter is wholly custom — `MichiganSync` does
not extend the shared `run_dataset` pipeline.

**Evidence**: `MichiganSync` has its own `load_data()` and `generate_content()`
methods that bypass the entire `DatasetRun`/`ArtifactStore`/`Fetcher` stack.

**Workaround**: Accept the divergence. Michigan's adapter is ~850 lines,
self-contained, and clearly a different pipeline shape. The shared repository
still runs it through the same CLI and validation gates.

**Will State #4 hit this?** **Possibly, if offline.** The shared pipeline
assumes live HTTP sources. An offline CSV-reading mode would help.

---

### IF-2: No Diffing Against Prior Snapshots (MEDIUM)

**What happened**: Because Michigan data is extracted from static PDFs (not
periodically re-fetched), the `compare_snapshots` diff infrastructure is not
used. There are no "prior snapshots" to compare against.

**Workaround**: None needed for the initial pass. If the Data.pdf is ever
re-published, a second extraction can be diffed manually.

**Will State #4 hit this?** **Only if State #4 is also one-shot offline.**

---

## ENTITY-RESOLUTION FRICTION
*Real-world identity relationships don't fit current assumptions.*

### ER-1: No MSO / Parent-Company Resolution (LOW)

**What happened**: The Michigan adapter does not attempt MSO mapping. Entities
with multiple licenses (e.g., a retailer chain) get one license per premises
but no parent organization unless the known-labs list explicitly links them.

**Workaround**: None. Documented as a future phase.

**Will State #4 hit this?** **Yes.** Entity resolution is the hardest part
of any jurisdiction pass.

---

## BORIS FRICTION
*Boris makes the desired representation/build awkward or impossible.*

### BF-1: ID-Numbered Filenames Clash with Human-Readable Convention (LOW)

**What happened**: The old Michigan stub used `content/jurisdictions/michigan.md`
as a human-readable filename. The adapter originally allocated a fresh
ID-numbered jurisdiction entity; the reconciled wave instead binds generated
records to the existing editorial profile (`jurisdictions/TJUR-0023`, file
`michigan.md`), the Nevada pattern (`JURISDICTION_ENTITY`).

**Evidence**: The stub (TJUR-0023) is retained as the canonical profile and
now carries the deep-ingestion relations; generated satellites reference it
by ID.

**Workaround**: Accept ID-based filenames as the Boris convention.

**Will State #4 hit this?** **No.** This is now the established pattern.

---

## DOCUMENTATION FRICTION
*The correct mechanism exists but was difficult to discover.*

### DF-1: Massachusetts Adapter as Sole Reference (LOW)

**What happened**: The shared ingest infrastructure is well-documented in the
Massachusetts adapter, but there is no guide for building a new state adapter.
Every architectural decision required reading 2,500 lines of Massachusetts code.

**Evidence**: The Michigan adapter was built by studying the entire
Massachusetts adapter, `state_ingest.py`, `core.py`, `ids.py`, `storage.py`,
`markdown.py`, `validation.py`, and `diff.py`.

**Workaround**: This friction audit serves as partial documentation.

**Will State #4 hit this?** **Yes**, but less so now that Michigan exists as
a second reference.

---

## PROJECT-SPECIFIC
*Michigan genuinely requires custom logic that should remain local.*

### PS-1: Michigan Recall Bulletin Parsing (LOW)

**What happened**: Michigan recall bulletins are PDFs → text extraction →
heuristic parsing. The format varies per bulletin. The parsers
(`parse_exclusive_recall`, `parse_flavor_galaxy_recall`) are Michigan-specific.

**Workaround**: Kept as local functions in the Michigan adapter.

**Will State #4 hit this?** **Yes**, but State #4 will have its own recall
format.

---

### PS-2: Michigan License-Number Prefix Logic (LOW)

**What happened**: Michigan's AU-/PC-/GR-/PT-/SC-/TC- prefix system is
unique. The `derive_program()`, `derive_category()`, `is_lab()`,
`is_adult_use()`, `is_medical()` functions are Michigan-specific.

**Workaround**: Kept as local functions in the Michigan adapter.

**Will State #4 hit this?** **Yes.** Every state has unique license formats.

---

## AGENT TEMPTATION
*Problems that strongly tempted creating generic glue/scaffolding.*

### AT-1: Generic PDF Extraction Pipeline (RESISTED)

**Temptation**: Build a `PdfExtractor` class, a `PdfTableParser`, a
`LayoutColumnGuesser`, and a CLI flag for `--pdf-source`.

**Why resisted**: Michigan is the first PDF-dominated state. A generic PDF
pipeline built from one example would be wrong for the second. Building it
now would create an abstraction that State #4's different PDF layout would
break.

**Resolution**: pdftotext → committed CSV. Document as source friction (SF-1).

---

### AT-2: Generic Offline Sync Class (RESISTED)

**Temptation**: Extract an `OfflineSync` base class from `MichiganSync`,
refactor `MassachusettsSync` to extend it, and add `--offline` to the CLI.

**Why resisted**: Michigan is the first wholly offline adapter. The shared
`ArtifactStore`, `NaturalKeyRegistry`, `frontmatter()`, and `_write_page()`
are already reused. A base class would couple two very different pipelines
(Massachusetts: live HTTP → CSV → diff; Michigan: committed CSV → generate).

**Resolution**: `MichiganSync` is a separate class. The shared primitives
(core, ids, markdown, storage, validation) are reused without subclassing.

---

### AT-3: Generic Recall Parser (RESISTED)

**Temptation**: Build a `RecallBulletinParser` with pluggable field extractors.

**Why resisted**: Each jurisdiction's recall format is different. Michigan
uses PDF bulletins with free-form text. Massachusetts uses structured HTML
pages. Building a generic parser from two very different formats would
produce something that fits neither well.

**Resolution**: Michigan-specific parser functions; Massachusetts-specific
`parse_advisory_page()`. Document the difference.

---

## Summary

| Type | Count | Highest Severity |
|------|-------|-----------------|
| SOURCE FRICTION | 3 | HIGH (PDF surface) |
| SCHEMA FRICTION | 2 | MEDIUM (dual licensing) |
| INGESTION FRICTION | 2 | HIGH (offline-only) |
| ENTITY-RESOLUTION FRICTION | 1 | LOW |
| BORIS FRICTION | 1 | LOW |
| DOCUMENTATION FRICTION | 1 | LOW |
| PROJECT-SPECIFIC | 2 | LOW |
| AGENT TEMPTATION (resisted) | 3 | N/A |

**State #4 Impact Assessment**: **Moderate**. State #4 will hit the PDF
friction if it's also PDF-dominated, and will need its own offline adapter
if it lacks live APIs. The schema friction (dual licensing, state-specific
terminology) is universal. The resisted temptations (generic PDF pipeline,
offline base class) were the right calls — State #4 will be different enough
that premature abstraction would have been wrong.
>>>>>>> c5eb0e1 (feat: deep-ingest Michigan CRA as the third cannabis jurisdiction)
