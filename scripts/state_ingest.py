#!/usr/bin/env python3
"""Canonical state-backed ingestion CLI for Thermal Extraction Devices.

Usage
-----

    python3 scripts/state_ingest.py massachusetts                 # live sync
    python3 scripts/state_ingest.py massachusetts --fixtures-only # offline, committed fixtures
    python3 scripts/state_ingest.py massachusetts --dataset licenses --skip-publish
    python3 scripts/state_ingest.py massachusetts --report-only
    python3 scripts/state_ingest.py massachusetts --artifacts-dir /mnt/ingest --quiet

Flags
-----
--refresh          Force re-download even when an immutable snapshot matches.
--dataset NAME     Sync only the named dataset (repeatable).
--artifacts-dir P  Where large raw/normalized artifacts live (default var/ingest/).
--skip-content     Do not regenerate Boris content pages.
--skip-publish     Do not run privacy scan / ID / link / Boris validation gates.
--fixtures-only    Serve all payloads from committed fixtures; no network.
--report-only      Render a change report from the existing manifest only.
--quiet            Print only the final summary line.
"""

from __future__ import annotations

import argparse
import datetime as _dt
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
from scripts.ingest.validation import (  # noqa: E402
    assert_clean,
    collect_entity_ids,
    scan_directory,
    validate_relations,
)

GENERATED_COLLECTIONS = [
    "jurisdictions", "licenses", "organizations", "testing-laboratories",
    "contaminants", "datasets", "requirements", "safety-advisories",
    "affected-products",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="state_ingest.py",
        description="Ingest state-backed cannabis data into the Boris archive.",
    )
    parser.add_argument("state", choices=["massachusetts", "michigan"],
                        help="State adapter to run")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-download datasets even when snapshots match")
    parser.add_argument("--dataset", action="append", dest="datasets", default=None,
                        help="Sync only this dataset (repeatable)")
    parser.add_argument("--artifacts-dir", type=Path, default=None,
                        help="Working directory for large artifacts (default var/ingest/<state>-ccc)")
    parser.add_argument("--skip-content", action="store_true",
                        help="Do not regenerate Boris content pages")
    parser.add_argument("--skip-publish", action="store_true",
                        help="Do not run privacy/ID/link/Boris validation gates")
    parser.add_argument("--fixtures-only", action="store_true",
                        help="Serve payloads from committed fixtures; no network")
    parser.add_argument("--allow-fixture-content", action="store_true",
                        help="DEV ONLY: allow fixture/synthetic records to generate "
                             "content. Never use for publishable output.")
    parser.add_argument("--report-only", action="store_true",
                        help="Render a change report from the existing manifest only")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    return parser


def _new_store(args, state: str) -> ArtifactStore:
    durable_slug = {"michigan": "michigan-cra"}.get(state, f"{state}-ccc")
    if args.fixtures_only:
        # Fixture runs are fully isolated and must never touch committed data.
        base = Path(tempfile.mkdtemp(prefix=f"{state}-ingest-fixture-"))
        return ArtifactStore(state=state,
                             working_root=base / "var" / "ingest" / durable_slug,
                             durable_root=base / "data" / durable_slug)
    working = args.artifacts_dir or (ROOT / "var" / "ingest" / durable_slug)
    durable = ROOT / "data" / durable_slug
    return ArtifactStore(state=state, working_root=working, durable_root=durable)


def main() -> int:
    args = build_parser().parse_args()
    state = args.state

    try:
        if state == "massachusetts":
            from scripts.ingest.states import massachusetts as adapter
        else:
            from scripts.ingest.states import michigan as adapter
    except ImportError as error:  # pragma: no cover
        print(f"state_ingest: cannot load {state} adapter: {error}", file=sys.stderr)
        return 2

    # Hard guard: fixture/synthetic records must never reach publishable
    # content or the durable manifest without an explicit development flag.
    if (args.fixtures_only and not args.report_only
            and not args.skip_content and not args.allow_fixture_content):
        print("state_ingest: refusing to run datasets or generate content in "
              "fixture-only mode (fixture/synthetic records are for tests "
              "only). Pass --allow-fixture-content ONLY for isolated "
              "development output, or run live with official sources.",
              file=sys.stderr)
        return 2

    store = _new_store(args, state)
    registry_path = store.durable_root / "id-map.json"
    registry = NaturalKeyRegistry(registry_path, adapter.ID_PREFIXES,
                                  adapter.ID_COLLECTIONS)
    if not args.fixtures_only:
        # All state adapters share the canonical collections with California
        # and editorial content. Seed the allocator from the combined content
        # tree so newly allocated IDs never collide with existing entities.
        registry.seed_from_entity_ids(collect_entity_ids(ROOT / "content"))

    if args.report_only:
        return _report_only(args, store, adapter)

    if state == "michigan":
        return _run_michigan(args, store, registry, adapter)

    fetcher: object
    if args.fixtures_only:
        fixtures = ROOT / "tests" / "fixtures" / state
        fetcher = FixtureFetcher(fixtures)
    else:
        fetcher = Fetcher()

    datasets = args.datasets or list(adapter.DATASETS.keys())
    for requested in args.datasets or []:
        if requested not in adapter.DATASETS:
            print(f"state_ingest: unknown dataset {requested!r}; "
                  f"known: {', '.join(sorted(adapter.DATASETS))}", file=sys.stderr)
            return 2

    # Dev-flag output is routed to an isolated, gitignored demo directory so
    # fixture/synthetic pages can never land in the real content tree.
    if args.fixtures_only and args.allow_fixture_content and not args.skip_content:
        demo = ROOT / "var" / "ingest" / f"{state}-ccc" / "demo-content"
        content_root: Path = demo
        print(f"state_ingest: dev-flag content will be written to {demo} "
              "(isolated; never publishable)", file=sys.stderr)
    else:
        content_root = ROOT / "content"

    sync = adapter.MassachusettsSync(
        fetch=fetcher, store=store, registry=registry,
        content_root=content_root, datasets=datasets,
        refresh=args.refresh, fixtures_only=args.fixtures_only,
        allow_fixture_content=args.allow_fixture_content,
    )

    report = ChangeReport(state=state, run_id=_run_id(), started_at=utc_now())
    for slug in datasets:
        sync.run_dataset(slug, report)

    advisories = sync.discover_advisories() if not args.skip_content else []
    if not args.skip_content:
        sync.generate_content(report, advisories)

    # Publication gates (fail without publishing on any error).
    errors = []
    if not args.skip_publish:
        errors += _publish_gates(store, report, quiet=args.quiet)

    report.completed_at = utc_now()
    report.errors.extend(errors)
    sync.store.write_report(f"sync-{report.run_id}.md", report.to_markdown())
    registry.save()

    if not args.quiet:
        print(report.to_markdown())
    else:
        ok = not report.errors
        print(f"state_ingest: {'OK' if ok else 'FAILED'} {state} run={report.run_id} "
              f"datasets={len(report.datasets)} pages={len(report.pages_generated)} "
              f"warnings={len(report.warnings)} errors={len(report.errors)}")
    return 1 if report.errors else 0


def _run_michigan(args, store, registry, adapter) -> int:
    """Run the Michigan offline ingestion pipeline.

    Michigan is offline-first: the CRA publishes PDFs, not live endpoints,
    so dataset runs read the committed extracts (data/michigan-cra/,
    tests/fixtures/michigan/) instead of fetching. The sync constructs with
    no fetcher, mirroring the Massachusetts flow around it.
    """
    content_root = ROOT / "content"
    if args.fixtures_only and args.allow_fixture_content and not args.skip_content:
        demo = ROOT / "var" / "ingest" / "michigan-cra" / "demo-content"
        content_root = demo
        print(f"state_ingest: dev-flag content will be written to {demo} "
              "(isolated; never publishable)", file=sys.stderr)

    sync = adapter.MichiganSync(
        store=store, registry=registry, content_root=content_root,
        allow_fixture_content=args.allow_fixture_content,
        fixtures_only=args.fixtures_only,
    )

    report = ChangeReport(state="michigan", run_id=_run_id(), started_at=utc_now())
    if not args.skip_content:
        sync.generate_content(report)

    errors: list[str] = []
    if not args.skip_publish:
        errors += _publish_gates(store, report, quiet=args.quiet,
                                 spec=adapter.PRIVACY_SPEC)

    report.completed_at = utc_now()
    report.errors.extend(errors)
    sync.store.write_report(f"sync-{report.run_id}.md", report.to_markdown())
    registry.save()

    if not args.quiet:
        print(report.to_markdown())
    else:
        ok = not report.errors
        print(f"state_ingest: {'OK' if ok else 'FAILED'} michigan run={report.run_id} "
              f"pages={len(report.pages_generated)} "
              f"warnings={len(report.warnings)} errors={len(report.errors)}")
    return 1 if report.errors else 0


def _publish_gates(store, report, *, quiet: bool, spec=None) -> list[str]:
    """Privacy scan, relation targets, ID validation, Markdown links, Boris gate."""
    from scripts.ingest.validation import PrivacyViolationError

    if spec is None:
        from scripts.ingest.states.massachusetts import PRIVACY_SPEC as spec

    errors: list[str] = []
    content = ROOT / "content"

    # The privacy gate validates only the pages this run generated (plus the
    # Massachusetts-created trunk pages), never other states' or workstreams'
    # content. Whole-collection scanning would wrongly fail on unrelated
    # content (e.g. jurisdiction scaffold prose like "2018 Constitutional
    # Court" or legal citations) that this state does not publish.
    state_trunks = ({"safety-advisories.md", "affected-products.md"}
                    if report.state == "massachusetts" else set())
    state_paths = ({p for p in report.pages_generated if p.endswith(".md")}
                   | state_trunks)
    # The reference/ privacy-spec page is generated by this pipeline but
    # deliberately names excluded source fields ("EIN_TIN", "BUSINESS_EMAIL")
    # as policy examples; the field-marker scan must not flag the spec itself.
    state_paths = {p for p in state_paths if not p.startswith("reference/")}
    findings = scan_directory(content, spec, only_paths=state_paths or None)
    if findings:
        errors.append(f"privacy scan: {len(findings)} finding(s); first: {findings[0]}")
        for finding in findings[:5]:
            report.warnings.append(f"privacy: {finding}")
    if not errors:
        try:
            assert_clean(findings)
        except PrivacyViolationError as error:
            errors.append(str(error))

    broken = validate_relations(content)
    if broken:
        errors.append(f"relation targets: {len(broken)} broken; first: {broken[0]}")
        report.warnings.extend(f"relation: {b}" for b in broken[:5])

    if errors:
        return errors

    import os
    import subprocess

    # The Boris graph/build gate needs a compiler. Provision it once from the
    # pinned commit when missing (same path CI uses), so a fresh clone can run
    # the full gate chain without manual setup.
    boris_bin = os.environ.get("BORIS_BIN")
    if not boris_bin and not (ROOT / "bin" / "boris").is_file():
        print("state_ingest: provisioning pinned Boris compiler...")
        proc = subprocess.run(
            ["bash", "scripts/ensure-boris.sh", "--provision"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return ["Boris compiler could not be provisioned; "
                    "run ./scripts/ensure-boris.sh --provision first"]

    # ID + link audits and the Boris graph/build gate.
    steps = [
        (["python3", "scripts/ted_ids.py", "--root", "content", "--map", "metadata/id-map.jsonl", "--all-state-maps"], "ID validation"),
        (["python3", "scripts/audit_markdown_links.py", "content"], "Markdown link audit"),
        (["bash", "bin/validate_graph.sh"], "Boris graph + build gate"),
    ]
    for command, label in steps:
        proc = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
            errors.append(f"{label} failed:\n" + "\n".join(f"  {line}" for line in tail))
        elif not quiet:
            print(f"state_ingest: {label} passed")
    return errors


def _report_only(args, store, ma) -> int:
    from scripts.ingest.core import ChangeReport

    report = ChangeReport(state=ma.STATE, run_id=_run_id(), started_at=utc_now())
    for slug, entries in store.all_snapshot_records().items():
        latest = entries[-1] if entries else {}
        report.datasets[slug] = {
            "slug": slug,
            "status": "archived",
            "row_count": latest.get("row_count"),
            "raw_sha256": latest.get("raw_sha256"),
            "normalized_sha256": latest.get("normalized_sha256"),
            "change": f"snapshots archived: {len(entries)}",
            "message": "",
        }
    report.completed_at = utc_now()
    store.write_report(f"sync-{report.run_id}.md", report.to_markdown())
    print(report.to_markdown())
    return 0


def _run_id() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
