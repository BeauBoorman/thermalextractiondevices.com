"""Michigan CRA adapter unit tests (offline, fixture-backed)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.ingest.ids import NaturalKeyRegistry
from scripts.ingest.storage import ArtifactStore
from scripts.ingest.states.michigan import (
    DATASETS,
    ID_COLLECTIONS,
    ID_PREFIXES,
    JURISDICTION_ENTITY,
    KNOWN_LABS,
    MichiganSync,
    PRIVACY_SPEC,
    StateSync,
    aggregate_facilities,
    derive_category,
    derive_county,
    derive_program,
    is_adult_use,
    is_lab,
    is_medical,
    normalize_facilities,
    normalize_products,
    parse_exclusive_recall,
    parse_flavor_galaxy_recall,
    parse_monthly_report,
)

FIXTURES = Path(__file__).parent / "fixtures" / "michigan"


def _sample_facility() -> dict:
    return {
        "facility_name": "5 & Dime Detroit",
        "license_number": "PC-000205",
        "municipality": "Detroit",
        "zip_code": "48234",
        "product_count": "2",
    }


def _sample_product() -> dict:
    return {
        "facility_name": "5 & Dime Detroit",
        "license_number": "PC-000205",
        "municipality": "Detroit",
        "zip_code": "48234",
        "metrc_tag": "1A405010002786D000029809",
        "product_name": "Strawberry 1g Infused Preroll",
        "product_category": "Infused Pre-Rolls",
    }


def _sync(tmp: str) -> MichiganSync:
    base = Path(tmp)
    store = ArtifactStore("michigan", base / "var", base / "data")
    registry = NaturalKeyRegistry(base / "data" / "id-map.json", ID_PREFIXES, ID_COLLECTIONS)
    return MichiganSync(
        store=store, registry=registry,
        content_root=base / "content",
        allow_fixture_content=True,  # isolated test context only
    )


class ProgramDerivationTestCase(unittest.TestCase):
    def test_medical_pc_license(self):
        # Michigan labels programs by license-type prefix; "PC" is a medical
        # provisioning-center-adjacent processor prefix in the CRA numbering.
        self.assertEqual(derive_program("PC-000205"), "Medical Processor")
        self.assertFalse(is_adult_use("PC-000205"))
        self.assertTrue(is_medical("PC-000205"))

    def test_adult_use_license(self):
        self.assertEqual(derive_program("AU-R-000521"), "Adult-Use Retailer")
        self.assertTrue(is_adult_use("AU-R-000521"))
        self.assertFalse(is_medical("AU-R-000521"))

    def test_safety_compliance_facility_is_lab(self):
        self.assertTrue(is_lab("AU-S-000018"))
        self.assertTrue(is_lab("SC-000018"))
        self.assertFalse(is_lab("AU-R-000521"))

    def test_license_category_derivation(self):
        self.assertEqual(derive_category("AU-R-000521"), "Retailer")
        self.assertEqual(derive_category("AU-P-000373"), "Processor")
        self.assertEqual(derive_category("AU-S-000018"), "Testing Laboratory")
        self.assertEqual(derive_category("PC-000205"), "Processor")

    def test_county_lookup(self):
        self.assertEqual(derive_county("Detroit"), "Wayne")
        self.assertEqual(derive_county("Ann Arbor"), "Washtenaw")
        # Unknown municipalities fall back to an empty string rather than guessing.
        self.assertEqual(derive_county("Nowhereville"), "")


class FacilityNormalizationTestCase(unittest.TestCase):
    def test_public_fields_extracted(self):
        rows = normalize_facilities([_sample_facility()])
        row = rows[0]
        self.assertEqual(row["license_number"], "PC-000205")
        self.assertEqual(row["legal_name"], "5 & Dime Detroit")
        self.assertEqual(row["program"], "Medical")
        self.assertEqual(row["category"], "Processor")
        self.assertEqual(row["municipality"], "Detroit")
        self.assertEqual(row["county"], "Wayne")
        # Street addresses are never part of the public facility record.
        self.assertNotIn("address", row)
        self.assertNotIn("street", row)

    def test_products_count_is_typed(self):
        rows = normalize_facilities([_sample_facility()])
        self.assertEqual(rows[0]["product_count"], 2)


class ProductNormalizationTestCase(unittest.TestCase):
    def test_metrc_tag_preserved_verbatim(self):
        rows = normalize_products([_sample_product()])
        self.assertEqual(rows[0]["metrc_tag"], "1A405010002786D000029809")
        self.assertEqual(rows[0]["product_name"], "Strawberry 1g Infused Preroll")
        self.assertEqual(rows[0]["product_category"], "Infused Pre-Rolls")


class AggregateTestCase(unittest.TestCase):
    def test_facility_aggregates(self):
        rows = normalize_facilities([
            _sample_facility(),
            {**_sample_facility(), "license_number": "AU-R-000521",
             "facility_name": "7Engines", "municipality": "Buchanan",
             "zip_code": "49107", "product_count": "5"},
        ])
        aggr = aggregate_facilities(rows)
        self.assertEqual(aggr["rows"], 2)
        self.assertEqual(aggr["by_program"]["Medical"], 1)
        self.assertEqual(aggr["by_program"]["Adult-Use"], 1)
        self.assertEqual(aggr["by_municipality"]["Detroit"], 1)


class RecallParsingTestCase(unittest.TestCase):
    def test_parse_exclusive_recall_fixture(self):
        text = (FIXTURES / "Recall-Bulletin-Exclusive.txt").read_text(encoding="utf-8")
        recall = parse_exclusive_recall(text)
        self.assertIn("Exclusive", recall["title"])
        self.assertIn("MCT", recall.get("concern", ""))
        self.assertTrue(recall["licensees"])
        self.assertTrue(recall["url"].startswith("https://www.michigan.gov"))

    def test_parse_flavor_galaxy_recall_fixture(self):
        text = (FIXTURES / "Recall-Bulletin---Flavor-Galaxy---FINAL.txt").read_text(encoding="utf-8")
        recall = parse_flavor_galaxy_recall(text)
        self.assertIn("Flavor Galaxy", recall["title"])
        self.assertTrue(recall["licensees"])
        self.assertTrue(any(r.startswith("AU-R-") for r in recall["retailers"]))


class MonthlyReportParsingTestCase(unittest.TestCase):
    def test_parse_monthly_report_fixture_shape(self):
        # The pdftotext dump of the CRA monthly report uses dot-leader tables
        # whose "Category  Count" pairs are split across lines, so the current
        # parser conservatively returns empty maps. The dataset page therefore
        # describes the source without claiming extracted counts. This test
        # pins the honest behavior rather than pretending extraction works.
        text = (FIXTURES / "monthly-report.txt").read_text(encoding="utf-8")
        report = parse_monthly_report(text)
        self.assertIsInstance(report["adult_use_licenses"], dict)
        self.assertIsInstance(report["medical_licenses"], dict)
        self.assertTrue(report["raw_text_sample"])

    def test_empty_report_does_not_crash(self):
        report = parse_monthly_report("")
        self.assertEqual(report["adult_use_licenses"], {})
        self.assertEqual(report["medical_licenses"], {})


class ReconciliationContractTestCase(unittest.TestCase):
    """Post-rebase contract: the adapter binds to main's editorial entity.

    The stranded branch originally allocated TJUR-0076 for Michigan; main
    has since given TJUR-0076 to Denmark and carries Michigan as TJUR-0023
    (content/jurisdictions/michigan.md). These tests pin the reconciled
    binding so a regression cannot silently re-mint a jurisdiction ID.
    """

    def test_jurisdiction_entity_is_bound_not_allocated(self):
        self.assertEqual(JURISDICTION_ENTITY, "jurisdictions/TJUR-0023")

    def test_registry_never_allocates_jurisdiction_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry = NaturalKeyRegistry(base / "id-map.json", ID_PREFIXES,
                                          ID_COLLECTIONS)
            sync = MichiganSync(
                store=ArtifactStore("michigan", base / "var", base / "data"),
                registry=registry,
                content_root=base / "content",
                allow_fixture_content=True,
            )
            sync.load_data()
            sync._preallocate_ids()
            self.assertEqual(sync._jurisdiction_id(), JURISDICTION_ENTITY)
            allocated = {eid.rsplit("/", 1)[0] for eid in registry.all_ids()}
            self.assertNotIn("jurisdictions", allocated)

    def test_offline_adapter_registers_no_fetchable_datasets(self):
        # Michigan is offline-first: the runner's dataset/fetch loop must be a
        # no-op. DATASETS stays empty; provenance lives in DATASET_META and
        # the durable source catalog.
        self.assertEqual(DATASETS, {})

    def test_statesync_alias(self):
        self.assertIs(StateSync, MichiganSync)

    def test_privacy_allowlists_target_generated_entities(self):
        for entity_type in ("license", "testing_laboratory", "organization",
                            "safety_advisory", "dataset", "contaminant",
                            "requirement"):
            self.assertIn(entity_type, PRIVACY_SPEC.entity_allowlists)


class SyncSmokeTestCase(unittest.TestCase):
    def test_generate_content_from_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            sync = _sync(tmp)
            sync.load_data()
            pages = sync.generate_content(_FakeReport())
            self.assertTrue(len(pages) > 50)
            for rel in pages[:5]:
                self.assertTrue((Path(tmp) / "content" / rel).is_file())

    def test_recall_pages_link_to_existing_license_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            sync = _sync(tmp)
            sync.load_data()
            sync.generate_content(_FakeReport())
            content_root = Path(tmp) / "content"
            for recall in content_root.glob("safety-advisories/TSAD-000*.md"):
                body = recall.read_text(encoding="utf-8")
                if "Recall Bulletin" in body:
                    # Every wiki-link must point at a page that was generated.
                    import re
                    for target in re.findall(r"\[\[([^\]|]+)", body):
                        self.assertTrue(
                            (content_root / f"{target}.md").is_file(),
                            f"{recall.name} links to missing page {target}",
                        )


class _FakeReport:
    """Minimal ChangeReport stand-in for generation smoke tests."""

    def __init__(self) -> None:
        self.pages_generated: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []


if __name__ == "__main__":
    unittest.main()
