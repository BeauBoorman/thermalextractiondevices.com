# Jurisdiction Temporal Model (P04, v1)

Effective-date semantics for jurisdiction legal statements. This is the
authoring guide for `metadata/jurisdiction-legal-states.jsonl` (the registry),
`metadata/jurisdiction-schema.json` (the shape), and
`scripts/audit_jurisdiction_temporal.py` (the enforced rules, TEM-01..TEM-10).

v1 is deliberately **schema + validator + sample records only**. The mass
migration of all 74 jurisdiction profiles into the registry is a later wave;
nothing in this model changes how `content/jurisdictions/*.md` pages render
today.

## Why

A jurisdiction profile written as a single current-state snapshot cannot say
"retail sales are not operational, and the enacted implementation schedule
changes that on 2027-07-01" without either losing the date or writing future
law as present tense. The P03 sweep (`reports/jurisdiction-next-pass.md`,
§"P04 temporal-model examples") recorded eight concrete cases: Virginia retail
and hemp, federal hemp, the Oklahoma moratorium extension, federal scheduling
scope, South Africa's draft regulations, NJ home grow, and Thailand's
reversal.

## The model

One registry row is **one state of one (jurisdiction, topic)**:

| Field | Meaning |
| --- | --- |
| `jurisdiction_id` | Canonical `jurisdictions/TJUR-XXXX` (must exist in the id map) |
| `topic` | The legal dimension, e.g. `adult-use commercial sales`. Keep the string stable across successive states |
| `status` | `enacted` / `pending` / `repealed` |
| `effective_date` | ISO date the statement takes/took effect, or `null` when indeterminate |
| `verified_as_of` | Date the status was checked against the source |
| `source_url`, `retrieved_at` | Evidence, same rule as `jurisdiction-sources.jsonl` |
| `review_acknowledged` | `true` silences the stale gate (TEM-05) for a reviewed elapsed transition |
| `note` | Conditionality, milestones, scope |
| `supersedes_effective_date` | `effective_date` of the prior state this one replaces |

### Status semantics (the part agents get wrong)

- **`enacted`** means adopted — a law can be enacted *and not yet effective*
  (Virginia's hemp limit: enacted, effective 2026-08-15, verified 2026-08-09).
  Enactment is a legislative state, not a calendar claim.
- **`pending`** subsumes proposed legislation, draft regulation, and
  passed-but-conditional schedules. A pending statement **never overwrites**
  the enacted state of the same topic; they coexist as separate rows (see the
  NJ home-grow pair in the registry).
- **`repealed`** is an event and therefore always dated.

### Choosing a status

1. Is the text adopted law/regulation today (even if its effect date is
   future)? → `enacted` with the future `effective_date`.
2. Is it a bill, draft rule, or conditional schedule that may not happen? →
   `pending`. If no date is pinned by the cited source, `effective_date: null`
   **and** a `note` explaining why (TEM-03 enforces the note).
3. Did it used to be law and no longer is? → `repealed` with the repeal or
   supersession date.
4. Never encode "unknown" as a status — omit the row and let the profile's
   prose carry the uncertainty.

### Impossible states the validator rejects

- `pending` whose `effective_date` had already passed at `verified_as_of`
  (TEM-06): the record claims it saw a pending state that had already taken
  effect.
- Undated `repealed` (TEM-04); unjustified undated `pending` (TEM-03).
- `supersedes_effective_date` not strictly earlier than `effective_date`
  (TEM-08) — Oklahoma-style extensions must move forward.
- Unknown jurisdiction ids (TEM-09), schema violations (TEM-01).

### Stale-state behavior (warnings, not errors)

- **TEM-05**: a future-dated statement whose date has since elapsed without
  re-verification → re-verify, or set `review_acknowledged: true` once a human
  has confirmed the row still says the right thing.
- **TEM-07**: two enacted states of one topic both in force → add the missing
  repeal or `supersedes_effective_date` link.
- **TEM-10**: `verified_as_of` older than 400 days → re-verification due.

## Relationship to existing machinery

- `metadata/jurisdiction-sources.jsonl` stays the **source ledger** (what was
  retrieved, when, with what per-source effective date). The new registry is
  the **statement layer** (what the law's state is). A registry row's
  `source_url` should appear in the ledger for its jurisdiction.
- Jurisdiction **pages** keep their closed Boris frontmatter (`id, title,
  parent, status, tags, relations` — no temporal keys). The temporal state
  lives in the registry; pages link to it by existing id. The mass rewrite
  that would render temporal sections on pages is the later migration wave.
- `bin/validate_graph.sh` runs the audit after the cultivar-claims gate:
  `python3 scripts/audit_jurisdiction_temporal.py` (repo-root defaults).

## Adding a statement (checklist)

1. Cite a primary source (regulator, statute, regulation, official dataset) —
   the archive's evidence rule; BillTrack50-style trackers are secondary.
2. Fix `verified_as_of` to the date you actually checked it.
3. Pick the status by the decision list above; if `pending` and undated,
   write the `note`.
4. If the row replaces an earlier state of the same topic, add
   `supersedes_effective_date` (and keep or add the prior row — the registry
   is append-friendly; history is cheap, contradiction is not).
5. Run `python3 scripts/audit_jurisdiction_temporal.py` — it must exit 0.
6. Run the tests: `python3 -m unittest tests.test_jurisdiction_temporal`.

## Samples shipped in v1

| Jurisdiction | Topic | Status | Why it's here |
| --- | --- | --- | --- |
| NJ | adult-use commercial sales | enacted 2022-04-21 | Straight effective date |
| NJ | home cultivation (adult use) | repealed + pending pair | The never-overwrite invariant |
| VA | adult-use commercial sales | pending 2027-07-01 | Enacted-but-conditional schedule (P03 case 1) |
| VA | adult-use possession | enacted 2021-07-01 | Baseline honest row |
| VA | hemp product THC limit | enacted 2026-08-15, acknowledged | Enacted-not-yet-effective (P03 case 2), stale-gate demo |
| OK | commercial license moratorium | enacted 2028-08-01 ⊃ 2026-08-01 | Supersession/conditional end (P03 case 4) |
| US federal | hemp definition (total THC) | enacted 2026-11-12 | Successive-states coexistence (P03 case 3) |
