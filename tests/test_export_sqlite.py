"""Research export and cultivar chemotype-leakage audit tests.

``scripts/export_sqlite.py`` compiles the machine records into a researcher
SQLite archive; these tests pin its schema, determinism, censoring
discipline, and graceful handling of a missing IR graph. ``scripts/
audit_cultivar_chemotype.py`` pins the P18 phrasing rules: which lines flag,
which lines pass because they carry evidence anchors, and the structural
firewall check.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from audit_cultivar_chemotype import audit_file  # noqa: E402
from export_sqlite import build_archive  # noqa: E402


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def fixture_id_map() -> list[dict]:
    return [
        {
            "id": "cultivars",
            "collection": "cultivars",
            "form_id": None,
            "legacy_id": "cultivars",
            "parent": None,
            "role": "trunk",
            "source": "cultivars.md",
            "title": "Cultivars",
        },
        {
            "id": "cultivars/TCUL-0001",
            "collection": "cultivars",
            "form_id": "TCUL-0001",
            "legacy_id": "cultivars/TCUL-0001",
            "parent": "cultivars",
            "role": "satellite",
            "source": "cultivars/TCUL-0001.md",
            "title": "Sample Cultivar",
        },
    ]


def fixture_claims() -> list[dict]:
    return [
        {
            "claim_id": "CLM-9001",
            "kind": "claimed_bred_by",
            "subject": "cultivars/TCUL-0001",
            "object": "Sample Breeder",
            "object_is_entity": False,
            "status": "claimed",
            "wording": "Bred by Sample Breeder",
            "source": {
                "name": "Sample Breeder site",
                "type": "producer",
                "url": "https://example.invalid/blue-dream",
                "retrieved": "2026-09-03",
            },
            "notes": "fixture",
        }
    ]


def fixture_coa() -> list[dict]:
    return [
        {
            "schema_version": "1.0",
            "batch": {
                "record_kind": "verified",
                "jurisdiction": "CA",
                "batch_id": "FIXTURE-1",
                "lot_number": "LOT-1",
                "product_id": "products/TPRD-0001",
                "producer_id": None,
                "sample_type": "flower",
                "matrix_detail": None,
                "basis": "dry-weight",
                "decarb_convention": "native",
                "harvest_date": None,
                "package_date": None,
                "production_date": None,
                "metrc_tag": "",
                "cultivar_labels": ["Sample Cultivar"],
                "cultivar_claims": [],
            },
            "report": {
                "jurisdiction": "CA",
                "laboratory": {
                    "jurisdiction": "CA",
                    "lab_id": "testing-laboratories/TSTL-0001",
                    "license_number": "LIC-1",
                    "name": "Fixture Lab",
                },
                "license_references": [],
                "method": {"calibration_type": "unknown"},
            },
            "measurements": [
                {
                    "compound_name": "CBD",
                    "compound_cas": None,
                    "compound_id": None,
                    "state": "numeric",
                    "value": 0.219,
                    "reported_value": "0.219",
                    "reported_unit": "mg/pkg",
                    "lod": 0.001,
                    "loq": 0.003,
                    "unit": "other",
                    "method": None,
                    "test_date": None,
                    "quantitation_note": None,
                    "calculation_formula": None,
                    "conversion": None,
                },
                {
                    "compound_name": "CBC",
                    "compound_cas": None,
                    "compound_id": None,
                    "state": "nd",
                    "value": None,
                    "reported_value": "ND",
                    "reported_unit": "mg/pkg",
                    "lod": 0.00114,
                    "loq": 0.00341,
                    "unit": "other",
                    "method": None,
                    "test_date": None,
                    "quantitation_note": None,
                    "calculation_formula": None,
                    "conversion": None,
                },
            ],
        }
    ]


class ExportSqliteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, with_content: bool = True) -> Path:
        id_map = self.root / "id-map.jsonl"
        claims = self.root / "cultivar-claims.jsonl"
        coa = self.root / "coa-records.jsonl"
        content = self.root / "content" / "cultivars"
        content.mkdir(parents=True, exist_ok=True)
        write_jsonl(id_map, fixture_id_map())
        write_jsonl(claims, fixture_claims())
        write_jsonl(coa, fixture_coa())
        if with_content:
            (content / "TCUL-0001.md").write_text(
                "---\n"
                "id: cultivars/TCUL-0001\n"
                "title: Sample Cultivar\n"
                "parent: cultivars\n"
                "relations: [relates_to=cultivars/TCUL-0002]\n"
                "---\n"
                "# Sample Cultivar\n",
                encoding="utf-8",
            )
        out = self.root / "out" / "ted-archive.sqlite"
        counts = build_archive(
            id_map_path=id_map,
            claims_path=claims,
            coa_path=coa,
            content_root=self.root / "content",
            ir_path=self.root / "absent" / "graph.json",
            output_path=out,
        )
        self.counts = counts
        return out

    def test_counts_and_schema(self):
        out = self.build()
        conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            tables = {
                name
                for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("entities", tables)
            self.assertIn("cultivar_claims", tables)
            self.assertIn("coa_batches", tables)
            self.assertIn("coa_measurements", tables)
            self.assertIn("coa_reports", tables)
            self.assertIn("crosslink_edges", tables)
            self.assertIn("ir_nodes", tables)
            self.assertIn("ir_edges", tables)
            self.assertIn("archive_meta", tables)

            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 2
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM cultivar_claims").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM coa_batches").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM coa_measurements").fetchone()[0], 2
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM coa_reports").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM crosslink_edges").fetchone()[0], 1
            )
            # Missing IR degrades to empty tables, never an error.
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ir_nodes").fetchone()[0], 0
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ir_edges").fetchone()[0], 0
            )
        finally:
            conn.close()

    def test_deterministic_bytes(self):
        first = self.build()
        first_bytes = first.read_bytes()
        second = self.build()
        self.assertEqual(first_bytes, second.read_bytes())

    def test_censoring_discipline_preserved(self):
        out = self.build()
        conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            numeric = conn.execute(
                "SELECT value, reported_value FROM coa_measurements "
                "WHERE compound_name = 'CBD'"
            ).fetchone()
            self.assertEqual(numeric, (0.219, "0.219"))

            nd = conn.execute(
                "SELECT state, value, reported_value FROM coa_measurements "
                "WHERE compound_name = 'CBC'"
            ).fetchone()
            self.assertEqual(nd, ("nd", None, "ND"))
        finally:
            conn.close()

    def test_claim_provenance_columns(self):
        out = self.build()
        conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT source_name, source_type, source_url, source_retrieved "
                "FROM cultivar_claims WHERE claim_id = 'CLM-9001'"
            ).fetchone()
            self.assertEqual(
                row,
                (
                    "Sample Breeder site",
                    "producer",
                    "https://example.invalid/blue-dream",
                    "2026-09-03",
                ),
            )
        finally:
            conn.close()

    def test_ir_edges_decode_typed_endpoints(self):
        id_map = self.root / "id-map.jsonl"
        claims = self.root / "cultivar-claims.jsonl"
        coa = self.root / "coa-records.jsonl"
        write_jsonl(id_map, fixture_id_map())
        write_jsonl(claims, fixture_claims())
        write_jsonl(coa, fixture_coa())
        graph = self.root / "graph.json"
        graph.write_text(
            json.dumps(
                {
                    "schemaVersion": "0.4.0",
                    "nodes": [
                        {
                            "id": "cultivars/TCUL-0001",
                            "title": "Sample Cultivar",
                            "role": "satellite",
                            "parent": "cultivars",
                            "sourcePath": "content/cultivars/TCUL-0001.md",
                        }
                    ],
                    "edges": [
                        {
                            "from": {"type": "page", "value": "cultivars"},
                            "to": {"type": "page", "value": "cultivars/TCUL-0001"},
                            "kind": "parent",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        out = self.root / "out-ir" / "ted-archive.sqlite"
        counts = build_archive(
            id_map_path=id_map,
            claims_path=claims,
            coa_path=coa,
            content_root=self.root / "content",
            ir_path=graph,
            output_path=out,
        )
        self.assertEqual(counts["ir_nodes"], 1)
        self.assertEqual(counts["ir_edges"], 1)
        conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            edge = conn.execute(
                "SELECT source, kind, target FROM ir_edges"
            ).fetchone()
            self.assertEqual(edge, ("cultivars", "parent", "cultivars/TCUL-0001"))
        finally:
            conn.close()

    def test_invalid_id_map_fails_loud(self):
        id_map = self.root / "id-map.jsonl"
        id_map.write_text('{"id": "x"}\n', encoding="utf-8")  # missing keys
        claims = self.root / "cultivar-claims.jsonl"
        coa = self.root / "coa-records.jsonl"
        write_jsonl(claims, fixture_claims())
        write_jsonl(coa, fixture_coa())
        with self.assertRaises(ValueError):
            build_archive(
                id_map_path=id_map,
                claims_path=claims,
                coa_path=coa,
                content_root=self.root,
                ir_path=None,
                output_path=self.root / "out" / "ted-archive.sqlite",
            )


class CultivarChemotypeAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Path label the audit function computes relative to a fake root.
        self.page = self.root / "content" / "cultivars" / "fixture.md"

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self, findings) -> set[str]:
        return {finding.code for finding in findings}

    def test_clean_page_with_evidence_anchors(self):
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text(
            "---\n"
            "id: cultivars/TCUL-0099\n"
            "title: Fixture\n"
            "---\n"
            "{{include includes/cultivar-identity-warning.md}}\n"
            "Batch 123 reported CBD at 19.6% (CLM-9001, observed in the "
            "associated COA).\n",
            encoding="utf-8",
        )
        findings = audit_file(self.page, "content/cultivars/fixture.md")
        self.assertEqual(self.codes(findings), set())

    def test_dominant_terpene_flags(self):
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text(
            "---\n"
            "id: cultivars/TCUL-0099\n"
            "---\n"
            "{{include includes/cultivar-identity-warning.md}}\n"
            "Myrcene is the dominant terpene of this cultivar.\n",
            encoding="utf-8",
        )
        findings = audit_file(self.page, "content/cultivars/fixture.md")
        self.assertIn("CUL-CHM-001", self.codes(findings))

    def test_typical_thc_flags(self):
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text(
            "---\n"
            "id: cultivars/TCUL-0099\n"
            "---\n"
            "{{include includes/cultivar-identity-warning.md}}\n"
            "Typical THC content ranges around 18-24% for this name.\n",
            encoding="utf-8",
        )
        findings = audit_file(self.page, "content/cultivars/fixture.md")
        # Either the phrase rule or the numeric rule fires; both is fine.
        self.assertTrue(self.codes(findings) & {"CUL-CHM-002", "CUL-CHM-006"})

    def test_common_primary_terpenes_flags_medium(self):
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text(
            "---\n"
            "id: cultivars/TCUL-0099\n"
            "---\n"
            "{{include includes/cultivar-identity-warning.md}}\n"
            "- **Common Primary Terpene Descriptors**: myrcene, caryophyllene\n",
            encoding="utf-8",
        )
        findings = audit_file(self.page, "content/cultivars/fixture.md")
        self.assertIn("CUL-CHM-004", self.codes(findings))
        severities = {f.code: f.severity for f in findings}
        self.assertEqual(severities["CUL-CHM-004"], "medium")

    def test_report_attributed_number_passes(self):
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text(
            "---\n"
            "id: cultivars/TCUL-0099\n"
            "---\n"
            "{{include includes/cultivar-identity-warning.md}}\n"
            "The associated lab report measured 19.6% CBD.\n",
            encoding="utf-8",
        )
        findings = audit_file(self.page, "content/cultivars/fixture.md")
        self.assertEqual(self.codes(findings), set())

    def test_missing_firewall_include_flags(self):
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text(
            "---\n"
            "id: cultivars/TCUL-0099\n"
            "---\n"
            "# A page with no identity include and no chemovar link\n",
            encoding="utf-8",
        )
        findings = audit_file(self.page, "content/cultivars/fixture.md")
        self.assertIn("CUL-CHM-000", self.codes(findings))


if __name__ == "__main__":
    unittest.main()
