#!/usr/bin/env python3
"""Compile the archive's machine records into a researcher SQLite database.

Produces ``dist/ted-archive.sqlite`` (and, when ``pyarrow`` is importable, a
columnar ``dist/ted-archive-parquet/`` dataset) from the same structured
sources the site already publishes — never from rendered HTML:

* ``metadata/id-map.jsonl``            → ``entities`` (normalized identity map)
* ``metadata/cultivar-claims.jsonl``   → ``cultivar_claims`` + provenance columns
* ``metadata/coa-records.jsonl``       → ``coa_batches`` / ``coa_measurements`` / ``coa_reports``
* frontmatter ``relations:`` in ``content/**`` → ``crosslink_edges`` (direct, author-declared)
* ``publish/ir/graph.json`` (when present, from ``scripts/ted-publish.sh``) → ``ir_nodes`` / ``ir_edges``

Design rules (mirroring ``scripts/crosslinks.py``):

* Read-only over the repository: the only writes are under ``--output-dir``.
* Deterministic: rows are inserted in sorted order; no timestamps, host
  names, or environment values are recorded, so two runs over the same inputs
  produce byte-identical artifacts. The SQLite file is built into a
  temporary path and atomically moved into place.
* Censoring discipline is preserved verbatim: ND / below-LOD / below-LOQ
  measurements keep their ``state`` and their printed ``reported_value``;
  ``value`` stays NULL. Nothing is imputed as zero (see
  ``scripts/cultivar_profiles.py`` for the module that owns that rule).
* No scientific thresholds or interpretations are added. This is a
  compilation, not an analysis engine.

Usage:

    python3 scripts/export_sqlite.py                     # dist/ted-archive.sqlite
    python3 scripts/export_sqlite.py --output-dir pub    # pub/ted-archive.sqlite
    python3 scripts/export_sqlite.py --with-parquet      # + dist/ted-archive-parquet/

Exit codes: 0 = success, 1 = input validation failure, 2 = I/O error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_VERSION = "1"

SCHEMA_SQL = """
PRAGMA user_version = 1;

CREATE TABLE archive_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    form_id TEXT,
    legacy_id TEXT,
    parent TEXT,
    role TEXT NOT NULL,
    source_path TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE INDEX idx_entities_collection ON entities(collection);
CREATE INDEX idx_entities_parent ON entities(parent);

CREATE TABLE cultivar_claims (
    claim_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    object TEXT NOT NULL,
    object_is_entity INTEGER NOT NULL,
    status TEXT NOT NULL,
    wording TEXT,
    source_name TEXT,
    source_type TEXT,
    source_url TEXT,
    source_retrieved TEXT,
    notes TEXT
);
CREATE INDEX idx_claims_subject ON cultivar_claims(subject);
CREATE INDEX idx_claims_object ON cultivar_claims(object);
CREATE INDEX idx_claims_kind ON cultivar_claims(kind);

CREATE TABLE coa_batches (
    batch_key INTEGER PRIMARY KEY,
    coa_line INTEGER NOT NULL,
    record_kind TEXT,
    jurisdiction TEXT,
    batch_id TEXT,
    lot_number TEXT,
    product_id TEXT,
    producer_id TEXT,
    sample_type TEXT,
    matrix_detail TEXT,
    basis TEXT,
    decarb_convention TEXT,
    harvest_date TEXT,
    package_date TEXT,
    production_date TEXT,
    metrc_tag TEXT,
    cultivar_labels TEXT,
    cultivar_claims TEXT
);
CREATE INDEX idx_batches_product ON coa_batches(product_id);

CREATE TABLE coa_measurements (
    measurement_key INTEGER PRIMARY KEY,
    batch_key INTEGER NOT NULL REFERENCES coa_batches(batch_key),
    compound_name TEXT,
    compound_cas TEXT,
    compound_id TEXT,
    state TEXT,
    value REAL,
    reported_value TEXT,
    reported_unit TEXT,
    lod REAL,
    loq REAL,
    unit TEXT,
    method TEXT,
    test_date TEXT,
    quantitation_note TEXT,
    calculation_formula TEXT,
    conversion TEXT
);
CREATE INDEX idx_measurements_batch ON coa_measurements(batch_key);
CREATE INDEX idx_measurements_compound ON coa_measurements(compound_name);

CREATE TABLE coa_reports (
    batch_key INTEGER PRIMARY KEY REFERENCES coa_batches(batch_key),
    coa_line INTEGER NOT NULL,
    schema_version TEXT,
    jurisdiction TEXT,
    laboratory_id TEXT,
    laboratory_name TEXT,
    laboratory_license TEXT,
    laboratory_jurisdiction TEXT,
    method_summary TEXT
);

CREATE TABLE crosslink_edges (
    edge_key INTEGER PRIMARY KEY,
    source_entity TEXT NOT NULL,
    relation TEXT NOT NULL,
    target TEXT NOT NULL,
    source_path TEXT NOT NULL
);
CREATE INDEX idx_edges_source ON crosslink_edges(source_entity);
CREATE INDEX idx_edges_target ON crosslink_edges(target);

CREATE TABLE ir_nodes (
    id TEXT PRIMARY KEY,
    title TEXT,
    role TEXT,
    parent TEXT,
    source_path TEXT
);

CREATE TABLE ir_edges (
    edge_key INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT,
    target TEXT NOT NULL
);
"""

# Deterministic table order for the Parquet mirror.
PARQUET_TABLES = (
    "entities",
    "cultivar_claims",
    "coa_batches",
    "coa_measurements",
    "coa_reports",
    "crosslink_edges",
    "ir_nodes",
    "ir_edges",
)

RELATION_PATTERN = re.compile(
    r"([a-z_]+)\s*=\s*([^\s,\]]+)", re.IGNORECASE
)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON: {error}"
                ) from error
    return records


def load_id_map(path: Path) -> List[Dict[str, Any]]:
    records = load_jsonl(path)
    required = {"id", "collection", "role", "source", "title"}
    for index, record in enumerate(records, start=1):
        missing = required - record.keys()
        if missing:
            raise ValueError(
                f"{path}: record {index} missing keys: {sorted(missing)}"
            )
    records.sort(key=lambda r: str(r["id"]))
    return records


def load_claims(path: Path) -> List[Dict[str, Any]]:
    records = load_jsonl(path)
    required = {"claim_id", "kind", "subject", "object", "status"}
    for index, record in enumerate(records, start=1):
        missing = required - record.keys()
        if missing:
            raise ValueError(
                f"{path}: record {index} missing keys: {sorted(missing)}"
            )
    records.sort(key=lambda r: str(r["claim_id"]))
    return records


def load_coa_records(path: Path) -> List[Dict[str, Any]]:
    records = load_jsonl(path)
    for index, record in enumerate(records, start=1):
        if not {"batch", "measurements", "report"} <= record.keys():
            raise ValueError(
                f"{path}: record {index} missing batch/measurements/report"
            )
    # Stable order: keep file order (already the registry's canonical order).
    return records


def iter_frontmatter_relations(
    content_root: Path,
) -> Iterable[Tuple[str, str, str, str]]:
    """Yield (entity_id, relation, target, source_path) from frontmatter.

    Mirrors the Boris closed vocabulary consumed by ``scripts/crosslinks.py``:
    ``relates_to`` / ``implements`` / ``depends_on`` / ``supersedes``.
    Unknown relation verbs are skipped (Boris fails them at build time; the
    export never widens the vocabulary on its own).
    """
    allowed = {"relates_to", "implements", "depends_on", "supersedes"}
    for path in sorted(content_root.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^---\n(.*?)\n---", text, flags=re.S)
        if not match:
            continue
        frontmatter = match.group(1)
        id_match = re.search(r"^id:\s*(.+)$", frontmatter, flags=re.M)
        if not id_match:
            continue
        entity_id = id_match.group(1).strip().strip('"')
        rel_match = re.search(
            r"^relations:\s*\[(.*?)\]", frontmatter, flags=re.M | re.S
        )
        if not rel_match:
            continue
        for item in rel_match.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            pair = RELATION_PATTERN.search(item)
            if pair:
                relation, target = pair.group(1).lower(), pair.group(2)
            else:
                relation, target = "relates_to", item
            if relation not in allowed:
                continue
            yield entity_id, relation, target, str(path)


def json_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def insert_entities(conn: sqlite3.Connection, records: List[Dict[str, Any]]) -> int:
    conn.executemany(
        "INSERT INTO entities "
        "(id, collection, form_id, legacy_id, parent, role, source_path, title) "
        "VALUES (:id, :collection, :form_id, :legacy_id, :parent, :role, "
        ":source, :title)",
        records,
    )
    return len(records)


def insert_claims(conn: sqlite3.Connection, records: List[Dict[str, Any]]) -> int:
    rows = []
    for record in records:
        source = record.get("source") or {}
        rows.append(
            {
                "claim_id": record["claim_id"],
                "kind": record["kind"],
                "subject": record["subject"],
                "object": record["object"],
                "object_is_entity": 1 if record.get("object_is_entity") else 0,
                "status": record["status"],
                "wording": record.get("wording"),
                "source_name": source.get("name"),
                "source_type": source.get("type"),
                "source_url": source.get("url"),
                "source_retrieved": source.get("retrieved"),
                "notes": record.get("notes"),
            }
        )
    conn.executemany(
        "INSERT INTO cultivar_claims VALUES ("
        ":claim_id, :kind, :subject, :object, :object_is_entity, :status, "
        ":wording, :source_name, :source_type, :source_url, "
        ":source_retrieved, :notes)",
        rows,
    )
    return len(rows)


def insert_coa(conn: sqlite3.Connection, records: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    batch_rows: List[Dict[str, Any]] = []
    measurement_rows: List[Dict[str, Any]] = []
    report_rows: List[Dict[str, Any]] = []
    batch_key = 0
    measurement_key = 0
    for line_number, record in enumerate(records, start=1):
        batch = record["batch"]
        report = record["report"]
        batch_key += 1
        batch_rows.append(
            {
                "batch_key": batch_key,
                "coa_line": line_number,
                "record_kind": batch.get("record_kind"),
                "jurisdiction": batch.get("jurisdiction"),
                "batch_id": batch.get("batch_id"),
                "lot_number": batch.get("lot_number"),
                "product_id": batch.get("product_id"),
                "producer_id": batch.get("producer_id"),
                "sample_type": batch.get("sample_type"),
                "matrix_detail": batch.get("matrix_detail"),
                "basis": batch.get("basis"),
                "decarb_convention": batch.get("decarb_convention"),
                "harvest_date": batch.get("harvest_date"),
                "package_date": batch.get("package_date"),
                "production_date": batch.get("production_date"),
                "metrc_tag": batch.get("metrc_tag") or None,
                "cultivar_labels": json_or_none(batch.get("cultivar_labels")),
                "cultivar_claims": json_or_none(batch.get("cultivar_claims")),
            }
        )
        laboratory = report.get("laboratory") or {}
        method = report.get("method") or {}
        report_rows.append(
            {
                "batch_key": batch_key,
                "coa_line": line_number,
                "schema_version": record.get("schema_version"),
                "jurisdiction": report.get("jurisdiction"),
                "laboratory_id": laboratory.get("lab_id"),
                "laboratory_name": laboratory.get("name"),
                "laboratory_license": laboratory.get("license_number"),
                "laboratory_jurisdiction": laboratory.get("jurisdiction"),
                "method_summary": json_or_none(method) if method else None,
            }
        )
        for measurement in record.get("measurements", []):
            measurement_key += 1
            measurement_rows.append(
                {
                    "measurement_key": measurement_key,
                    "batch_key": batch_key,
                    "compound_name": measurement.get("compound_name"),
                    "compound_cas": measurement.get("compound_cas"),
                    "compound_id": measurement.get("compound_id"),
                    "state": measurement.get("state"),
                    "value": measurement.get("value"),
                    "reported_value": json_or_none(measurement.get("reported_value")),
                    "reported_unit": measurement.get("reported_unit"),
                    "lod": measurement.get("lod"),
                    "loq": measurement.get("loq"),
                    "unit": measurement.get("unit"),
                    "method": json_or_none(measurement.get("method")),
                    "test_date": measurement.get("test_date"),
                    "quantitation_note": measurement.get("quantitation_note"),
                    "calculation_formula": measurement.get("calculation_formula"),
                    "conversion": json_or_none(measurement.get("conversion")),
                }
            )
    conn.executemany(
        "INSERT INTO coa_batches VALUES ("
        ":batch_key, :coa_line, :record_kind, :jurisdiction, :batch_id, "
        ":lot_number, :product_id, :producer_id, :sample_type, :matrix_detail, "
        ":basis, :decarb_convention, :harvest_date, :package_date, "
        ":production_date, :metrc_tag, :cultivar_labels, :cultivar_claims)",
        batch_rows,
    )
    conn.executemany(
        "INSERT INTO coa_reports VALUES ("
        ":batch_key, :coa_line, :schema_version, :jurisdiction, "
        ":laboratory_id, :laboratory_name, :laboratory_license, "
        ":laboratory_jurisdiction, :method_summary)",
        report_rows,
    )
    conn.executemany(
        "INSERT INTO coa_measurements VALUES ("
        ":measurement_key, :batch_key, :compound_name, :compound_cas, "
        ":compound_id, :state, :value, :reported_value, :reported_unit, "
        ":lod, :loq, :unit, :method, :test_date, :quantitation_note, "
        ":calculation_formula, :conversion)",
        measurement_rows,
    )
    return len(batch_rows), len(report_rows), len(measurement_rows)


def insert_crosslinks(
    conn: sqlite3.Connection, edges: Iterable[Tuple[str, str, str, str]]
) -> int:
    count = 0
    rows = []
    for entity_id, relation, target, source_path in edges:
        count += 1
        rows.append(
            {
                "edge_key": count,
                "source_entity": entity_id,
                "relation": relation,
                "target": target,
                "source_path": source_path,
            }
        )
    conn.executemany("INSERT INTO crosslink_edges VALUES ("
                     ":edge_key, :source_entity, :relation, :target, "
                     ":source_path)", rows)
    return count


def insert_ir(conn: sqlite3.Connection, graph: Dict[str, Any]) -> Tuple[int, int]:
    nodes = sorted(graph.get("nodes", []), key=lambda n: str(n.get("id", "")))
    node_rows = []
    for node in nodes:
        node_rows.append(
            {
                "id": node.get("id"),
                "title": node.get("title"),
                "role": node.get("role"),
                "parent": node.get("parent"),
                "source_path": node.get("sourcePath") or node.get("source_path"),
            }
        )
    conn.executemany(
        "INSERT OR REPLACE INTO ir_nodes VALUES "
        "(:id, :title, :role, :parent, :source_path)",
        node_rows,
    )

    def edge_endpoint(endpoint: Any) -> Optional[str]:
        # Boris IR edges carry ``{"type": ..., "value": ...}`` endpoints;
        # tolerate a bare string for forward compatibility.
        if isinstance(endpoint, dict):
            return endpoint.get("value")
        if isinstance(endpoint, str):
            return endpoint
        return None

    edge_rows = []
    edge_key = 0
    for edge in sorted(
        graph.get("edges", []),
        key=lambda e: (
            str(edge_endpoint(e.get("from")) or ""),
            str(edge_endpoint(e.get("to")) or ""),
        ),
    ):
        source = edge_endpoint(edge.get("from"))
        target = edge_endpoint(edge.get("to"))
        if source is None or target is None:
            continue
        edge_key += 1
        edge_rows.append(
            {
                "edge_key": edge_key,
                "source": source,
                "kind": edge.get("kind") or edge.get("relation"),
                "target": target,
            }
        )
    conn.executemany(
        "INSERT INTO ir_edges VALUES (:edge_key, :source, :kind, :target)",
        edge_rows,
    )
    return len(node_rows), len(edge_rows)


def write_parquet(conn: sqlite3.Connection, out_dir: Path) -> List[str]:
    """Mirror each table to Parquet when pyarrow is importable."""
    try:
        import pyarrow as pa  # noqa: F401
        import pyarrow.parquet as pq
    except ImportError:
        print(
            "note: pyarrow not importable — Parquet mirror skipped "
            "(sqlite archive is complete on its own)",
            file=sys.stderr,
        )
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for table in PARQUET_TABLES:
        cursor = conn.execute(f"SELECT * FROM {table}")
        columns = [description[0] for description in cursor.description]
        data = cursor.fetchall()
        arrow_table = pa.table(
            {name: [row[index] for row in data] for index, name in enumerate(columns)}
        )
        target = out_dir / f"{table}.parquet"
        pq.write_table(arrow_table, target)
        written.append(str(target))
    return written


def build_archive(
    id_map_path: Path,
    claims_path: Path,
    coa_path: Path,
    content_root: Path,
    ir_path: Optional[Path],
    output_path: Path,
) -> Dict[str, int]:
    entities = load_id_map(id_map_path)
    claims = load_claims(claims_path)
    coa_records = load_coa_records(coa_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=".ted-archive-", suffix=".sqlite"
    )
    os.close(fd)
    temp_path = Path(temp_name)
    temp_path.unlink()  # sqlite3.connect wants to create it itself

    counts: Dict[str, int] = {}
    try:
        conn = sqlite3.connect(str(temp_path))
        try:
            conn.executescript(SCHEMA_SQL)
            counts["entities"] = insert_entities(conn, entities)
            counts["cultivar_claims"] = insert_claims(conn, claims)
            batches, reports, measurements = insert_coa(conn, coa_records)
            counts["coa_batches"] = batches
            counts["coa_reports"] = reports
            counts["coa_measurements"] = measurements
            counts["crosslink_edges"] = insert_crosslinks(
                conn, iter_frontmatter_relations(content_root)
            )
            if ir_path is not None and ir_path.exists():
                graph = json.loads(ir_path.read_text(encoding="utf-8"))
                nodes, edges = insert_ir(conn, graph)
                counts["ir_nodes"] = nodes
                counts["ir_edges"] = edges
            else:
                counts["ir_nodes"] = 0
                counts["ir_edges"] = 0
            meta_rows = [
                {"key": "schema_version", "value": SCHEMA_VERSION},
                {"key": "generator", "value": "scripts/export_sqlite.py"},
                {"key": "censoring_rule",
                 "value": "ND/below_lod/below_loq keep state and printed "
                          "reported_value; value stays NULL; never imputed"},
                {"key": "ir_source",
                 "value": str(ir_path) if ir_path is not None and ir_path.exists()
                 else "absent (run scripts/ted-publish.sh first to include IR)"},
            ]
            conn.executemany(
                "INSERT INTO archive_meta (key, value) VALUES (:key, :value)",
                meta_rows,
            )
            conn.commit()
        finally:
            conn.close()
        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--id-map", type=Path, default=ROOT / "metadata" / "id-map.jsonl",
        help="entity identity map (metadata/id-map.jsonl)",
    )
    parser.add_argument(
        "--claims", type=Path, default=ROOT / "metadata" / "cultivar-claims.jsonl",
        help="cultivar claim registry (metadata/cultivar-claims.jsonl)",
    )
    parser.add_argument(
        "--coa", type=Path, default=ROOT / "metadata" / "coa-records.jsonl",
        help="COA record registry (metadata/coa-records.jsonl)",
    )
    parser.add_argument(
        "--content", type=Path, default=ROOT / "content",
        help="content tree scanned for frontmatter relations",
    )
    parser.add_argument(
        "--ir", type=Path, default=ROOT / "publish" / "ir" / "graph.json",
        help="Boris IR graph to include when present "
             "(publish/ir/graph.json from scripts/ted-publish.sh)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "dist",
        help="directory receiving ted-archive.sqlite",
    )
    parser.add_argument(
        "--with-parquet", action="store_true",
        help="also mirror tables to dist/ted-archive-parquet/ (needs pyarrow)",
    )
    args = parser.parse_args(argv)

    output_path = args.output_dir / "ted-archive.sqlite"
    try:
        counts = build_archive(
            id_map_path=args.id_map,
            claims_path=args.claims,
            coa_path=args.coa,
            content_root=args.content,
            ir_path=args.ir,
            output_path=output_path,
        )
    except ValueError as error:
        print(f"export_sqlite: error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"export_sqlite: error: {error}", file=sys.stderr)
        return 2

    summary = ", ".join(f"{table}={count}" for table, count in sorted(counts.items()))
    print(f"export_sqlite: wrote {output_path} ({summary})")

    if args.with_parquet:
        conn = sqlite3.connect(f"file:{output_path}?mode=ro", uri=True)
        try:
            written = write_parquet(conn, args.output_dir / "ted-archive-parquet")
        finally:
            conn.close()
        for path in written:
            print(f"export_sqlite: wrote {path}")
        if not written:
            print("export_sqlite: parquet mirror skipped (pyarrow unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
