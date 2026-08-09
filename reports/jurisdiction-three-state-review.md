<<<<<<< HEAD
# Three-State Jurisdiction Review — California, Massachusetts, Michigan

Date: **2026-08-09**.

## What is universal

- Jurisdiction → license → legal entity/premises is a useful backbone.
- Testing requirements need raw state terminology plus normalized categories.
- Labs, recalls/advisories, products, batches, and COAs need separate identity
  and provenance rather than a single licensee object.
- Source manifests and retrieval dates are useful even when a source is not
  machine-readable.
- Negative evidence gaps must be first-class records.

## State-specific differences preserved

| Concept | California | Massachusetts | Michigan |
| --- | --- | --- | --- |
| License source | DCC JSON API | CCC CSV/JSON open-data catalog | Accela search + DOC/DOCX reports |
| Testing data | DCC lab registry and curated requirements; no public statewide COA corpus | Large testing CSVs; lab names anonymized | CRA technical guide; no public statewide COA corpus |
| Notice vocabulary | recall | public health and safety advisory | voluntary recall, mandatory recall, consumer advisory |
| Traceability | DCC license/recall surfaces | Metrc-derived testing files | Metrc is described, but public data access is limited |
| Numeric limits | intentionally not republished in CA record | intentionally not republished in MA record | captured from current CRA guidance with matrix/product scope |

## Architecture findings

1. The shared source-manifest and evidence model survived Michigan without a
   schema rewrite.
2. The shared ID-seeding and closed-frontmatter conventions worked; Michigan
   records allocated after existing CA/MA records without collisions.
3. The shared COA model survives a jurisdiction with zero public COAs: zero is
   represented as an evidence gap, not a chemistry result.
4. The current human collection names are not fully jurisdiction-neutral:
   `recalls` and `safety-advisories` carry historical state semantics.
5. The largest missing abstraction is a source adapter contract for
   document-oriented registries and PDFs, not another state-specific page
   generator.

## State #4 readiness

State #4 should be easier to add than Michigan if it has a stable CSV/JSON/API
surface: the Massachusetts package provides the reusable path, and Michigan
proves how to document a poor source without creating a second architecture.
If State #4 is another Accela/DOCX/portal-heavy state, the same source and
document friction will recur until shared document ingestion and relationship
confidence are improved.
||||||| parent of c5eb0e1 (feat: deep-ingest Michigan CRA as the third cannabis jurisdiction)
=======
# Three-State Jurisdiction Review: CA · MA · MI

Comparative analysis of the jurisdiction data architecture after implementing
California (DCC), Massachusetts (CCC), and Michigan (CRA).

Conducted 2026-08-09.

---

## What Proved Universal

### U-1: Regulator → License → Entity → Premises Chain

All three states fit the basic chain:
- jurisdiction → licenses → legal entities → premises

California's DCC publishes licenses as downloadable data. Massachusetts's
CCC publishes license trackers as live CSV/JSON. Michigan forces extraction
from PDFs. But the resulting graph shape is the same: a jurisdiction issues
licenses to entities at premises.

### U-2: Testing Requirements as Separate Entities

All three states have regulator-issued testing requirements that are best
modeled as separate requirement entities linked to the jurisdiction, not
as prose embedded in the jurisdiction page.

Massachusetts uses `TREQ-*` requirements; Michigan maps 13 testing requirement
categories; California's requirements are partially modeled.

### U-3: Contaminants as First-Class Entities

The cannabis analyte space — cannabinoids, pesticides, heavy metals,
microbials, mycotoxins, solvents — maps cleanly across all three states.
The specific lists differ (Michigan has 35+ pesticides; Massachusetts
tracks 4 heavy metals), but the entity model works.

### U-4: Recall/Advisory as Safety Advisory Entities

All three states publish consumer-facing product safety notices. Michigan
calls them "recall bulletins"; Massachusetts uses "public health and safety
advisories." The `safety-advisories` collection and `TSAD-*` prefix work for
both.

### U-5: Source Manifest per Jurisdiction

Sharing `metadata/jurisdiction-sources.jsonl` across jurisdictions works.
Each jurisdiction contributes its own source records with unique
jurisdiction field values.

### U-6: ID Prefix + Collection System

The `NaturalKeyRegistry` approach (entity_type → prefix → collection) works
for all three states. Michigan allocated 108 new entities without collision.

---

## What Proved State-Specific

### SS-1: Data Access Pattern

| State | Pattern | Shared Pipeline? |
|-------|---------|-----------------|
| CA (DCC) | Download portal + API | Partial |
| MA (CCC) | Live CSV/JSON open-data | Full (`Fetcher` → `DatasetRun`) |
| MI (CRA) | PDF extraction → committed CSV | **None** (offline-only) |

Michigan is the first state that cannot use the shared `Fetcher`/`DatasetRun`
pipeline at all. The Michigan adapter is ~850 lines of wholly custom content
generation.

### SS-2: Laboratory Designation

| State | Term | Prefix |
|-------|------|--------|
| CA | Licensed Testing Laboratory | — |
| MA | Independent Testing Laboratory | ITL |
| MI | Safety Compliance Facility | SCF |

Michigan's "Safety Compliance Facility" terminology is unique. The
`testing-laboratories` collection absorbs this without schema change.

### SS-3: License Number Format

| State | Format | Example |
|-------|--------|---------|
| CA | CCL + number | CCL18-0001234 |
| MA | Alpha prefix + number | MR281234 |
| MI | AU-/PC-/GR-/PT-/SC-/TC- + number | AU-R-000521 |

Michigan's dual adult-use/medical prefix system is the most complex of the
three. The adapter uses prefix-derived logic for program/category/lab detection.

### SS-4: Testing Data Availability

| State | Public Testing Data? | Format | Coverage |
|-------|---------------------|--------|----------|
| CA | Yes (DCC lab results) | CSV/JSON | Multi-lab, per-batch |
| MA | Yes (CCC Testing Results) | CSV/JSON | Multi-lab, anonymized |
| MI | **No** | — | Iron Labs portal only |

Michigan is the only deep-implementation state without statewide testing data.

### SS-5: COA Availability

| State | State-Level COAs | Lab Portals | Best Portal |
|-------|-----------------|-------------|-------------|
| CA | Partial (DCC) | Some | — |
| MA | Partial (CCC) | Few | — |
| MI | None | 8 known | Iron Labs (public, enumerable) |

Michigan has the most fragmented COA landscape but the best single-lab public
portal (Iron Labs).

---

## Field Names That Proved Misleading

### MN-1: "License Type" Means Different Things

Massachusetts `LICENSE_TYPE` includes cultivation tiers and micro-business
subtypes. Michigan's license_type is derived from the license-number prefix
(AU-R, PC, etc.). California's license type is statutory (Type 1-12).

**Recommendation**: Never assume `license_type` means the same thing across
states. Always preserve the regulator's original category naming.

### MN-2: "Recall" vs "Advisory"

Michigan uses "recall bulletin" even for voluntary withdrawals (Flavor Galaxy).
Massachusetts uses "public health and safety advisory" even when it means
return/destroy. The distinction is editorial, not legal.

**Recommendation**: Use `safety-advisory` as the canonical collection;
preserve the regulator's own terminology in the title and body text.

---

## Cardinality Assumptions That Broke

### CA-1: One License Per Entity

**Assumption**: An entity has one license.
**Reality**: Iron Laboratories holds SC-000018 (medical) and AU-S-000018
(adult-use) — two licenses for the same entity at the same premises.
**Resolution**: Each license is a separate entity; organization entities
can reference multiple licenses.
**State #4 impact**: Likely. Dual-license states are common.

### CA-2: License Registry = Complete License Set

**Assumption**: The official license dataset covers all licensees.
**Reality**: Michigan's Data.pdf is a product registry, not a license
registry. It only lists facilities with Metrc-tagged products. The monthly
report shows higher license counts.
**Resolution**: Documented the discrepancy with a note on the license
overview page.
**State #4 impact**: Depends on the state's data publication practice.

---

## Source Manifest Field Usage

| Field | CA | MA | MI | Notes |
|-------|----|----|-----|-------|
| authority | ✓ | ✓ | ✓ | Always used |
| source_title | ✓ | ✓ | ✓ | Always used |
| source_class | ✓ | ✓ | ✓ | Useful for filtering |
| url | ✓ | ✓ | ✓ | Always used |
| jurisdiction | ✓ | ✓ | ✓ | Required for multi-jurisdiction |
| format | ✓ | ✓ | ✓ | PDF/CSV/HTML/webapp |
| retrieval_date | ✓ | ✓ | ✓ | Date source was last accessed |
| machine_readability | ✓ | ✓ | ✓ | "none"/"partial"/"full" |
| update_cadence | ✓ | ✓ | ✓ | "monthly"/"irregular"/"static" |
| archival_strategy | — | ✓ | ✓ | Added for MA, used for MI |
| provenance_notes | — | ✓ | ✓ | PDF extraction method notes |
| known_limitations | — | ✓ | ✓ | Accela viewstate, PDF extraction |

---

## Did Michigan Require Bespoke Scaffolding?

**Yes, but only where genuinely state-specific.**

The Michigan adapter is self-contained (~850 lines) and does not modify any
shared module. The bespoke parts are:

1. **PDF-to-CSV extraction** (committed to `data/michigan-cra/`)
2. **License-number prefix logic** (`derive_program`, `derive_category`)
3. **Recall bulletin parsers** (`parse_exclusive_recall`, `parse_flavor_galaxy`)
4. **Known labs list** (hand-curated from CRA research)
5. **Testing requirements with action limits** (from CRA Lab Technical Guidance 5.2)

No new generic scaffolding was created.

---

## Can the Same Pipeline Accept State #4?

**Conditionally, yes.** The core pipeline (Fetcher → DatasetRun → Normalize →
Diff → Generate) will work for State #4 if:

1. State #4 publishes live CSV/JSON datasets (like MA)
2. State #4 uses the same ID allocation system (like all three)
3. State #4's license/entity/contaminant/requirement model maps to the
   shared collections

If State #4 is PDF-dominated like Michigan, it will need its own offline
adapter. The friction audit identifies this as a known pattern.

The key question is not "can the pipeline handle State #4?" — it's "will
State #4 be more like Massachusetts (live data) or Michigan (PDF extraction)?"

---

## Recommendations

1. **Consider a shared `PdfSourceAdapter`** when State #4 confirms the PDF
   pattern. Do not build it from Michigan alone.
2. **Add a `related_license` relation type** to handle dual adult-use/medical
   licenses explicitly.
3. **Document the state-adapter build process** in `docs/ingest/` using
   Michigan as the second example.
4. **Keep Michigan's adapter local** — it is the right size for what it does.
5. **Validate the COA normalization model** by ingesting Iron Labs samples
   before adding State #4.
>>>>>>> c5eb0e1 (feat: deep-ingest Michigan CRA as the third cannabis jurisdiction)
