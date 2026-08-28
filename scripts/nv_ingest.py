#!/usr/bin/env python3
"""Nevada CCB ingestion runner: lab-library ZIP + WP-JSON bulletins.

Usage
-----
    python3 scripts/nv_ingest.py --live                # full live run
    python3 scripts/nv_ingest.py --live --coa-batch    # also emit the bounded
                                                      # verified COA batch
    python3 scripts/nv_ingest.py --fixtures-only       # offline tests
    python3 scripts/nv_ingest.py --fixtures-only --allow-fixture-content \
        --demo-content-dir var/ingest/nevada-ccb/demo-content   # isolated dev

What it does
------------
1. Downloads the configured monthly Lab Library ZIP (UTF-16 TSV payloads),
   snapshots it immutably (sha256-addressed), normalizes every row, runs the
   schema/identity/date guards, and records the dataset run.
2. Fetches CCB Public Health and Safety Bulletins from the WordPress REST
   API and parses them (items, retail locations minus street addresses).
3. Generates Boris content pages (datasets, testing laboratories,
   organizations for the verified batch, safety advisories, privacy spec).
4. With --coa-batch: builds the bounded batch of verified COA records
   (metadata/coa-records.jsonl) plus their lab-results pages, in one-to-one
   parity (audit rule COA-08). The batch bound is deliberate: the
   lab-results/TLAB-XXXX form-id space is finite and every verified record
   must have a page, so the committed publication grows in reviewed
   increments while the full per-batch detail stays in the working
   artifacts and the durable dataset record.

Fixture mode never touches committed data (isolated tmp store, same hard
guard as the Massachusetts runner).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ingest.core import ChangeReport, IngestError, utc_now  # noqa: E402
from scripts.ingest.fetch import Fetcher, FixtureFetcher  # noqa: E402
from scripts.ingest.ids import NaturalKeyRegistry  # noqa: E402
from scripts.ingest.storage import ArtifactStore  # noqa: E402
from scripts.ingest.validation import collect_entity_ids  # noqa: E402

from scripts.ingest.states.nevada import (  # noqa: E402
    DATASETS,
    ID_COLLECTIONS,
    ID_PREFIXES,
    JURISDICTION_ENTITY,
    PAGE_POLICY,
    PRIVACY_SPEC,
    NevadaSync,
    report_to_coa_record,
    sample_type_for,
)

COA_REGISTRY = ROOT / "metadata" / "coa-records.jsonl"
LAB_RESULTS_DIR = ROOT / "content" / "lab-results"


def _run_id() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nv_ingest.py", description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="run against live official sources")
    parser.add_argument("--fixtures-only", action="store_true",
                        help="serve payloads from committed fixtures; no network")
    parser.add_argument("--allow-fixture-content", action="store_true",
                        help="DEV ONLY: allow fixture data to generate isolated "
                             "demo content; never for publishable output")
    parser.add_argument("--demo-content-dir", type=Path, default=None,
                        help="content root for --allow-fixture-content runs")
    parser.add_argument("--skip-content", action="store_true",
                        help="do not regenerate Boris content pages")
    parser.add_argument("--coa-batch", action="store_true",
                        help="emit the bounded verified COA batch (jsonl + pages)")
    parser.add_argument("--coa-batch-size", type=int, default=None,
                        help="override the verified batch bound (dev only)")
    parser.add_argument("--artifacts-dir", type=Path, default=None,
                        help="working directory for large artifacts "
                             "(default var/ingest/nevada-ccb)")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _make_store(args, state: str) -> ArtifactStore:
    if args.fixtures_only:
        base = Path(tempfile.mkdtemp(prefix="nevada-ingest-fixture-"))
        return ArtifactStore(state=state,
                             working_root=base / "var" / "ingest" / "nevada-ccb",
                             durable_root=base / "data" / "nevada-ccb")
    working = args.artifacts_dir or (ROOT / "var" / "ingest" / "nevada-ccb")
    durable = ROOT / "data" / "nevada-ccb"
    return ArtifactStore(state=state, working_root=working, durable_root=durable)


def _emit_coa_batch(sync: NevadaSync, report: ChangeReport, *, quiet: bool,
                    batch_size: int = None) -> int:
    """Build the bounded verified COA batch: jsonl records + TLAB pages.

    Returns the number of verified records written. Every record is re-
    validated through scripts.coa_model (CoaRecord/coa_problems) and must
    pass jsonschema against metadata/coa-measurement.schema.json when the
    package is available. Records and pages are written in one-to-one parity
    (COA-08).
    """
    from scripts.coa_model import CoaRecord, Report, Batch, AnalyteMeasurement, \
        Laboratory, SourceProvenance, ResultState, ReportingBasis, RecordKind
    from scripts.crosslinks import coa_record_from_dict

    dataset_slug = "lab_library_2026_06"
    snapshot = sync.store.latest_snapshot(dataset_slug) or {}
    provenance = {
        "source_url": DATASETS[dataset_slug].url,
        "document_hash": snapshot.get("raw_sha256", ""),
        "retrieval_date": (snapshot.get("retrieval_timestamp") or "")[:10] or None,
        "upstream_record_id": "CCB Lab Library June 2026",
        "parser_version": "nv_ingest-0.1",
        "retrieval_note": "Nevada CCB Lab Library monthly extract (official)",
    }

    if batch_size is None:
        batch_size = PAGE_POLICY["verified_batch_max"]

    records = []
    pages = []
    for rpt in sync.lab_reports[:batch_size]:
        report_id = sync.registry.id_for("lab_report", rpt["report_key"])
        producer_entity = sync.registry.entity_id(
            "organization", f"NV:org:{rpt['packaged_by']}") if rpt.get("packaged_by") else None
        lab_entity = sync.registry.entity_id("testing_laboratory", f"NV:lab:{rpt['lab']}")
        record = report_to_coa_record(
            rpt,
            report_id=report_id,
            record_kind="verified",
            provenance=provenance,
            producer_id=producer_entity,
            laboratory_lab_id=lab_entity,
        )
        # Hard validation through the durable model.
        try:
            coa_record_from_dict(record)
        except ValueError as error:
            raise IngestError(f"COA batch record invalid: {error}") from error
        records.append(record)
        pages.append((record, rpt, report_id, producer_entity, lab_entity))

    # jsonschema gate (skipped only when the package is absent, like the MA tests).
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        if not quiet:
            print("nv_ingest: jsonschema not installed; schema validation skipped",
                  file=sys.stderr)
    else:
        import jsonschema

        schema = json.loads((ROOT / "metadata" / "coa-measurement.schema.json")
                            .read_text(encoding="utf-8"))
        for record in records:
            jsonschema.validate(record, schema)

    # ---- write the durable registry (append; verified ids are immutable) ----
    existing = []
    if COA_REGISTRY.is_file():
        existing = [json.loads(line) for line in
                    COA_REGISTRY.read_text(encoding="utf-8").splitlines() if line.strip()]
    existing_ids = {r.get("report", {}).get("report_id") for r in existing}
    new_records = [r for r in records
                   if r["report"]["report_id"] not in existing_ids]
    if new_records:
        with open(COA_REGISTRY, "a", encoding="utf-8") as handle:
            for record in new_records:
                handle.write(json.dumps(record, ensure_ascii=False,
                                        sort_keys=True) + "\n")

    # ---- write lab-results pages in parity (COA-08) ----
    from scripts.ingest.markdown import frontmatter, h1, table, callout, mdlink

    for record, rpt, report_id, producer_entity, lab_entity in pages:
        rel = f"lab-results/{report_id.rsplit('/', 1)[-1]}.md"
        path = ROOT / "content" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        batch = record["batch"]
        report_data = record["report"]
        m_count = len(record["measurements"])
        analytes = sorted({m["compound_name"] for m in record["measurements"]})
        relations = [JURISDICTION_ENTITY]
        if lab_entity:
            relations.append(lab_entity)
        if producer_entity:
            relations.append(producer_entity)
        if "Lead" in analytes:
            relations.append("contaminants/TCNT-0007")
        rows = [
            ["Report ID", report_id],
            ["Testing laboratory", record["report"]["laboratory"]["name"]
                if record["report"]["laboratory"] else "—"],
            ["Producer (packaged by)", rpt.get("packaged_by", "")],
            ["Sample id", rpt.get("sample_id", "")],
            ["Test date", rpt.get("test_date", "")],
            ["Metrc package tag", batch.get("metrc_tag", "")],
            ["Product", escape(rpt.get("product_name", ""))],
            ["Category / matrix", f'{batch.get("matrix_detail", "")}'],
            ["Sample type", batch.get("sample_type", "")],
            ["Package quantity", rpt.get("package_quantity", "")],
            ["Overall passed", rpt.get("overall_passed", "")],
            ["Measurements", str(m_count)],
        ]
        body = [h1(f"{rpt.get('product_name') or rpt.get('sample_id')} — "
                   f"batch {batch.get('batch_id', '')}"),
                "",
                "Verified COA record derived from the Nevada CCB Lab Library "
                "June 2026 official extract.", "",
                "## Report Identity", ""]
        body.append(table(["Field", "Value"], rows))
        body += ["", "## Provenance", ""]
        body.append(table(["Field", "Value"], [
            ["Official source", mdlink(DATASETS["lab_library_2026_06"].url,
                                       "CCB Lab Library June 2026 ZIP")],
            ["Document SHA-256", provenance["document_hash"]],
            ["Retrieval date", str(provenance["retrieval_date"])],
            ["Upstream record id", provenance["upstream_record_id"]],
            ["Parser version", provenance["parser_version"]],
        ]))
        body += ["", "## Measurement Summary", ""]
        body.append(
            f"{m_count} measurements across {len(analytes)} analytes. Full "
            "per-analyte values are carried in the durable COA registry "
            "(`metadata/coa-records.jsonl`); the printed extract preserves "
            "every value verbatim."
        )
        body += ["", callout("warning",
            "Nevada lab extracts carry no method, LOD/LOQ, or basis fields. "
            "Those stay unknown (never imputed); cross-lab comparability is "
            "limited accordingly. A single result implies nothing about "
            "consumer safety without the applicable requirement, unit, "
            "matrix, and action limit."), ""]
        fm = frontmatter(title=f"Verified COA: {rpt.get('product_name') or rpt.get('sample_id')} "
                               f"({report_id.rsplit('/', 1)[-1]})",
                          entity_id=report_id, parent="lab-results",
                          status="published",
                          tags=["lab-results", "coa", "verified", "nevada",
                                batch.get("sample_type", "unknown")],
                          relations=relations)
        path.write_text(fm + "\n\n" + "\n".join(body) + "\n", encoding="utf-8")
    if not quiet:
        print(f"nv_ingest: COA batch: {len(records)} records, "
              f"{len(new_records)} new to the registry")
    return len(records)


def escape(value) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.live and not args.fixtures_only:
        print("nv_ingest: pass --live or --fixtures-only", file=sys.stderr)
        return 2
    if args.fixtures_only and not args.allow_fixture_content and not args.skip_content:
        # Mirror the Massachusetts hard guard: fixture runs never generate
        # publishable content without the explicit dev flag.
        print("nv_ingest: refusing to generate content in fixture-only mode "
              "(fixture/synthetic records are for tests only). Pass "
              "--allow-fixture-content ONLY for isolated development output.",
              file=sys.stderr)
        return 2

    store = _make_store(args, "nevada")
    registry_path = store.durable_root / "id-map.json"
    registry = NaturalKeyRegistry(registry_path, ID_PREFIXES, ID_COLLECTIONS)
    if not args.fixtures_only:
        # Nevada shares the canonical collections with CA/MA; seed the
        # allocator from the combined content tree so new IDs never collide.
        registry.seed_from_entity_ids(collect_entity_ids(ROOT / "content"))

    fetcher = (FixtureFetcher(ROOT / "tests" / "fixtures" / "nevada")
               if args.fixtures_only else Fetcher().with_accepted_types("application/zip"))

    content_root = ROOT / "content"
    if args.fixtures_only and args.allow_fixture_content and not args.skip_content:
        content_root = (args.demo_content_dir
                        or ROOT / "var" / "ingest" / "nevada-ccb" / "demo-content")
        print(f"nv_ingest: dev-flag content isolated to {content_root}",
              file=sys.stderr)

    sync = NevadaSync(
        fetch=fetcher, store=store, registry=registry,
        content_root=content_root,
        datasets=list(DATASETS.keys()),
        fixtures_only=args.fixtures_only,
        allow_fixture_content=args.allow_fixture_content,
        verified_batch_max=args.coa_batch_size,
    )

    report = ChangeReport(state="nevada", run_id=_run_id(), started_at=utc_now())
    for slug in DATASETS:
        sync.run_dataset(slug, report)
    if not args.skip_content:
        sync.generate_content(report)
        if args.coa_batch and not args.fixtures_only:
            _emit_coa_batch(sync, report, quiet=args.quiet,
                            batch_size=args.coa_batch_size)
    report.completed_at = utc_now()
    sync.store.write_report(f"sync-{report.run_id}.md", report.to_markdown())
    registry.save()

    if args.quiet:
        ok = not report.errors
        print(f"nv_ingest: {'OK' if ok else 'FAILED'} run={report.run_id} "
              f"datasets={len(report.datasets)} pages={len(report.pages_generated)} "
              f"warnings={len(report.warnings)} errors={len(report.errors)}")
        return 1 if report.errors else 0
    print(report.to_markdown())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
