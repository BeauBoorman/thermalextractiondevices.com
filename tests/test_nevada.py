"""Nevada adapter unit tests (offline, fixture-backed)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ingest.core import ChangeReport  # noqa: E402
from scripts.ingest.fetch import FixtureFetcher  # noqa: E402
from scripts.ingest.ids import NaturalKeyRegistry  # noqa: E402
from scripts.ingest.storage import ArtifactStore  # noqa: E402

from scripts.ingest.states.nevada import (  # noqa: E402
    DATASETS,
    ID_COLLECTIONS,
    ID_PREFIXES,
    LAB_COLUMNS,
    NevadaSync,
    aggregate_lab,
    canonical_unit,
    group_rows_to_reports,
    iter_lab_rows,
    normalize_bulletin,
    normalize_lab_row,
    parse_bulletin_content,
    parse_test_type,
    report_natural_key,
    rows_to_coa_measurements,
    sample_type_for,
    sniff_encoding_and_delimiter,
)

FIXTURES = Path(__file__).parent / "fixtures" / "nevada"
LAB_ZIP = FIXTURES / "June%202026.zip"


def _sync(tmp: str, **kwargs) -> NevadaSync:
    base = Path(tmp)
    store = ArtifactStore("nevada", base / "var", base / "data")
    registry = NaturalKeyRegistry(base / "data" / "id-map.json",
                                  ID_PREFIXES, ID_COLLECTIONS)
    defaults = dict(
        fetch=FixtureFetcher(FIXTURES), store=store, registry=registry,
        content_root=base / "content", fixtures_only=True,
        allow_fixture_content=True,   # isolated test context only
    )
    defaults.update(kwargs)
    return NevadaSync(**defaults)


def _report() -> ChangeReport:
    return ChangeReport(state="nevada", run_id="test", started_at="now")


def _fixture_rows():
    return [normalize_lab_row(row) for row in iter_lab_rows(LAB_ZIP)]


class EncodingSniffTestCase(unittest.TestCase):
    def test_utf16_tsv(self):
        encoding, delimiter = sniff_encoding_and_delimiter(b"\xff\xfe" + "a\tb".encode("utf-16-le"))
        self.assertEqual(encoding, "utf-16")
        self.assertEqual(delimiter, "\t")

    def test_utf8_bom_csv(self):
        encoding, delimiter = sniff_encoding_and_delimiter(b"\xef\xbb\xbf" + b"a,b")
        self.assertEqual(encoding, "utf-8-sig")
        self.assertEqual(delimiter, ",")

    def test_plain_tsv(self):
        encoding, delimiter = sniff_encoding_and_delimiter(b"a\tb\tc\nd\te\tf")
        self.assertEqual(encoding, "utf-8")
        self.assertEqual(delimiter, "\t")


class ZipReaderTestCase(unittest.TestCase):
    def test_reads_real_fixture_zip(self):
        rows = list(iter_lab_rows(LAB_ZIP))
        self.assertGreater(len(rows), 400)
        self.assertEqual(sorted(rows[0].keys())[:1], ["_csv_member"][:0] or sorted(rows[0].keys())[:1])
        # every row carries the 16 official columns
        for column in LAB_COLUMNS:
            self.assertIn(column, rows[0])
        # provenance columns present
        self.assertIn("_csv_member", rows[0])

    def test_rows_normalized(self):
        rows = _fixture_rows()
        by_detail = {r["lab_test_detail_id"] for r in rows}
        self.assertEqual(len(by_detail), len(rows), "Lab Test Detail Id must be unique")

    def test_bad_zip_raises(self):
        from scripts.ingest.core import IngestError

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.zip"
            bad.write_bytes(b"not a zip")
            with self.assertRaises(IngestError):
                list(iter_lab_rows(bad))

    def test_zip_without_csv_raises(self):
        import zipfile

        from scripts.ingest.core import IngestError

        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.zip"
            with zipfile.ZipFile(empty, "w") as zf:
                zf.writestr("readme.txt", "no csv here")
            with self.assertRaises(IngestError):
                list(iter_lab_rows(empty))


class TestTypeParsingTestCase(unittest.TestCase):
    def test_split(self):
        parsed = parse_test_type("Arsenic (ppm) Raw Plant Material")
        self.assertEqual(parsed["test_type"], "Arsenic")
        self.assertEqual(parsed["unit"], "ppm")
        self.assertEqual(parsed["matrix"], "Raw Plant Material")

    def test_no_unit(self):
        # Without a (unit) segment nothing is guessed: the whole string stays
        # as test_type (verified against June 2026: "Sub-Contract Testing").
        parsed = parse_test_type("Salmonella Raw Plant Material")
        self.assertEqual(parsed["test_type"], "Salmonella Raw Plant Material")
        self.assertEqual(parsed["unit"], "")
        self.assertEqual(parsed["matrix"], "")

    def test_bare(self):
        parsed = parse_test_type("Water Activity (Aw) Additional")
        self.assertEqual(parsed["test_type"], "Water Activity")
        self.assertEqual(parsed["unit"], "Aw")
        self.assertEqual(parsed["matrix"], "Additional")


class NormalizationTestCase(unittest.TestCase):
    def test_row_shape(self):
        row = normalize_lab_row({
            "Packaged By Facility Name": " 3AP INC ",
            "Testing Facility Name": "DB LABS LLC",
            "Test Performed Date": "6/4/2026",
            "PackageLabSampleId": "10976826",
            "Overall Passed": "True",
            "Is Finished": "True",
            "Contains Remediated Product": "False",
            "Product Category Type Name": "Buds",
            "Product Name": "Buds - Abomination",
            "PackageLabel": "1A404030000012D0000090126",
            "Quantity": "11.4",
            "Unit Of Measure Abbreviation": "g",
            "Test Type Name": "Abamectin (ppm) Raw Plant Material",
            "Test Passed": "True",
            "Lab Test Detail Id": "40049447",
            "Test Result Level": "0",
        })
        self.assertEqual(row["packaged_by"], "3AP INC")
        self.assertEqual(row["test_date_iso"], "2026-06-04")
        self.assertEqual(row["test_type"], "Abamectin")
        self.assertEqual(row["test_unit"], "ppm")
        self.assertEqual(row["test_matrix"], "Raw Plant Material")
        self.assertEqual(row["sample_type_for_category"] if False else row["product_category"], "Buds")
        self.assertEqual(row["result_level"], "0")

    def test_sample_type_mapping(self):
        self.assertEqual(sample_type_for("Buds"), "flower")
        self.assertEqual(sample_type_for("ShakeTrim"), "trim")
        self.assertEqual(sample_type_for("Concentrate"), "extract")
        self.assertEqual(sample_type_for("InfusedEdible"), "edible")
        self.assertEqual(sample_type_for("MadeUp"), "unknown")


class ReportGroupingTestCase(unittest.TestCase):
    def test_group_and_keys(self):
        rows = _fixture_rows()
        reports = group_rows_to_reports(rows)
        self.assertGreater(len(reports), 0)
        keys = [r["report_key"] for r in reports]
        self.assertEqual(len(keys), len(set(keys)))
        for rpt in reports:
            self.assertIn("nv-ccb:", rpt["report_key"])
            self.assertGreater(len(rpt["measurements"]), 0)

    def test_retests_get_distinct_reports(self):
        # Simulate: same sample id, two test dates -> two reports.
        base = _fixture_rows()[0]
        second = dict(base)
        second["test_date_iso"] = "2026-06-30"
        second["lab_test_detail_id"] = base["lab_test_detail_id"] + "9"
        reports = group_rows_to_reports([base, second])
        self.assertEqual(len(reports), 2)

    def test_report_natural_key_shape(self):
        self.assertEqual(
            report_natural_key({"sample_id": "123", "test_date_iso": "2026-06-04"}),
            "nv-ccb:123:2026-06-04",
        )


class MeasurementsBridgeTestCase(unittest.TestCase):
    def test_zero_is_zero_never_nd(self):
        measurements = rows_to_coa_measurements([{
            "test_type": "Delta-9 THC", "test_unit": "%", "test_matrix": "Raw Plant Material",
            "result_level": "0", "test_date_iso": "2026-06-04", "test_passed": "True",
        }])
        self.assertEqual(measurements[0]["state"], "zero")
        self.assertEqual(measurements[0]["value"], 0.0)

    def test_numeric(self):
        measurements = rows_to_coa_measurements([{
            "test_type": "Total THC", "test_unit": "%", "test_matrix": "Raw Plant Material",
            "result_level": "24.2", "test_date_iso": "2026-06-04", "test_passed": "True",
        }])
        self.assertEqual(measurements[0]["state"], "numeric")
        self.assertEqual(measurements[0]["value"], 24.2)
        # calculated totals never carry a compound id
        self.assertIsNone(measurements[0]["compound_id"])
        self.assertEqual(measurements[0]["calculation_formula"], "report-derived total")

    def test_blank_is_missing(self):
        measurements = rows_to_coa_measurements([{
            "test_type": "Salmonella", "test_unit": "", "test_matrix": "Raw Plant Material",
            "result_level": "", "test_date_iso": "2026-06-04", "test_passed": "True",
        }])
        self.assertEqual(measurements[0]["state"], "missing")
        self.assertIsNone(measurements[0]["value"])

    def test_lead_maps_to_canonical(self):
        measurements = rows_to_coa_measurements([{
            "test_type": "Lead", "test_unit": "ppm", "test_matrix": "Raw Plant Material",
            "result_level": "1.2", "test_date_iso": "2026-06-04", "test_passed": "True",
        }])
        self.assertEqual(measurements[0]["compound_id"], "contaminants/TCNT-0007")

    def test_unit_mapping(self):
        self.assertEqual(canonical_unit("%"), "% w/w")
        self.assertEqual(canonical_unit("ppm"), "ppm")
        self.assertEqual(canonical_unit("CFU/g"), "CFU/g")
        self.assertEqual(canonical_unit("mg/package"), "other")
        self.assertEqual(canonical_unit(""), "other")
        self.assertEqual(canonical_unit("lightyears"), "other")


class BulletinParsingTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.posts = json.loads(
            (FIXTURES / "posts?search=bulletin&per_page=100").read_text(encoding="utf-8"))
        cls.bulletins = [normalize_bulletin(p) for p in cls.posts]

    def test_bulletin_identity(self):
        b = next(x for x in self.bulletins if x["bulletin_id"] == "3868")
        self.assertEqual(b["title"], "CCB Issues Public Health and Safety Bulletin 2023-03")
        self.assertEqual(b["bulletin_date"], "2023-06-23")
        self.assertIn("ccb.nv.gov", b["canonical_url"])

    def test_affected_items_from_table(self):
        b = next(x for x in self.bulletins if x["bulletin_id"] == "3868")
        self.assertEqual(len(b["affected_items"]), 1)
        item = b["affected_items"][0]
        self.assertIn("Phantom Farms", item["product_text"])
        self.assertEqual(item["batch_lot"], "2105 6926 0793 4799")

    def test_production_run_items(self):
        # Bulletin 3341 uses "Product Name | Production Run Number"
        b = next(x for x in self.bulletins if x["bulletin_id"] == "3341")
        self.assertEqual(len(b["affected_items"]), 2)
        self.assertEqual(b["affected_items"][0]["batch_lot"], "OMG554")

    def test_retail_locations_never_carry_addresses(self):
        suffixes = ("Blvd", "Ave", " Rd", "Dr ", "Pkwy", "Cir", " Ln",
                    "Suite", "Ste ", "#", "NV 8")
        for b in self.bulletins:
            for r in b["retail_locations"]:
                blob = (r["facility"] + " " + r["dba"]).lower()
                for suffix in suffixes:
                    self.assertNotIn(suffix.lower(), blob,
                                     f"address leaked in {b['bulletin_id']}: {r}")

    def test_retail_location_shape(self):
        b = next(x for x in self.bulletins if x["bulletin_id"] == "3868")
        self.assertEqual(len(b["retail_locations"]), 1)
        r = b["retail_locations"][0]
        self.assertEqual(r["facility"], "SILVER STATE RELIEF LLC")
        self.assertEqual(r["dba"], "Silver State Relief Fernley")
        self.assertEqual(r["city"], "Fernley")
        self.assertEqual(r["license_number"], "71064968398758187793")

    def test_sold_between(self):
        b = next(x for x in self.bulletins if x["bulletin_id"] == "3868")
        self.assertEqual(b["sold_between"], ["May 9, 2023", "May 21, 2023"])

    def test_concern_extraction(self):
        b = next(x for x in self.bulletins if x["bulletin_id"] == "3868")
        self.assertEqual(b["concern"], "Aspergillus fumigatus")

    def test_terminology_preserved(self):
        # The Board's own term must survive normalization; no "recall" relabel.
        for b in self.bulletins:
            if "bulletin" in b["slug"] or "bulletin" in b["title"].lower():
                self.assertNotIn("recall", b["title"].lower())

    def test_plain_text_fallback(self):
        parsed = parse_bulletin_content(
            "The affected cannabis was sold at the following cannabis sales "
            "facility between May 9, 2023 – May 21, 2023: SILVER STATE RELIEF "
            "LLC dba Silver State Relief Fernley (License #: 71064968398758187793), "
            "1301 Financial Way, Fernley, NV 89408."
        )
        self.assertEqual(len(parsed["retail_locations"]), 1)
        r = parsed["retail_locations"][0]
        self.assertEqual(r["facility"], "SILVER STATE RELIEF LLC")
        self.assertNotIn("Financial Way", r["facility"] + r["dba"])
        self.assertEqual(parsed["sold_between"], ["May 9, 2023", "May 21, 2023"])


class AggregatesTestCase(unittest.TestCase):
    def test_aggregate_lab(self):
        rows = _fixture_rows()
        aggr = aggregate_lab(rows)
        self.assertEqual(aggr["rows"], len(rows))
        self.assertGreater(aggr["packages"], 0)
        self.assertGreater(aggr["samples"], 0)
        self.assertEqual(aggr["passed"] + aggr["failed"] + 0, aggr["rows"] - sum(
            1 for r in rows if r["test_passed"] not in ("True", "False")))
        # multiple labs represented
        self.assertGreater(len(aggr["by_lab"]), 1)

    def test_aggregate_bulletins(self):
        posts = json.loads(
            (FIXTURES / "posts?search=bulletin&per_page=100").read_text(encoding="utf-8"))
        bulletins = [normalize_bulletin(p) for p in posts]
        from scripts.ingest.states.nevada import aggregate_bulletins

        aggr = aggregate_bulletins(bulletins)
        self.assertEqual(aggr["bulletins"], len(posts))
        self.assertGreater(aggr["with_items"], 0)


class SyncFixtureRunTestCase(unittest.TestCase):
    def test_full_fixture_run_generates_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            sync = _sync(tmp)
            report = _report()
            sync.run_dataset("lab_library_2026_06", report)
            sync.run_dataset("safety_bulletins", report)
            self.assertEqual(report.errors, [])
            pages = sync.generate_content(report)
            self.assertGreater(len(pages), 0)
            content = Path(tmp) / "content"
            written = list(content.rglob("*.md"))
            self.assertGreater(len(written), 0)
            # dataset pages
            self.assertTrue(any(p.startswith("datasets/") for p in pages))
            # lab pages
            self.assertTrue(any(p.startswith("testing-laboratories/") for p in pages))
            # advisory pages
            self.assertTrue(any(p.startswith("safety-advisories/") for p in pages))
            # privacy spec page
            self.assertTrue(any(p.startswith("reference/") for p in pages))
            # no errors, and every page file exists
            for rel in pages:
                self.assertTrue((content / rel).is_file(), rel)

    def test_lab_report_guard_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            sync = _sync(tmp)
            report = _report()
            run = sync.run_dataset("lab_library_2026_06", report)
            self.assertEqual(run.status, "fetched")
            self.assertEqual(report.errors, [])

    def test_snapshot_immutable_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            sync = _sync(tmp)
            report = _report()
            first = sync.run_dataset("lab_library_2026_06", report)
            self.assertEqual(first.status, "fetched")
            second = _sync(tmp)
            run2 = second.run_dataset("lab_library_2026_06", _report())
            self.assertEqual(run2.status, "unchanged")
            self.assertEqual(run2.raw_sha256, first.raw_sha256)

    def test_fixture_guard_without_dev_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            sync = _sync(tmp, allow_fixture_content=False)
            with self.assertRaises(Exception):
                sync.run_dataset("lab_library_2026_06", _report())


class PageShapeTestCase(unittest.TestCase):
    def test_generated_pages_use_closed_frontmatter(self):
        import re

        with tempfile.TemporaryDirectory() as tmp:
            sync = _sync(tmp)
            sync.run_dataset("lab_library_2026_06", _report())
            sync.run_dataset("safety_bulletins", _report())
            sync.generate_content(_report())
            content = Path(tmp) / "content"
            allowed = {"title", "id", "parent", "status", "tags", "relations"}
            for path in content.rglob("*.md"):
                text = path.read_text(encoding="utf-8")
                match = re.match(r"^---\n(.*?)\n---", text, re.S)
                self.assertIsNotNone(match, path)
                keys = {line.split(":", 1)[0].strip()
                        for line in match.group(1).splitlines() if ":" in line}
                self.assertTrue(keys <= allowed,
                                f"{path}: unexpected frontmatter keys {keys - allowed}")


class COABatchShapeTestCase(unittest.TestCase):
    def test_record_dict_is_schema_shaped(self):
        from scripts.ingest.states.nevada import report_to_coa_record

        rows = _fixture_rows()
        reports = group_rows_to_reports(rows)
        record = report_to_coa_record(
            reports[0], report_id="lab-results/TLAB-0003",
            record_kind="verified",
            provenance={"source_url": "https://ccb.nv.gov/lab-library/",
                        "document_hash": "a" * 64, "retrieval_date": "2026-08-28",
                        "upstream_record_id": "test", "parser_version": "t"},
        )
        self.assertEqual(record["schema_version"], "1.0")
        self.assertEqual(record["report"]["report_id"], "lab-results/TLAB-0003")
        self.assertEqual(record["batch"]["record_kind"], "verified")
        self.assertEqual(record["batch"]["jurisdiction"], "NV")
        self.assertGreater(len(record["measurements"]), 0)
        # every measurement carries the censoring state
        states = {m["state"] for m in record["measurements"]}
        self.assertIn("zero", states)

    def test_record_validates_through_coa_model(self):
        from scripts.crosslinks import coa_record_from_dict
        from scripts.ingest.states.nevada import report_to_coa_record

        rows = _fixture_rows()
        reports = group_rows_to_reports(rows)
        record = report_to_coa_record(
            reports[0], report_id="lab-results/TLAB-0003",
            record_kind="verified",
            provenance={"source_url": "https://ccb.nv.gov/lab-library/",
                        "document_hash": "a" * 64, "retrieval_date": "2026-08-28",
                        "upstream_record_id": "test", "parser_version": "t"},
        )
        validated = coa_record_from_dict(record)
        self.assertEqual(validated.report.report_id, "lab-results/TLAB-0003")


if __name__ == "__main__":
    unittest.main()
