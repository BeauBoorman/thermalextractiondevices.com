# Contributing to Thermal Extraction Devices

Thanks for helping improve the Thermal Extraction Devices archive. This is a
production static site compiled with [Boris](https://github.com/drawmeanelephant/boris);
please read `README.md`, `rules.md`, and `AGENTS.md` before starting.

## Workflow

1. **Discuss first.** For anything beyond a typo fix, open an issue or
   discussion to agree on scope before writing content.
2. **Branch.** Create a branch off `main`; never commit directly to `main`.
3. **Make focused changes.** One logical change per commit. Do not run
   repository-wide formatting or touch unrelated files.
4. **Validate locally.** Run every gate below.
5. **Open a pull request** describing what changed, what evidence the
   content relies on, and which validation commands you ran.
6. **Get review before merging.** `.github/CODEOWNERS` requests review from
   `@drawmeanelephant` for every file. The active `Protect main` ruleset
   requires a pull request, at least one approval, and the passing
   `Validate & Build Site` check. Use the pull-request template to record
   context and exact validation results.

## Issue labels

Use the bug or feature issue form and assign one type, one priority, and one
area label. The forms default to `priority:P2`; maintainers set the final
priority and apply the area selected in the form.

* **Type:** `bug`, `enhancement`, `documentation`, `chore`, or `question`.
* **Priority:** `priority:P0` (immediate exposure/outage), `priority:P1`
  (release blocker/major defect), `priority:P2` (normal planned work), or
  `priority:P3` (non-blocking improvement/decision).
* **Area:** `area:content`, `area:theme`, `area:ingest`, `area:build`, or
  `area:governance`.

`.github/labels.json` records the label names, colors, and descriptions.
Additional default GitHub labels can still be used when helpful.

## Parallel work and coordination

The strategic direction lives in `docs/roadmap.md`. The current implementation
snapshot lives in `docs/status.md`, with independently owned state or workstream
lanes under `docs/status/states/`.

When working in parallel:

* Claim or use the relevant state/workstream lane before editing it.
* Keep the top-level status matrix concise; put detailed evidence in the
  owned lane file.
* Do not use the roadmap as a daily task log.
* Add one append-only `content/changelog/TCHG-XXXX.md` record for a meaningful
  merged change or durable architectural decision.
* Do not allocate competing changelog IDs on parallel branches. Use a
  reservation in `docs/status.md` or ask the integrator to allocate the ID at
  merge time.
* Include the status-lane path, changelog ID or reservation, blockers, next
  action, and exact validation results in the pull request body.

## Required validation (all must pass)

```sh
./bin/validate_graph.sh                          # IDs, taxonomy, record completeness, graph, build, HTML IDs
python3 scripts/audit_public_release.py --config docs/audit-config.json
python3 scripts/audit_markdown_links.py content
python3 scripts/audit_html_ids.py dist/cantilever   # after a build
```

CI runs these on every push and pull request.

## Content rules

* **Frontmatter is a closed schema.** Only `id`, `title`, `parent`, `status`,
  `tags`, `relations` are allowed. Unknown keys break the build.
* **Device records must clear the completeness floor.** Every `content/devices/*.md`
  record carries a tag from all five taxonomy axes, a `Part Number` row, and at least
  one primary-source URL. `scripts/audit_record_completeness.py` enforces this in
  `validate_graph.sh`. Where a manufacturer publishes no part number, say so in the
  row — an absent identifier is recorded, never omitted. See
  `content/reference/TREF-0004.md` § The record-completeness floor.
* **IDs are immutable.** Never rename, renumber, or reuse canonical IDs.
  Run `python3 scripts/ted_ids.py --root content --map metadata/id-map.jsonl`
  to validate; allocate new IDs from the next unused prefix.
* **Evidence over assertion.** Follow the claim grammar in
  `content/reference/evidence-labels-and-claim-grammar.md`. Attribute
  cultivar lineage and morphology to first-party breeder sources; never
  claim that breeder marketing predicts batch chemistry or effects.
* **Provenance warnings.** Records describing commercial products, cultivar
  lineages, or lab results must carry the relevant evidence/provenance
  warning include (see `content/includes/`).
* **Draft vs published.** Unverified research material stays `status: draft`
  (for example `content/guides/manufacturer-research-queue.md`).
* **No PII.** Do not add personal email addresses, phone numbers, full
  addresses, coordinates, or tax identifiers to content. See `PRIVACY.md`.

## History policy

* Never rewrite, rebase, or force-push shared history.
* Never commit build output (`dist/`, `publish/`, `site/`, `bin/boris*`,
  `.tools/`).
* `metadata/id-map.jsonl` is a generated file but is deliberately tracked as
  the migration record; update it via `ted_ids.py --write`, not by hand.

## Licensing

The repository is currently unlicensed (all rights reserved). Do not assume
that contributed content is reusable elsewhere. See `LICENSE.md`.
