"""Canonical analyte registry tests (alias cases from MA/NV surfaces)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ingest.analytes import (  # noqa: E402
    canonical_id,
    load_registry,
    registry_entry,
    resolve_analyte,
)
from scripts.ingest.analyte_validation import (  # noqa: E402
    assert_all_mapped,
    coverage,
    validate_analytes,
)

FIXTURES = ROOT / "tests" / "fixtures"


class RegistryStructureTestCase(unittest.TestCase):
    def test_registry_loads_and_is_structurally_valid(self):
        registry = load_registry()
        entries = registry["entries"]
        self.assertGreater(len(entries), 60)
        ids = [e["id"] for e in entries]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids")

    def test_wiki_sourced_entries_carry_the_identity_triple(self):
        for entry in load_registry()["entries"]:
            if entry["source"] == "wiki":
                for key in ("cas", "inchikey", "wiki_path"):
                    self.assertTrue(entry[key], f"{entry['id']} missing {key}")

    def test_reference_case_from_the_task(self):
        """beta-caryophyllene: BCP/trans-caryophyllene, CAS 87-44-5, NPNUFJ…"""
        entry = registry_entry("beta-caryophyllene")
        self.assertEqual(entry["cas"], "87-44-5")
        self.assertEqual(entry["inchikey"], "NPNUFJAVOOONJE-GFUGXAQUSA-N")
        for alias in ("BCP", "trans-caryophyllene", "caryophyllene"):
            self.assertEqual(resolve_analyte(alias)["id"], "beta-caryophyllene")


class DeltaNineTestCase(unittest.TestCase):
    """The delta-9 vs D9-THC vs THC alias family must not collapse."""

    def test_delta9_spellings_resolve_to_delta_9_thc(self):
        for raw in ("Delta-9 THC", "D9-THC", "delta9-thc",
                    "delta-9-tetrahydrocannabinol",
                    "Delta-9 THC (%) Whole Wet Plant"):
            self.assertEqual(canonical_id(raw), "delta-9-thc", raw)

    def test_bare_thc_stays_thc(self):
        self.assertEqual(canonical_id("THC"), "thc")
        self.assertEqual(canonical_id("THC (%) Raw Plant Material"), "thc")

    def test_thca_is_distinct_from_thc(self):
        self.assertEqual(canonical_id("THCA"), "thca")
        self.assertEqual(canonical_id("THCa"), "thca")
        self.assertEqual(canonical_id("THC-A"), "thca")


class MassachusettsSurfaceTestCase(unittest.TestCase):
    """Alias cases drawn from the MA CCC fixture (Testing Results 2025)."""

    def test_ma_fixture_analytes(self):
        for raw, want in [
            ("Arsenic (ppm) Raw Plant Material", "arsenic"),
            ("Cadmium (ppm) Raw Plant Material", "cadmium"),
            ("Lead (ppm) Raw Plant Material", "lead"),
            ("Mercury (ppm) Raw Plant Material", "mercury"),
            ("THC (%) Raw Plant Material", "thc"),
            ("THCA (%) Raw Plant Material", "thca"),
            ("Total Yeast and Mold (CFU/g) Raw Plant Material",
             "total-yeast-and-mold"),
        ]:
            self.assertEqual(canonical_id(raw), want, raw)

    def test_ma_contaminants_table_resolves(self):
        from scripts.ingest.states.massachusetts import CONTAMINANTS
        labels = [label for _, label, _ in CONTAMINANTS]
        assert_all_mapped(labels, source="ma CONTAMINANTS")


class NevadaSurfaceTestCase(unittest.TestCase):
    """Alias cases drawn from the NV CCB lab-library surface.

    The Nevada adapter ships on its own branch; these spellings are the
    state-variant names it emits (verified against the June 2026 fixture
    on agent/nevada-adapter).
    """

    def test_nv_state_variant_spellings(self):
        for raw, want in [
            ("THCa", "thca"),
            ("CBDa (%) Raw Plant Material", "cbda"),
            ("CBGA (mg/g) Infused Edible", "cbga"),
            ("Alpha-Terpinolene (%) Raw Plant Material", "terpinolene"),
            ("Beta-Caryophyllene (%) Non-Solvent Concentrate",
             "beta-caryophyllene"),
            ("Caryophyllene Oxide (%) Whole Wet Plant",
             "caryophyllene-oxide"),
            ("Total Potential THC (%) Raw Plant Material", "total-thc"),
            ("Total Cannabinoids", "total-cannabinoids"),
            ("Total Terpenes", "total-terpenes"),
            ("Other Terpenes", "total-terpenes"),
            ("Pathogenic E. Coli Infused Edible", "ste-coli"),
            ("Salmonella Non-Solvent Concentrate", "salmonella"),
            ("Total Viable Aerobic Bacteria", "total-viable-aerobic-bacteria"),
            ("Total Enterobacteriaceae", "total-enterobacteriaceae"),
        ]:
            self.assertEqual(canonical_id(raw), want, raw)

    def test_aspergillus_species_collapse_to_genus(self):
        for species in ("Aspergillus flavus", "Aspergillus fumigatus",
                        "Aspergillus Niger Raw Plant Material",
                        "Aspergillus Terreus Solvent Based Concentrate"):
            self.assertEqual(canonical_id(species), "aspergillus", species)

    def test_nv_pesticide_spellings(self):
        for raw in ("Bifenazate (ppm) Whole Wet Plant",
                    "Piperonyl Butoxide (ppm) Raw Plant Material",
                    "Pyrethrin (ppm) Non-Solvent Concentrate"):
            self.assertIsNotNone(resolve_analyte(raw), raw)

    def test_residual_solvents(self):
        for raw, want in [("Butane (ppm) Solvent Based Concentrate", "butane"),
                          ("Propane", "propane"),
                          ("Heptane (ppm) Solvent Based Concentrate", "heptane")]:
            self.assertEqual(canonical_id(raw), want, raw)


class GreekAndMatrixTestCase(unittest.TestCase):
    def test_greek_glyph_and_word_spellings_agree(self):
        self.assertEqual(canonical_id("β-Myrcene"), canonical_id("Beta-Myrcene"))
        self.assertEqual(canonical_id("α-Pinene"), canonical_id("Alpha-Pinene"))

    def test_matrix_words_are_stripped_not_matched(self):
        entry = resolve_analyte("Arsenic (ppm) Whole Wet Plant")
        self.assertEqual(entry["id"], "arsenic")

    def test_unmatched_names_never_guess(self):
        self.assertIsNone(resolve_analyte("Mystery Analyte 77"))
        self.assertEqual(canonical_id("Mystery Analyte 77"),
                         "mystery-analyte-77")


class ValidatorTestCase(unittest.TestCase):
    def test_unmapped_is_warning_by_default(self):
        findings = validate_analytes(["Arsenic (ppm) Raw Plant Material",
                                      "Brand New Panel XYZ"])
        self.assertEqual(len(findings), 1)
        severity, rule, message = findings[0]
        self.assertEqual(severity, "warning")
        self.assertEqual(rule, "analyte-registry")
        self.assertIn("Brand New Panel XYZ", message)

    def test_fail_on_unresolved_hardens_the_tier(self):
        findings = validate_analytes(["Brand New Panel XYZ"],
                                     fail_on_unresolved=True)
        self.assertEqual(findings[0][0], "error")

    def test_source_label_carries_into_the_message(self):
        findings = validate_analytes(["Brand New Panel XYZ"], source="nv-ccb")
        self.assertIn("[nv-ccb]", findings[0][2])

    def test_mixed_surface_reports_only_unknowns(self):
        findings = validate_analytes(["THC (%) Raw Plant Material",
                                      "Water Activity",
                                      "Something Else Entirely"])
        self.assertEqual([f[2].split(":")[1].strip().split()[0]
                          for f in findings], ["'Something"])

    def test_assert_all_mapped_raises(self):
        with self.assertRaises(ValueError):
            assert_all_mapped(["Totally Unknown Thing"], source="test")

    def test_coverage_summary(self):
        summary = coverage(["THC (%) Raw Plant Material", "THC", "Mystery"])
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["resolved"], 2)
        self.assertEqual(summary["unresolved"], ["Mystery"])


class EvidenceBridgeTestCase(unittest.TestCase):
    """normalize_analyte_name stays contract-compatible, registry-backed."""

    def test_registry_hit_returns_high_confidence(self):
        from scripts.ingest.evidence import normalize_analyte_name
        slug, display, confidence = normalize_analyte_name("Beta-Caryophyllene")
        self.assertEqual(slug, "beta-caryophyllene")
        self.assertEqual(display, "β-Caryophyllene")
        self.assertGreaterEqual(confidence, 0.9)

    def test_unmatched_keeps_low_confidence_slug(self):
        from scripts.ingest.evidence import normalize_analyte_name
        slug, display, confidence = normalize_analyte_name("Mystery Analyte 77")
        self.assertEqual(slug, "mystery-analyte-77")
        self.assertLess(confidence, 0.3)


if __name__ == "__main__":
    unittest.main()
