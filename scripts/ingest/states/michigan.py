"""Michigan Cannabis Regulatory Agency (CRA) ingestion adapter.

Third reference implementation of the shared state-ingest package, after
``massachusetts`` (and the in-flight ``nevada``). Owns everything
Michigan-specific: regulator identity, the official source catalog, dataset
schemas, normalizers, privacy exclusions, the recall-bulletin parser, and
Boris content generation.

This is an offline-first adapter. Michigan's data surface is primarily PDF-based
(monthly reports, recall bulletins, the CRA "Data.pdf" product registry). Live
CSV/API endpoints are not available. Source PDFs are extracted to CSVs committed
under ``data/michigan-cra/``, and recall fixtures live under
``tests/fixtures/michigan/``. The adapter therefore takes no fetcher and
registers no fetchable ``DATASETS`` (the runner's dataset loop is a no-op for
Michigan); provenance lives in ``DATASET_META`` and the durable source catalog.

Key architectural differences from MA/NV:

* No live fetching — all dataset runs read committed extracts.
* The product registry is a Metrc package snapshot (May 2024), not a license
  registry. License information is derived from the CRA monthly statistical
  report (PDF) and the facility list extract.
* Michigan uses **Safety Compliance Facilities** (SCFs) as its testing
  laboratory designation, not "Independent Testing Laboratories."
* Recalls are PDF bulletins, not structured advisory pages.
* COAs are available through individual laboratory portals (Iron Labs) but
  not via a state-level open-data system.

Jurisdiction binding: generated records relate to the existing editorial
profile ``jurisdictions/TJUR-0023`` (``content/jurisdictions/michigan.md``) —
the same pattern Nevada uses (``JURISDICTION_ENTITY``). The adapter never
allocates a jurisdiction entity; TJUR-0076 belongs to Denmark.

Terminology is preserved as Michigan publishes it: "recall bulletin,"
"safety compliance facility," "marihuana" (statutory spelling).
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from ..core import (
    ChangeReport,
    DatasetRun,
    IngestError,
    parse_date,
    utc_now,
    summarize_counts,
)
from ..ids import NaturalKeyRegistry
from ..markdown import (
    callout,
    escape_cell,
    frontmatter,
    h1,
    h2,
    h3,
    mdlink,
    table,
    wikilink,
)
from ..storage import ArtifactStore, sha256_file
from ..validation import PrivacySpec, validate_relations

STATE = "michigan"

# Existing editorial jurisdiction page for Michigan (already on main as
# TJUR-0023, file jurisdictions/michigan.md). The adapter binds generated
# records to this entity and never allocates a jurisdiction ID — the
# Nevada JURISDICTION_ENTITY pattern.
JURISDICTION_ENTITY = "jurisdictions/TJUR-0023"

# ---------------------------------------------------------------------------
# Regulator identity
# ---------------------------------------------------------------------------

REGULATOR = {
    "slug": "michigan-cra",
    "name": "Michigan Cannabis Regulatory Agency",
    "jurisdiction": "Michigan",
    "jurisdiction_code": "MI",
    "site": "https://www.michigan.gov/cra",
    "license_lookup": "https://www.michigan.gov/cra/verify-a-license-1",
    "recalls_url": "https://www.michigan.gov/cra/sections/enforcement-division/recalls",
    "monthly_reports_url": "https://www.michigan.gov/cra/resources/statistical-reports",
    "lab_guidance_url": "https://www.michigan.gov/cra/-/media/Project/Websites/cra/Resources-and-Publications/Guides-and-Manuals/Technical-Guidance-for-Laboratories-Version-5-2---September-2024.pdf",
    "rules_url": "https://www.michigan.gov/cra/sections/laws-and-rules/laws-and-rules",
    "data_catalog_note": (
        "Michigan does not publish a structured open-data catalog. "
        "The CRA Data.pdf product registry (Metrc snapshot, ~May 2024) and "
        "monthly statistical report PDFs are the primary public data surfaces."
    ),
}

DISCLAIMER = (
    "Data is derived from official CRA sources (public PDFs, bulletins). "
    "CSV extracts are machine-generated from PDF source documents and may "
    "contain OCR or column-alignment artifacts. Always consult the original "
    "PDF for regulatory decisions."
)

# ---------------------------------------------------------------------------
# Source catalog (offline datasets)
# ---------------------------------------------------------------------------


def _project_data(path: str) -> Path:
    """Resolve a path relative to the repository root."""
    import sys
    candidate = Path(__file__).resolve().parent.parent.parent.parent
    # Use ROOT from state_ingest if available, else walk up.
    return candidate / path


DATASET_FILES = {
    "facilities": _project_data("data/michigan-cra/facilities.csv"),
    "product_registry": _project_data("data/michigan-cra/product-registry.csv"),
    "monthly_report": _project_data("tests/fixtures/michigan/monthly-report.txt"),
    "recall_exclusive": _project_data("tests/fixtures/michigan/Recall-Bulletin-Exclusive.txt"),
    "recall_flavor_galaxy": _project_data("tests/fixtures/michigan/Recall-Bulletin---Flavor-Galaxy---FINAL.txt"),
}

DATASET_META = {
    "facilities": {
        "title": "Michigan CRA Licensed Facilities (Facility-Product Registry Extract)",
        "description": (
            "Facility-level extract from the CRA Data.pdf product registry "
            "(Metrc package snapshot, ~May 2024). One row per licensed facility; "
            "includes product count, municipality, and license number. License "
            "type/program is derived from the license-number prefix."
        ),
        "source_url": "https://www.michigan.gov/cra/verify-a-license-1",
        "format": "CSV (extracted from PDF)",
        "reporting_period": "~2024-05-09 (PDF metadata)",
        "row_count_note": "196 facilities",
    },
    "product_registry": {
        "title": "Michigan CRA Product Registry (Metrc Package Snapshot)",
        "description": (
            "Product-level extract from the CRA Data.pdf. One row per Metrc-tagged "
            "product; includes facility, municipality, Metrc tag, product name, and "
            "product category. Approximately 6,546 products across 196 facilities."
        ),
        "source_url": "https://www.michigan.gov/cra/verify-a-license-1",
        "format": "CSV (extracted from PDF)",
        "reporting_period": "~2024-05-09 (PDF metadata)",
        "row_count_note": "6,546 product rows",
    },
    "monthly_report": {
        "title": "Michigan CRA Monthly Statistical Report (February 2026)",
        "description": (
            "Official CRA monthly report with aggregate license counts by type, "
            "sales figures, and market statistics. PDF; text extracted via pdftotext."
        ),
        "source_url": "https://www.michigan.gov/cra/resources/statistical-reports",
        "format": "Text (extracted from PDF)",
        "reporting_period": "February 2026",
        "row_count_note": "~2,425 lines of extracted text",
    },
}

# ---------------------------------------------------------------------------
# Content policy
# ---------------------------------------------------------------------------

ID_PREFIXES = {
    "jurisdiction": "TJUR", "license": "TLIC", "organization": "TORG",
    "testing_laboratory": "TSTL", "contaminant": "TCNT", "dataset": "TDTS",
    "requirement": "TREQ", "safety_advisory": "TSAD", "product": "TPRD",
    "batch": "TBAT", "lab_result": "TLBR", "reference": "TREF",
}

ID_COLLECTIONS = {
    "jurisdiction": "jurisdictions", "license": "licenses",
    "organization": "organizations", "testing_laboratory": "testing-laboratories",
    "contaminant": "contaminants", "dataset": "datasets",
    "requirement": "requirements", "safety_advisory": "safety-advisories",
    "product": "products", "batch": "batches", "lab_result": "lab-results",
    "reference": "reference",
}

PAGE_POLICY = {
    "generate_lab_pages": True,
    "generate_advisory_pages": True,
    "generate_license_pages": True,
    "generate_org_pages_for_labs": True,
    "max_license_pages": 196,
}

# ---------------------------------------------------------------------------
# Privacy policy
# ---------------------------------------------------------------------------

PRIVACY_SPEC = PrivacySpec(
    state="michigan",
    entity_allowlists={
        "license": [
            "legal_name", "license_number", "license_type", "program", "status",
            "municipality", "county", "license_start_date", "license_expiration_date",
        ],
        "testing_laboratory": [
            "legal_name", "license_number", "license_type", "program", "status",
            "municipality", "accreditation", "related_jurisdiction",
        ],
        "organization": ["legal_name", "license_numbers", "municipality"],
        "safety_advisory": ["title", "advisory_date", "canonical_url", "concern",
                            "affected_licensees", "affected_products"],
        "dataset": ["title", "slug", "source_url", "format", "reporting_period",
                    "retrieval_date", "description", "disclaimer"],
        "contaminant": ["name", "source_name", "unit", "matrix", "action_limit",
                        "action_limit_source"],
        "requirement": ["title", "citation", "regulator", "official_source_url",
                        "notes"],
    },
)

# Michigan's sources are PDFs, not live endpoints. The runner's dataset loop
# iterates DATASETS; an empty mapping keeps Michigan offline-first (no fetch,
# no snapshot manifest churn) while DATASET_META below still records the
# full provenance for generated dataset pages and the durable source catalog.
DATASETS: dict = {}

# Cross-branch reservation floor. The Nevada adapter (PR #48, in flight as
# of 2026-09-02) commits pages through these watermarks but is not yet on
# main, so seeding from the content tree alone would let Michigan allocate
# the SAME form IDs (it did, before this floor existed: 12 add/add merge
# collisions). Allocation starts above the higher of the content tree and
# these floors; once Nevada merges, the content-tree seed supersedes this
# table and it can be deleted with the Michigan PR's rebase.
CROSS_BRANCH_RESERVATION = {
    "TDTS": 29,   # Nevada: TDTS-0028/0029
    "TLAB": 27,
    "TORG": 71,
    "TREF": 6,   # Nevada: TREF-0006
    "TSAD": 13,   # Nevada: TSAD-0004..0013
    "TSTL": 39,   # Nevada: TSTL-0032..0039
}

# ---------------------------------------------------------------------------
# Licensing constants
# ---------------------------------------------------------------------------

# Michigan license-number prefix mapping.
# AU = Adult-Use, PC = Processor (medical), GR = Grower (medical), etc.
LICENSE_PROGRAM_MAP = {
    "AU-R": "Adult-Use Retailer",
    "AU-G": "Adult-Use Grower",
    "AU-P": "Adult-Use Processor",
    "AU-M": "Adult-Use Microbusiness",
    "AU-S": "Adult-Use Safety Compliance Facility (Lab)",
    "AU-T": "Adult-Use Transporter",
    "PC": "Medical Processor",
    "GR": "Medical Grower",
    "PT": "Medical Provisioning Center",
    "SC": "Medical Safety Compliance Facility (Lab)",
    "TC": "Medical Transporter",
}

LICENSE_CATEGORY_MAP = {
    "AU-R": "Retailer", "AU-G": "Cultivator", "AU-P": "Processor",
    "AU-M": "Microbusiness", "AU-S": "Testing Laboratory",
    "AU-T": "Transporter",
    "PC": "Processor", "GR": "Cultivator", "PT": "Dispensary",
    "SC": "Testing Laboratory", "TC": "Transporter",
}

MUNICIPALITY_COUNTY_MAP = {
    # Counties derived from municipality for major Michigan cannabis cities.
    # This is intentionally sparse — only high-confidence mappings supported
    # by the jurisdiction architecture's existing county-derivation policy.
    "Detroit": "Wayne",
    "Ann Arbor": "Washtenaw",
    "Lansing": "Ingham",
    "Grand Rapids": "Kent",
    "Flint": "Genesee",
    "Kalamazoo": "Kalamazoo",
    "Muskegon": "Muskegon",
    "Bay City": "Bay",
    "Battle Creek": "Calhoun",
    "Jackson": "Jackson",
    "Saginaw": "Saginaw",
    "Traverse City": "Grand Traverse",
    "Adrian": "Lenawee",
    "Monroe": "Monroe",
    "Morenci": "Lenawee",
    "Marshall": "Calhoun",
    "Buchanan": "Berrien",
    "Niles": "Berrien",
    "Benton Harbor": "Berrien",
    "Ypsilanti": "Washtenaw",
    "Warren": "Macomb",
    "Sterling Heights": "Macomb",
    "Utica": "Macomb",
    "Pontiac": "Oakland",
    "Ferndale": "Oakland",
    "Royal Oak": "Oakland",
    "Troy": "Oakland",
    "Southfield": "Oakland",
    "Auburn Hills": "Oakland",
    "Hazel Park": "Oakland",
    "Dearborn": "Wayne",
    "Hamtramck": "Wayne",
    "River Rouge": "Wayne",
    "Inkster": "Wayne",
    "Wyoming": "Kent",
    "Mount Morris": "Genesee",
    "Burton": "Genesee",
}


def derive_county(municipality: str) -> str:
    """Derive county from municipality using the project's deterministic map."""
    return MUNICIPALITY_COUNTY_MAP.get(str(municipality or "").strip(), "")


def derive_program(license_number: str) -> str:
    """Derive program and license type from Michigan license-number prefix."""
    if not license_number:
        return "Unknown"
    for prefix, label in LICENSE_PROGRAM_MAP.items():
        if license_number.startswith(prefix):
            return label
    return "Other"


def derive_category(license_number: str) -> str:
    """Derive license category from Michigan license-number prefix."""
    if not license_number:
        return "Unknown"
    for prefix, label in LICENSE_CATEGORY_MAP.items():
        if license_number.startswith(prefix):
            return label
    return "Other"


def is_lab(license_number: str) -> bool:
    """True if this license number belongs to a Safety Compliance Facility."""
    return bool(license_number) and (
        license_number.startswith("AU-S") or license_number.startswith("SC")
    )


def is_adult_use(license_number: str) -> bool:
    return bool(license_number) and license_number.startswith("AU-")


def is_medical(license_number: str) -> bool:
    return bool(license_number) and not license_number.startswith("AU-")


# ---------------------------------------------------------------------------
# Michigan testing requirements (CRA Lab Technical Guidance 5.2, Sept 2024)
# ---------------------------------------------------------------------------

# Action limits from Technical Guidance for Laboratories Version 5.2 (Sept 2024).
# All values are sourced from the CRA's official lab technical guidance.
# Units are preserved as Michigan publishes them.

TESTING_REQUIREMENTS = {
    "potency": {
        "title": "Potency / Cannabinoid Testing",
        "citation": "CRA Lab Technical Guidance 5.2 §4.1; R 420.304",
        "description": (
            "All marijuana products must be tested for cannabinoid potency. "
            "The CRA requires testing for: THCA, Delta-9-THC, CBDA, CBD, CBN, "
            "CBGA, CBG, Delta-8-THC (when present), and THCV (when present). "
            "Total THC is calculated as: THCA × 0.877 + Delta-9-THC."
        ),
        "note": (
            "Michigan requires delta-8-THC testing when the cannabinoid is "
            "detected above the LOQ. This is a notable difference from several "
            "other states that do not explicitly mandate delta-8 quantification."
        ),
        "analytes": [
            ("THCA", "cannabinoid", ""),
            ("Delta-9-THC", "cannabinoid", ""),
            ("CBDA", "cannabinoid", ""),
            ("CBD", "cannabinoid", ""),
            ("CBN", "cannabinoid", ""),
            ("CBGA", "cannabinoid", ""),
            ("CBG", "cannabinoid", ""),
            ("Delta-8-THC", "cannabinoid", "when present"),
            ("THCV", "cannabinoid", "when present"),
            ("Total THC (calculated)", "cannabinoid", "THCA × 0.877 + Delta-9-THC"),
        ],
    },
    "terpenes": {
        "title": "Terpene Testing",
        "citation": "CRA Lab Technical Guidance 5.2 §4.2",
        "description": (
            "Terpene testing is NOT mandatory in Michigan. Laboratories may "
            "offer terpene profiling as an optional service. This contrasts "
            "with states that require terpene reporting for certain product "
            "categories."
        ),
        "analytes": [],
    },
    "moisture": {
        "title": "Moisture Content / Water Activity",
        "citation": "CRA Lab Technical Guidance 5.2 §4.3; R 420.304(1)(c)",
        "description": (
            "Marijuana flower (usable marihuana) must be tested for moisture "
            "content and water activity. Action limits apply to prevent mold "
            "growth during storage."
        ),
        "analytes": [
            ("Moisture Content", "physical", "≤ 15%", "%", "Usable marihuana (flower)"),
            ("Water Activity (aw)", "physical", "≤ 0.65", "aw", "Usable marihuana (flower)"),
        ],
    },
    "residual_solvents": {
        "title": "Residual Solvents",
        "citation": "CRA Lab Technical Guidance 5.2 §4.4; R 420.304(1)(d)",
        "description": (
            "Required for marijuana concentrates and extract-based products. "
            "Michigan uses the USP <467> residual solvents framework adapted "
            "for cannabis matrices. Class 1, Class 2, and Class 3 solvents "
            "are regulated with specific action limits."
        ),
        "analytes": [
            ("Benzene", "Class 1 solvent", "≤ 2", "ppm", "Concentrates/extracts"),
            ("Carbon Tetrachloride", "Class 1 solvent", "≤ 4", "ppm", "Concentrates/extracts"),
            ("1,2-Dichloroethane", "Class 1 solvent", "≤ 5", "ppm", "Concentrates/extracts"),
            ("1,1-Dichloroethene", "Class 1 solvent", "≤ 8", "ppm", "Concentrates/extracts"),
            ("1,1,1-Trichloroethane", "Class 1 solvent", "≤ 1,500", "ppm", "Concentrates/extracts"),
            ("Acetone", "Class 3 solvent", "≤ 5,000", "ppm", "Concentrates/extracts"),
            ("Butane", "Class 3 solvent", "≤ 5,000", "ppm", "Concentrates/extracts"),
            ("Ethanol", "Class 3 solvent", "≤ 5,000", "ppm", "Concentrates/extracts"),
            ("Ethyl Acetate", "Class 3 solvent", "≤ 5,000", "ppm", "Concentrates/extracts"),
            ("Heptane", "Class 3 solvent", "≤ 5,000", "ppm", "Concentrates/extracts"),
            ("Isopropyl Alcohol", "Class 3 solvent", "≤ 5,000", "ppm", "Concentrates/extracts"),
            ("Pentane", "Class 3 solvent", "≤ 5,000", "ppm", "Concentrates/extracts"),
            ("Propane", "Class 3 solvent", "≤ 5,000", "ppm", "Concentrates/extracts"),
        ],
    },
    "pesticides": {
        "title": "Pesticide Screening",
        "citation": "CRA Lab Technical Guidance 5.2 §4.5; R 420.304(1)(e)",
        "description": (
            "All marijuana products must be screened for pesticides listed in "
            "the CRA's mandatory pesticide panel. The action limit for most "
            "analytes is 0.1 ppm, though selected compounds have higher or "
            "lower limits. Michigan's pesticide list is one of the more "
            "comprehensive state panels."
        ),
        "analytes": [
            ("Abamectin", "insecticide", "≤ 0.5", "ppm", "All product categories"),
            ("Acephate", "insecticide", "≤ 0.1", "ppm", "All product categories"),
            ("Bifenazate", "acaricide", "≤ 0.1", "ppm", "All product categories"),
            ("Bifenthrin", "pyrethroid", "≤ 0.1", "ppm", "All product categories"),
            ("Chlordane", "organochlorine", "≤ 0.1", "ppm", "All product categories"),
            ("Chlorfenapyr", "insecticide", "≤ 0.1", "ppm", "All product categories"),
            ("Chlorpyrifos", "organophosphate", "≤ 0.1", "ppm", "All product categories"),
            ("Cyfluthrin", "pyrethroid", "≤ 0.1", "ppm", "All product categories"),
            ("Cypermethrin", "pyrethroid", "≤ 0.1", "ppm", "All product categories"),
            ("Daminozide", "plant growth regulator", "≤ 0.1", "ppm", "All product categories"),
            ("Diazinon", "organophosphate", "≤ 0.1", "ppm", "All product categories"),
            ("Dichlorvos", "organophosphate", "≤ 0.1", "ppm", "All product categories"),
            ("Dimethomorph", "fungicide", "≤ 0.1", "ppm", "All product categories"),
            ("Etoxazole", "acaricide", "≤ 0.1", "ppm", "All product categories"),
            ("Fenoxycarb", "insect growth regulator", "≤ 0.1", "ppm", "All product categories"),
            ("Fipronil", "insecticide", "≤ 0.1", "ppm", "All product categories"),
            ("Fludioxonil", "fungicide", "≤ 0.1", "ppm", "All product categories"),
            ("Imazalil", "fungicide", "≤ 0.1", "ppm", "All product categories"),
            ("Imidacloprid", "neonicotinoid", "≤ 0.1", "ppm", "All product categories"),
            ("Malathion", "organophosphate", "≤ 0.1", "ppm", "All product categories"),
            ("Metalaxyl", "fungicide", "≤ 0.1", "ppm", "All product categories"),
            ("Methomyl", "carbamate", "≤ 0.1", "ppm", "All product categories"),
            ("Methyl Parathion", "organophosphate", "≤ 0.1", "ppm", "All product categories"),
            ("Myclobutanil", "fungicide", "≤ 0.1", "ppm", "All product categories"),
            ("Paclobutrazol", "plant growth regulator", "≤ 0.1", "ppm", "All product categories"),
            ("Permethrin", "pyrethroid", "≤ 0.1", "ppm", "All product categories"),
            ("Phosmet", "organophosphate", "≤ 0.1", "ppm", "All product categories"),
            ("Piperonyl Butoxide", "synergist", "≤ 0.1", "ppm", "All product categories"),
            ("Prallethrin", "pyrethroid", "≤ 0.1", "ppm", "All product categories"),
            ("Propiconazole", "fungicide", "≤ 0.1", "ppm", "All product categories"),
            ("Pyrethrins", "botanical insecticide", "≤ 1.0", "ppm", "All product categories"),
            ("Spinosad", "biological insecticide", "≤ 0.1", "ppm", "All product categories"),
            ("Spiromesifen", "acaricide", "≤ 0.1", "ppm", "All product categories"),
            ("Tebuconazole", "fungicide", "≤ 0.1", "ppm", "All product categories"),
            ("Thiamethoxam", "neonicotinoid", "≤ 0.1", "ppm", "All product categories"),
        ],
    },
    "heavy_metals": {
        "title": "Heavy Metals",
        "citation": "CRA Lab Technical Guidance 5.2 §4.6; R 420.304(1)(f)",
        "description": (
            "All marijuana products must be tested for the four heavy metals "
            "on Michigan's mandatory panel. Action limits follow USP <2232> "
            "guidance for inhalable cannabis products."
        ),
        "analytes": [
            ("Arsenic", "heavy metal", "≤ 1.5", "µg/g (ppm)", "All product categories (inhalable)"),
            ("Cadmium", "heavy metal", "≤ 0.5", "µg/g (ppm)", "All product categories (inhalable)"),
            ("Lead", "heavy metal", "≤ 0.5", "µg/g (ppm)", "All product categories (inhalable)"),
            ("Mercury", "heavy metal", "≤ 0.1", "µg/g (ppm)", "All product categories (inhalable)"),
            ("Arsenic", "heavy metal", "≤ 3.0", "µg/g (ppm)", "All product categories (oral)"),
            ("Cadmium", "heavy metal", "≤ 1.0", "µg/g (ppm)", "All product categories (oral)"),
            ("Lead", "heavy metal", "≤ 1.0", "µg/g (ppm)", "All product categories (oral)"),
            ("Mercury", "heavy metal", "≤ 0.2", "µg/g (ppm)", "All product categories (oral)"),
        ],
    },
    "microbials": {
        "title": "Microbial Testing",
        "citation": "CRA Lab Technical Guidance 5.2 §4.7; R 420.304(1)(g)",
        "description": (
            "All marijuana products must be tested for the microbial panel. "
            "Michigan uses a four-plex panel: bile-tolerant gram-negative "
            "bacteria, pathogenic E. coli, Salmonella spp., and total yeast "
            "and mold count. Aspergillus (four species) is tested separately."
        ),
        "analytes": [
            ("Bile-Tolerant Gram-Negative Bacteria", "microbial", "≤ 100", "CFU/g", "All product categories"),
            ("Pathogenic E. coli", "microbial", "Not detected in 1g", "—", "All product categories"),
            ("Salmonella spp.", "microbial", "Not detected in 1g", "—", "All product categories"),
            ("Total Yeast and Mold", "microbial", "≤ 10,000", "CFU/g", "Usable marihuana (flower)"),
            ("Total Yeast and Mold", "microbial", "≤ 1,000", "CFU/g", "Extracts/concentrates"),
            ("Total Yeast and Mold", "microbial", "≤ 100", "CFU/g", "Marijuana-infused products (oral)"),
            ("Aspergillus flavus", "microbial", "Not detected in 1g", "—", "All product categories"),
            ("Aspergillus fumigatus", "microbial", "Not detected in 1g", "—", "All product categories"),
            ("Aspergillus niger", "microbial", "Not detected in 1g", "—", "All product categories"),
            ("Aspergillus terreus", "microbial", "Not detected in 1g", "—", "All product categories"),
        ],
    },
    "mycotoxins": {
        "title": "Mycotoxin Testing",
        "citation": "CRA Lab Technical Guidance 5.2 §4.8; R 420.304(1)(h)",
        "description": (
            "All marijuana products must be tested for mycotoxins. Michigan "
            "requires testing for aflatoxins (B1, B2, G1, G2) and ochratoxin A."
        ),
        "analytes": [
            ("Aflatoxin B1", "mycotoxin", "≤ 20", "µg/kg (ppb)", "All product categories"),
            ("Aflatoxin B2", "mycotoxin", "≤ 20", "µg/kg (ppb)", "All product categories"),
            ("Aflatoxin G1", "mycotoxin", "≤ 20", "µg/kg (ppb)", "All product categories"),
            ("Aflatoxin G2", "mycotoxin", "≤ 20", "µg/kg (ppb)", "All product categories"),
            ("Ochratoxin A", "mycotoxin", "≤ 20", "µg/kg (ppb)", "All product categories"),
            ("Total Aflatoxins", "mycotoxin", "≤ 20", "µg/kg (ppb)", "All product categories"),
        ],
    },
    "foreign_matter": {
        "title": "Foreign Matter Inspection",
        "citation": "CRA Lab Technical Guidance 5.2 §4.9; R 420.304(1)(i)",
        "description": (
            "All marijuana products must be visually inspected for foreign "
            "matter (hair, insects, packaging debris, etc.). No numeric action "
            "limit; this is a qualitative pass/fail inspection."
        ),
        "analytes": [],
    },
    "vitamin_e_acetate": {
        "title": "Vitamin E Acetate",
        "citation": "CRA Lab Technical Guidance 5.2 §4.13; R 420.304(1)(k)",
        "description": (
            "Marijuana vapor products (vape cartridges) must be tested for "
            "vitamin E acetate. Action limit is not detected. This requirement "
            "was added following the 2019 EVALI outbreak."
        ),
        "analytes": [
            ("Vitamin E Acetate", "adulterant", "Not detected", "—", "Vapor products"),
        ],
    },
    "remediation": {
        "title": "Remediation and Retesting",
        "citation": "CRA Lab Technical Guidance 5.2 §6; R 420.304a",
        "description": (
            "Michigan permits remediation of failed batches for microbial "
            "contamination only. Remediated batches must be retested for the "
            "failed analyte(s). Remediation is not permitted for pesticides, "
            "heavy metals, residual solvents, or mycotoxins failures. "
            "All remediation must be documented and reported through Metrc."
        ),
        "analytes": [],
    },
    "homogeneity": {
        "title": "Sample Homogeneity",
        "citation": "CRA Lab Technical Guidance 5.2 §5.3",
        "description": (
            "Laboratories must ensure sample homogeneity before analysis. "
            "For flower, a representative sample from throughout the batch "
            "must be homogenized by grinding. For concentrates and infused "
            "products, standard laboratory homogenization techniques apply."
        ),
        "analytes": [],
    },
    "batch_definition": {
        "title": "Batch Definition",
        "citation": "CRA Lab Technical Guidance 5.2 §5.1; R 420.302",
        "description": (
            "A production batch is defined as: (a) For usable marihuana: up "
            "to 15 pounds of flower from the same harvest lot of the same "
            "strain; (b) For concentrates: a single extraction run using a "
            "single solvent type; (c) For infused products: a single "
            "production run with homogeneous mixing."
        ),
        "analytes": [],
    },
}

# ---------------------------------------------------------------------------
# Michigan licensed testing laboratories (Safety Compliance Facilities)
# ---------------------------------------------------------------------------

# Known SCF labs identified from CRA sources and web research.
# Status reflects the most recent public information (2025-2026).
KNOWN_LABS = [
    {
        "name": "Iron Laboratories, LLC",
        "dba": "Iron Labs",
        "license_numbers": ["SC-000018", "AU-S-000018"],
        "municipality": "Walled Lake",
        "website": "https://www.ironlaboratories.com/",
        "coa_portal": "https://results.ironlaboratories.com/",
        "accreditation": "ISO/IEC 17025:2017 (PJLA)",
        "accreditation_id": "PJLA 106612",
        "coa_format": "Public web page at /sample/{id} (enumerable)",
        "methods_note": (
            "Potency: HPLC; Pesticides: LC-MS/MS, GC-MS/MS; Heavy Metals: "
            "ICP-MS; Microbials: qPCR; Residual Solvents: HS-GC-MS; "
            "Mycotoxins: LC-MS/MS"
        ),
        "disciplinary_note": (
            "October 2019: CRA settlement for pesticide/microbial/THC testing "
            "violations ($100,000 fine). Iron Labs contested many findings; "
            "settlement did not constitute an admission of all allegations. "
            "The lab continues to operate as a licensed SCF."
        ),
    },
    {
        "name": "PSI Labs, LLC",
        "dba": "PSI Labs",
        "license_numbers": ["SC-000007", "AU-S-000007"],
        "municipality": "Ann Arbor",
        "website": "https://www.psilabs.org/",
        "coa_portal": "https://results.thepsilabs.org/",
        "accreditation": "ISO/IEC 17025:2017 (PJLA)",
        "accreditation_id": "PJLA 87593",
        "coa_format": "Private portal (login required); QR codes on product labels",
        "methods_note": "Full panel testing; HPLC for potency; LC-MS/MS and GC-MS/MS for pesticides.",
    },
    {
        "name": "ACT Laboratories, Inc.",
        "dba": "ACT Laboratories",
        "license_numbers": ["SC-000019", "AU-S-000019"],
        "municipality": "Lansing",
        "website": "https://www.actlaboratories.com/",
        "coa_portal": "",
        "accreditation": "ISO/IEC 17025:2017 (PJLA)",
        "accreditation_id": "PJLA 110251",
        "coa_format": "Private portal (Batch ID lookup)",
        "methods_note": "Full panel; GC-MS and LC-MS/MS methods.",
        "disciplinary_note": "",
    },
    {
        "name": "North Coast Testing Laboratories, LLC",
        "dba": "North Coast Testing Labs",
        "license_numbers": ["SC-000032", "AU-S-000032"],
        "municipality": "Warren",
        "website": "https://www.northcoasttesting.com/",
        "coa_portal": "",
        "accreditation": "ISO/IEC 17025:2017",
        "accreditation_id": "",
        "coa_format": "Private portal",
        "methods_note": "Full panel testing services.",
    },
    {
        "name": "Steadfast Labs, LLC",
        "dba": "Steadfast Labs",
        "license_numbers": ["SC-000036", "AU-S-000036"],
        "municipality": "Hazel Park",
        "website": "https://www.steadfastlab.com/",
        "coa_portal": "",
        "accreditation": "ISO/IEC 17025:2017",
        "accreditation_id": "",
        "coa_format": "Private portal",
        "methods_note": "Full panel; HPLC, GC-MS, ICP-MS.",
    },
    {
        "name": "Candid Testing, LLC",
        "dba": "Candid Testing",
        "license_numbers": ["SC-000039", "AU-S-000039"],
        "municipality": "Flint",
        "website": "",
        "coa_portal": "",
        "accreditation": "ISO/IEC 17025:2017",
        "accreditation_id": "",
        "coa_format": "Unknown",
        "methods_note": "",
    },
    {
        "name": "Cambium Analytica, LLC",
        "dba": "Cambium Analytica",
        "license_numbers": ["SC-000042", "AU-S-000042"],
        "municipality": "Traverse City",
        "website": "https://www.cambiumanalytica.com/",
        "coa_portal": "",
        "accreditation": "ISO/IEC 17025:2017 (A2LA)",
        "accreditation_id": "",
        "coa_format": "Private portal",
        "methods_note": "Full panel; HPLC, LC-MS/MS, GC-MS/MS, ICP-MS.",
    },
    {
        "name": "Viridis Laboratories, LLC",
        "dba": "Viridis Laboratories",
        "license_numbers": ["SC-000014", "AU-S-000014"],
        "municipality": "Lansing",
        "website": "https://viridislabs.com/",
        "coa_portal": "",
        "accreditation": "ISO/IEC 17025:2017",
        "accreditation_id": "",
        "coa_format": "Private portal",
        "methods_note": (
            "Full panel testing. Historically one of Michigan's largest "
            "cannabis testing labs by volume."
        ),
        "disciplinary_note": (
            "2021: CRA issued a product recall following concerns about "
            "Viridis THC potency results (inflated values). Viridis sued the "
            "CRA. The CRA later entered a settlement agreement and the recall "
            "was partially rescinded. The case highlighted the absence of "
            "inter-laboratory proficiency testing mandates at that time."
        ),
    },
]

# ---------------------------------------------------------------------------
# Recalls and advisories
# ---------------------------------------------------------------------------


def _unwrap_pdf_text(text: str) -> list[str]:
    """Join PDF-extracted line wraps into paragraphs.

    pdftotext breaks sentences mid-word across lines; blank lines separate
    blocks. Joining the fragments first is what lets the recall parsers
    below quote complete sentences instead of line-wrap fragments (the
    original bulletin text is the authority; we never paraphrase it).
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _sentence_containing(paragraph: str, needle: str) -> str:
    """Return the full sentence within ``paragraph`` containing ``needle``."""
    for sentence in re.split(r"(?<=[.:])\s+", paragraph):
        if needle.lower() in sentence.lower():
            return sentence.strip()
    return ""


def _reject_address_bearing(text_value: str) -> str:
    """Blank a bulletin quote if it would carry a street address.

    Defensive privacy gate on top of sentence-level extraction: whatever the
    extractor picks up, a generated page must never reproduce a retail
    premises address. Uses the shared validation STREET_RE (the same
    detector the public-release audit runs), so the parser and the audit
    cannot drift apart.
    """
    from ..validation import STREET_RE
    if text_value and STREET_RE.search(text_value):
        return ""
    return text_value


def parse_exclusive_recall(text: str) -> dict:
    """Parse the Exclusive Brands recall bulletin (August 27, 2025)."""
    paragraphs = _unwrap_pdf_text(text)
    title = "Recall Bulletin: Exclusive Brands — MCT Oil in Vape Carts"
    date_str = "August 27, 2025"
    products = ["Kushy Punch-Vapes (Pineapple Jealousy)"]
    licensees = ["AU-P-000099 (Exclusive Brands, Ann Arbor)"]
    concern = ""
    additional_info = ""

    for paragraph in paragraphs:
        if not concern:
            hit = (_sentence_containing(paragraph, "Medium Chain Triglyceride")
                   or _sentence_containing(paragraph, "MCT oil"))
            if hit:
                concern = hit
        if not additional_info:
            hit = _sentence_containing(paragraph, "sold between")
            if hit:
                additional_info = hit

    if not concern:
        concern = "Vape carts contained Medium Chain Triglyceride (MCT) oil, an unapproved additive."

    return {
        "title": title,
        "date": date_str,
        "concern": concern,
        "products": products,
        "licensees": licensees,
        "additional_info": additional_info,
        "slug": "exclusive-brands-recall",
        "url": "https://www.michigan.gov/cra/sections/enforcement-division/recalls",
    }


def parse_flavor_galaxy_recall(text: str) -> dict:
    """Parse the Flavor Galaxy recall bulletin (May 15, 2024)."""
    paragraphs = _unwrap_pdf_text(text)
    title = "Recall Bulletin: Flavor Galaxy — Untested Infused Pre-Rolls"
    date_str = "May 15, 2024"
    products = ["Infused pre-rolls (1,098 units)"]
    licensees = ["AU-P-000373 (Flavor Galaxy LLC, Hazel Park)"]
    retailers = [line.strip() for line in text.split("\n")
                 if re.match(r"^AU-R-\d+$", line.strip())]
    concern = ""
    additional_info = ""

    for paragraph in paragraphs:
        # The two investigation sentences are the concern. The intro
        # paragraph — which carries the licensee's street address — is
        # deliberately never selected: sentence-level extraction keeps the
        # address out even though pdftotext merged the two paragraphs into
        # one block. The adapter reproduces bulletin prose but never retail
        # premises addresses.
        if not concern:
            hits = [s for s in re.split(r"(?<=[.:])\s+", paragraph)
                    if "did not submit" in s.lower()
                    or "were not tested after" in s.lower()]
            if hits:
                concern = " ".join(hits)
        if not additional_info:
            hit = _sentence_containing(paragraph, "between November 25, 2023")
            if hit:
                additional_info = hit

    concern = _reject_address_bearing(concern)
    additional_info = _reject_address_bearing(additional_info)

    if not concern:
        concern = (
            "Flavor Galaxy did not submit infused pre-rolls for testing in "
            "their final form. Products only had safety compliance testing "
            "for raw flower and potency; distillate/terpenes were added after "
            "testing without re-submission."
        )

    return {
        "title": title,
        "date": date_str,
        "concern": concern,
        "products": products,
        "licensees": licensees,
        "retailers": retailers,
        "additional_info": additional_info,
        "slug": "flavor-galaxy-recall",
        "url": "https://www.michigan.gov/cra/sections/enforcement-division/recalls",
    }


def parse_monthly_report(text: str) -> dict:
    """Extract key statistics from the CRA monthly report text.

    The pdftotext dump interleaves table blocks: label columns and value
    columns land in separate runs (see ``tests/fixtures/michigan/
    monthly-report.txt``), so no reliable "Category  Count" line pattern
    exists. Rather than guess numbers out of scrambled columns, the parser
    reports honestly empty license-count maps; the dataset page describes
    the source without claiming extracted counts. This mirrors the pinned
    contract in ``tests/test_michigan.py``.
    """
    lines = text.strip().split("\n")

    adult_use_counts: dict[str, int] = {}
    medical_counts: dict[str, int] = {}

    return {
        "adult_use_licenses": adult_use_counts,
        "medical_licenses": medical_counts,
        "raw_text_sample": "\n".join(lines[:20]),
    }


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_facilities(rows: list[dict]) -> list[dict]:
    """Normalize facility rows into license records."""
    out = []
    for row in rows:
        lic_num = _clean(row.get("license_number"))
        name = _clean(row.get("facility_name"))
        municipality = _clean(row.get("municipality"))
        normalized = {
            "legal_name": name,
            "license_number": lic_num,
            "license_type": derive_program(lic_num),
            "category": derive_category(lic_num),
            "program": "Adult-Use" if is_adult_use(lic_num) else (
                "Medical" if is_medical(lic_num) else "Unknown"
            ),
            "is_lab": is_lab(lic_num),
            "municipality": municipality,
            "county": derive_county(municipality),
            "zip_code": _clean(row.get("zip_code")),
            "product_count": int(row.get("product_count", 0) or 0),
            "status": "Active",  # Data.pdf only lists active facilities
        }
        out.append(normalized)
    return out


def normalize_products(rows: list[dict]) -> list[dict]:
    """Normalize product-registry rows."""
    out = []
    for row in rows:
        out.append({
            "facility_name": _clean(row.get("facility_name")),
            "license_number": _clean(row.get("license_number")),
            "municipality": _clean(row.get("municipality")),
            "metrc_tag": _clean(row.get("metrc_tag")),
            "product_name": _clean(row.get("product_name")),
            "product_category": _clean(row.get("product_category")),
        })
    return out


# ---------------------------------------------------------------------------
# Aggregate builders
# ---------------------------------------------------------------------------


def aggregate_facilities(rows: list[dict]) -> dict:
    by_category = Counter(r["category"] for r in rows)
    by_program = Counter(r["program"] for r in rows)
    by_municipality = Counter(r["municipality"] for r in rows)
    lab_rows = [r for r in rows if r["is_lab"]]
    return {
        "rows": len(rows),
        "by_category": dict(by_category.most_common()),
        "by_program": dict(by_program.most_common()),
        "by_municipality": dict(by_municipality.most_common(20)),
        "labs": lab_rows,
        "lab_count": len(lab_rows),
    }


def aggregate_products(rows: list[dict]) -> dict:
    by_category = Counter(r["product_category"] for r in rows)
    by_facility = Counter(r["facility_name"] for r in rows)
    return {
        "rows": len(rows),
        "by_category": dict(by_category.most_common(20)),
        "by_facility_top": dict(by_facility.most_common(10)),
    }


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------


class MichiganSync:
    """Offline ingestion pipeline for Michigan CRA data.

    Reads the committed PDF extracts (data/michigan-cra/) and bulletin
    fixtures (tests/fixtures/michigan/), allocates stable Boris IDs through
    the shared registry (seeded against the combined content tree by the
    runner), and generates the Michigan satellite pages. The editorial
    jurisdiction trunk (TJUR-0023 / michigan.md) is never written by this
    pipeline; satellites bind to it via JURISDICTION_ENTITY.
    """

    def __init__(self, *, store: ArtifactStore, registry: NaturalKeyRegistry,
                 content_root: Path, allow_fixture_content: bool = False,
                 fixtures_only: bool = False):
        self.store = store
        self.registry = registry
        self.content_root = content_root
        self.allow_fixture_content = allow_fixture_content
        self.fixtures_only = fixtures_only
        self.facilities: list[dict] = []
        self.products: list[dict] = []
        self.aggregates: dict[str, dict] = {}
        self.facility_data: Path = DATASET_FILES["facilities"]
        self.product_data: Path = DATASET_FILES["product_registry"]
        self.monthly_data: Path = DATASET_FILES["monthly_report"]
        self.recall_exclusive_data: Path = DATASET_FILES["recall_exclusive"]
        self.recall_flavor_data: Path = DATASET_FILES["recall_flavor_galaxy"]

    # --------------------------------------------------------------- data load

    def load_data(self) -> None:
        """Load pre-extracted CSVs and parse them.

        Offline-first guard: in fixture-only mode without the explicit dev
        flag, refuse before touching committed data or generating content —
        the same hard guard Massachusetts and Nevada enforce in
        run_dataset/generate_content.
        """
        if self.fixtures_only and not self.allow_fixture_content:
            raise IngestError(
                "fixture-only mode must not ingest committed Michigan extracts "
                "or generate content; supply --allow-fixture-content for "
                "isolated development output"
            )
        if not self.facility_data.is_file():
            raise IngestError(
                f"Michigan facilities CSV not found at {self.facility_data}. "
                "Run the PDF extraction pipeline first."
            )
        if not self.product_data.is_file():
            raise IngestError(
                f"Michigan product registry CSV not found at {self.product_data}."
            )

        # Load facilities
        with open(self.facility_data, "r", encoding="utf-8") as fh:
            raw_facilities = list(csv.DictReader(fh))
        self.facilities = normalize_facilities(raw_facilities)
        self.aggregates["facilities"] = aggregate_facilities(self.facilities)

        # Load products
        with open(self.product_data, "r", encoding="utf-8") as fh:
            raw_products = list(csv.DictReader(fh))
        self.products = normalize_products(raw_products)
        self.aggregates["product_registry"] = aggregate_products(self.products)

    # --------------------------------------------------------------- id helpers

    def _entity_id(self, entity_type: str, natural_key: str, label: str = "") -> str:
        return self.registry.id_for(entity_type, natural_key, label=label)

    def _jurisdiction_id(self) -> str:
        # Nevada pattern: bind to the existing editorial profile (TJUR-0023,
        # content/jurisdictions/michigan.md). Never allocate a jurisdiction
        # entity — that would mint a new TJUR and collide with the curated
        # jurisdiction index (TJUR-0076 is Denmark's).
        return JURISDICTION_ENTITY

    def _write_page(self, rel_path: str, *, entity_id: str, title: str,
                    parent: Optional[str], tags: list[str], relations: list[str],
                    body: str) -> str:
        path = self.content_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            existing = path.read_text(encoding="utf-8", errors="replace")
            existing_id = re.search(r"^id:\s*(.+?)\s*$", existing, flags=re.M)
            if existing_id and existing_id.group(1).strip().strip('"') != entity_id:
                raise IngestError(
                    f"refusing to overwrite {rel_path}: existing id "
                    f"{existing_id.group(1).strip()!r} differs from {entity_id!r}"
                )
        fm = frontmatter(title=title, entity_id=entity_id, parent=parent,
                         tags=tags, relations=relations)
        path.write_text(fm + "\n\n" + body + "\n", encoding="utf-8")
        return rel_path

    # ------------------------------------------------------------- content gen

    def generate_content(self, report: ChangeReport) -> list[str]:
        pages: list[str] = []

        self.load_data()

        # Preallocate IDs
        self._preallocate_ids()

        pages += self._write_dataset_pages()
        pages += self._write_license_summary_page()
        pages += self._write_lab_pages()
        pages += self._write_requirement_pages()
        pages += self._write_contaminant_pages()
        pages += self._write_recall_pages()
        pages += self._write_landscape_page()
        pages.append(self._write_privacy_spec_page())
        self._write_durable_artifacts()

        report.pages_generated = list(dict.fromkeys(pages))
        return pages

    def _preallocate_ids(self) -> None:
        # NOTE: no jurisdiction allocation — JURISDICTION_ENTITY is fixed.
        # Raise the allocation floor above the cross-branch reservation so
        # this branch's IDs never collide with Nevada's committed-but-unmerged
        # batch (see CROSS_BRANCH_RESERVATION above).
        prefix_to_type = {p: t for t, p in self.registry.prefixes.items()}
        for prefix, floor in CROSS_BRANCH_RESERVATION.items():
            entity_type = prefix_to_type.get(prefix)
            if entity_type is None:
                continue
            current = self.registry._allocated.get(entity_type, 0)
            self.registry._allocated[entity_type] = max(current, floor)
        for slug in DATASET_META:
            self._entity_id("dataset", f"MI:dataset:{slug}",
                            label=DATASET_META[slug]["title"])

        # Licenses
        for fac in self.facilities:
            lic_num = fac.get("license_number", "")
            if lic_num:
                self._entity_id("license", f"MI:lic:{lic_num}", label=lic_num)

        # Labs (separate entities for known labs)
        for lab in KNOWN_LABS:
            for lic_num in lab["license_numbers"]:
                self._entity_id("testing_laboratory", f"MI:lab:{lab['name']}",
                                label=lab["dba"] or lab["name"])

        # Requirements
        for slug in TESTING_REQUIREMENTS:
            self._entity_id("requirement", f"MI:req:{slug}",
                            label=TESTING_REQUIREMENTS[slug]["title"])

        # Contaminants
        contaminants_seen = set()
        for req_slug, req_data in TESTING_REQUIREMENTS.items():
            for analyte in req_data.get("analytes", []):
                name = analyte[0]
                if name not in contaminants_seen:
                    contaminants_seen.add(name)
                    self._entity_id("contaminant", f"MI:contaminant:{name}",
                                    label=name)

        # Recalls
        self._entity_id("safety_advisory", "MI:recall:exclusive-brands",
                        label="Exclusive Brands Recall Bulletin")
        self._entity_id("safety_advisory", "MI:recall:flavor-galaxy",
                        label="Flavor Galaxy Recall Bulletin")

        # Datasets
        self._entity_id("dataset", "MI:dataset:landscape",
                        label="Michigan Cannabis Data Landscape")

    # --------------------------------------------------------- jurisdiction
    # No jurisdiction page is generated. The editorial profile at
    # content/jurisdictions/michigan.md (jurisdictions/TJUR-0023) is the
    # authoritative trunk page; every generated satellite binds to it through
    # JURISDICTION_ENTITY, exactly as the Nevada adapter binds TJUR-0029.

    # -------------------------------------------------------------- datasets

    def _write_dataset_pages(self) -> list[str]:
        pages = []
        for slug, meta in DATASET_META.items():
            entity = self._entity_id("dataset", f"MI:dataset:{slug}", label=meta["title"])
            filename = entity.rsplit("/", 1)[-1]
            rel = f"datasets/{filename}.md"

            body = [
                h1(meta["title"]),
                "",
                meta["description"],
                "",
                "## Source Record",
                "",
                table(
                    ["Field", "Value"],
                    [
                        ["Official source", mdlink(meta["source_url"], "CRA website")],
                        ["Format", meta["format"]],
                        ["Reporting period", meta["reporting_period"]],
                        ["Rows", meta["row_count_note"]],
                    ],
                ),
                "",
                callout("warning", DISCLAIMER),
            ]
            self._write_page(
                rel, entity_id=entity, title=meta["title"],
                parent="datasets",
                tags=["dataset", "michigan", slug],
                relations=[self._jurisdiction_id()],
                body="\n".join(body),
            )
            pages.append(rel)
        return pages

    # -------------------------------------------------------------- licenses

    def _write_license_summary_page(self) -> list[str]:
        pages = []
        aggr = self.aggregates.get("facilities", {})
        entity = self._entity_id("license", "MI:lic:overview",
                                 label="Michigan Licensing Overview")
        rel = f"licenses/{entity.rsplit('/', 1)[-1]}.md"

        # Municipalities table
        muni_rows = [[m, str(c)] for m, c in aggr.get("by_municipality", {}).items()]

        body = [
            h1("Michigan Licensing Overview"),
            "",
            f"**{aggr.get('rows', 0)}** licensed facilities extracted from "
            "the CRA Data.pdf product registry (~May 2024). This is a "
            "facility-product snapshot, not a complete license registry.",
            "",
            "## License Categories",
            "",
            table(
                ["Category", "Count"],
                [[k, str(v)] for k, v in aggr.get("by_category", {}).items()],
            ),
            "",
            "## Program Distribution",
            "",
            table(
                ["Program", "Count"],
                [[k, str(v)] for k, v in aggr.get("by_program", {}).items()],
            ),
            "",
            "## Top Municipalities",
            "",
            table(["Municipality", "Facilities"], muni_rows[:20]),
            "",
            "## License Number Format",
            "",
            "Michigan uses distinct prefixes for adult-use and medical licenses:",
            "",
            "- **AU-R**: Adult-Use Retailer",
            "- **AU-G**: Adult-Use Grower",
            "- **AU-P**: Adult-Use Processor",
            "- **AU-M**: Adult-Use Microbusiness",
            "- **AU-S**: Adult-Use Safety Compliance Facility (Lab)",
            "- **AU-T**: Adult-Use Transporter",
            "- **PC**: Medical Processor",
            "- **GR**: Medical Grower",
            "- **PT**: Medical Provisioning Center",
            "- **SC**: Medical Safety Compliance Facility (Lab)",
            "- **TC**: Medical Transporter",
            "",
            callout("info",
                "The CRA's Accela Civic Access portal offers live license "
                "lookup at " + mdlink(REGULATOR["license_lookup"]) +
                " but does not provide a bulk download or API. The Data.pdf "
                "product registry is the closest available bulk source."
            ),
            "",
            callout("warning", DISCLAIMER),
        ]
        self._write_page(
            rel, entity_id=entity, title="Michigan Licensing Overview",
            parent="licenses",
            tags=["licenses", "michigan", "cra", "summary"],
            relations=[self._jurisdiction_id()],
            body="\n".join(body),
        )
        pages.append(rel)
        return pages

    # ------------------------------------------------------------------ labs

    def _write_lab_pages(self) -> list[str]:
        pages = []
        for lab in KNOWN_LABS:
            entity = self._entity_id("testing_laboratory", f"MI:lab:{lab['name']}",
                                     label=lab["dba"] or lab["name"])
            filename = entity.rsplit("/", 1)[-1]
            rel = f"testing-laboratories/{filename}.md"

            body_parts = [
                h1(lab["dba"] or lab["name"]),
                "",
                f"**Legal name**: {lab['name']}",
                "",
                "## License Information",
                "",
                table(
                    ["License Number", "Type", "Municipality"],
                    [[ln, "Safety Compliance Facility", lab["municipality"]]
                     for ln in lab["license_numbers"]],
                ),
                "",
            ]

            if lab.get("website"):
                body_parts += [
                    "## Website",
                    "",
                    mdlink(lab["website"], lab["dba"] or lab["website"]),
                    "",
                ]

            if lab.get("accreditation"):
                body_parts += [
                    "## Accreditation",
                    "",
                    f"- **Standard**: {lab['accreditation']}",
                ]
                if lab.get("accreditation_id"):
                    body_parts.append(f"- **Identifier**: {lab['accreditation_id']}")
                body_parts.append("")

            if lab.get("coa_portal"):
                body_parts += [
                    "## COA / Results Portal",
                    "",
                    mdlink(lab["coa_portal"]),
                    "",
                    f"**Format**: {lab.get('coa_format', 'Unknown')}",
                    "",
                ]

            if lab.get("methods_note"):
                body_parts += [
                    "## Methods / Instrumentation",
                    "",
                    lab["methods_note"],
                    "",
                ]

            if lab.get("disciplinary_note"):
                body_parts += [
                    "## Regulatory History",
                    "",
                    lab["disciplinary_note"],
                    "",
                ]

            body_parts += [
                callout("warning", DISCLAIMER),
            ]

            self._write_page(
                rel, entity_id=entity, title=lab["dba"] or lab["name"],
                parent="testing-laboratories",
                tags=["testing-laboratory", "michigan", "scf", "cra"],
                relations=[self._jurisdiction_id()],
                body="\n".join(body_parts),
            )
            pages.append(rel)
        return pages

    # --------------------------------------------------------- requirements

    def _write_requirement_pages(self) -> list[str]:
        pages = []
        for slug, req in TESTING_REQUIREMENTS.items():
            entity = self._entity_id("requirement", f"MI:req:{slug}", label=req["title"])
            filename = entity.rsplit("/", 1)[-1]
            rel = f"requirements/{filename}.md"

            body = [
                h1(req["title"]),
                "",
                f"**Citation**: {req['citation']}",
                "",
                req["description"],
                "",
            ]

            if req.get("note"):
                body += [callout("info", req["note"]), ""]

            if req.get("analytes"):
                body += ["## Action Limits", ""]
                analyte_rows = []
                for a in req["analytes"]:
                    row = [a[0], a[1]]
                    if len(a) > 2:
                        row.append(a[2])
                    if len(a) > 3:
                        row.append(a[3])
                    if len(a) > 4:
                        row.append(a[4])
                    analyte_rows.append(row)

                headers = ["Analyte", "Class", "Action Limit", "Unit", "Matrix"][:len(analyte_rows[0])] if analyte_rows else ["Analyte", "Class"]
                body.append(table(headers, analyte_rows))

            body += [
                "",
                callout("tip",
                    "All action limits are sourced from the CRA Technical "
                    "Guidance for Laboratories, Version 5.2 (September 2024). "
                    "Verify against the current version for regulatory compliance."
                ),
                "",
            ]
            self._write_page(
                rel, entity_id=entity, title=req["title"],
                parent="requirements",
                tags=["requirements", "michigan", "testing", slug],
                relations=[self._jurisdiction_id()],
                body="\n".join(body),
            )
            pages.append(rel)
        return pages

    # ----------------------------------------------------------- contaminants

    def _write_contaminant_pages(self) -> list[str]:
        pages = []
        seen = set()
        for req_slug, req in TESTING_REQUIREMENTS.items():
            for analyte in req.get("analytes", []):
                name = analyte[0]
                analyte_class = analyte[1] if len(analyte) > 1 else ""
                if name in seen:
                    continue
                seen.add(name)

                entity = self._entity_id("contaminant", f"MI:contaminant:{name}",
                                         label=name)
                filename = entity.rsplit("/", 1)[-1]
                rel = f"contaminants/{filename}.md"

                limit = ""
                unit = ""
                matrix = ""
                if len(analyte) > 2:
                    limit = analyte[2]
                if len(analyte) > 3:
                    unit = analyte[3]
                if len(analyte) > 4:
                    matrix = analyte[4]

                body = [
                    h1(name),
                    "",
                    f"**Class**: {analyte_class}",
                    "",
                    f"**Action limit**: {limit} {unit}".strip(),
                    f"**Matrix**: {matrix}" if matrix else "",
                    "",
                    f"**Source**: CRA Technical Guidance for Laboratories "
                    f"5.2, §{req['citation'].split('§')[-1] if '§' in req['citation'] else req['citation']}",
                    "",
                    f"**Testing requirement**: [[{self._entity_id('requirement', f'MI:req:{req_slug}')}|{req['title']}]]",
                    "",
                ]
                self._write_page(
                    rel, entity_id=entity, title=name,
                    parent="contaminants",
                    tags=["contaminant", "michigan", analyte_class.lower().replace(" ", "-")],
                    relations=[self._jurisdiction_id()],
                    body="\n".join(body),
                )
                pages.append(rel)
        return pages

    # ------------------------------------------------------------ privacy spec
    def _write_privacy_spec_page(self) -> str:
        entity = self._entity_id(
            "reference", "MI:ref:privacy-spec",
            label="Michigan Ingestion: Privacy and Excluded-Field Specification",
        )
        rel = f"reference/{entity.rsplit('/', 1)[-1]}.md"
        body = [h1("Michigan Ingestion: Privacy and Excluded-Field Specification")]
        body.append(
            "Generated pages from Michigan CRA source data publish only fields "
            "on the explicit allowlists below. The machine-readable "
            "specification is committed at `data/michigan-cra/privacy-spec.md` "
            "and enforced by an automated scan of generated Markdown."
        )
        body += ["", "## Excluded Fields and Values", ""]
        body.append(table(
            ["Category", "Examples"],
            [["Street addresses", "facility premises addresses (not extracted from source PDFs)"],
             ["Owner/officer names", "license-registry owner fields (not present in the registry extract)"],
             ["Contact fields", "emails, phones (not present in the registry extract)"],
             ["Coordinates", "latitude/longitude (not present in the registry extract)"],
             ["Postal codes", "ZIP codes are dropped; only municipality and county are published"],
             ["Fields present merely because they exist in source extracts",
              "all non-allowlisted source fields"]],
        ))
        body += ["", "## Entity Allowlists", ""]
        for entity_type, fields in sorted(PRIVACY_SPEC.entity_allowlists.items()):
            body.append(f"**{entity_type}**: `{'`, `'.join(fields)}`")
        self._write_page(rel, entity_id=entity,
                         title="Michigan Privacy and Excluded-Field Specification",
                         parent="reference",
                         tags=["privacy", "allowlist", "michigan", "ingest"],
                         relations=[], body="\n".join(body))
        return rel

    # ------------------------------------------------------- durable artifacts
    def _write_durable_artifacts(self) -> None:
        spec_lines = [
            "# Michigan CRA — Privacy and Excluded-Field Specification", "",
            f"State: {STATE}  ·  Generator: {self.store.importer_version}", "",
            "## Entity allowlists", "",
        ]
        for entity_type, fields in sorted(PRIVACY_SPEC.entity_allowlists.items()):
            spec_lines += [f"### {entity_type}", ""]
            spec_lines += [f"- `{f}`" for f in fields]
            spec_lines.append("")
        self.store.write_durable_markdown("privacy-spec.md", "\n".join(spec_lines))
        catalog = {
            "regulator": REGULATOR,
            "disclaimer": DISCLAIMER,
            "mode": "offline-first (committed PDF extracts; no live endpoints)",
            "datasets": [
                {"slug": slug, "title": meta["title"],
                 "url": meta["source_url"], "format": meta["format"],
                 "reporting_period": meta["reporting_period"],
                 "row_count_note": meta["row_count_note"],
                 "description": meta["description"]}
                for slug, meta in DATASET_META.items()
            ],
        }
        self.store.write_durable_json("source-catalog.json", catalog)

    # --------------------------------------------------------------- recalls

    def _write_recall_pages(self) -> list[str]:
        pages = []

        # Try to load recall fixtures
        recalls = []
        if self.recall_exclusive_data.is_file():
            text = self.recall_exclusive_data.read_text(encoding="utf-8")
            recalls.append(("MI:recall:exclusive-brands", parse_exclusive_recall(text)))
        if self.recall_flavor_data.is_file():
            text = self.recall_flavor_data.read_text(encoding="utf-8")
            recalls.append(("MI:recall:flavor-galaxy", parse_flavor_galaxy_recall(text)))

        for natural_key, recall in recalls:
            entity = self._entity_id("safety_advisory", natural_key,
                                     label=recall["title"])
            filename = entity.rsplit("/", 1)[-1]
            rel = f"safety-advisories/{filename}.md"

            body = [
                h1(recall["title"]),
                "",
                f"**Date**: {recall.get('date', 'Not specified')}",
                "",
                f"**Source**: {mdlink(recall.get('url', REGULATOR['recalls_url']), 'CRA Recall Bulletins')}",
                "",
            ]

            if recall.get("concern"):
                body += ["## Reason for Recall", "", recall["concern"], ""]

            if recall.get("products"):
                body += ["## Affected Products", ""]
                for p in recall["products"]:
                    body.append(f"- {p}")
                body.append("")

            if recall.get("licensees"):
                body += ["## Affected Licensees", ""]
                for lic in recall["licensees"]:
                    lic_num = lic.split()[0] if lic else ""
                    lic_entity = self.registry.entity_id("license", f"MI:lic:{lic_num.strip()}")
                    if lic_entity and (self.content_root / "licenses" / f"{lic_entity.rsplit('/', 1)[-1]}.md").is_file():
                        body.append(f"- [[{lic_entity}|{lic}]]")
                    else:
                        body.append(f"- {lic}")
                overview = self.registry.entity_id("license", "MI:lic:overview")
                if overview:
                    body += ["", f"See the [[{overview}|Michigan Licensing Overview]] for the full facility list."]
                body.append("")

            if recall.get("retailers"):
                body += ["## Retailers with Affected Product", ""]
                for r in recall["retailers"]:
                    body.append(f"- {r}")
                body.append("")

            if recall.get("additional_info"):
                body += ["## Additional Information", "", recall["additional_info"], ""]

            body += [
                callout("warning",
                    "This bulletin is reproduced from the CRA's official "
                    "publication. Always consult the original PDF for "
                    "regulatory decisions. Product identifiers are preserved "
                    "as published by the CRA."
                ),
                "",
            ]
            self._write_page(
                rel, entity_id=entity, title=recall["title"],
                parent="safety-advisories",
                tags=["safety-advisory", "recall", "michigan", "cra"],
                relations=[self._jurisdiction_id()],
                body="\n".join(body),
            )
            pages.append(rel)
        return pages

    # ------------------------------------------------------------- landscape

    def _write_landscape_page(self) -> list[str]:
        entity = self._entity_id("dataset", "MI:dataset:landscape",
                                 label="Michigan Cannabis Data Landscape")
        rel = f"datasets/{entity.rsplit('/', 1)[-1]}.md"

        body = [
            h1("Michigan Cannabis Data Landscape"),
            "",
            "Michigan's cannabis data surface is PDF-dominated. Unlike "
            "Massachusetts (structured open-data catalog) or California "
            "(DCC download portal), Michigan does not publish license or "
            "testing data as downloadable CSVs.",
            "",
            "## Available Data Surfaces",
            "",
            table(
                ["Surface", "Format", "Quality", "Notes"],
                [
                    ["CRA monthly reports", "PDF", "Medium",
                     "Aggregate statistics; monthly cadence; irregular filenames"],
                    ["Data.pdf product registry", "PDF", "Low–Medium",
                     "~May 2024 Metrc snapshot; not regularly updated"],
                    ["Recall bulletins", "PDF", "Medium",
                     "Per-event press releases; no structured index"],
                    ["Accela license lookup", "Web app", "Low",
                     "Search-only; viewstate-based; no bulk export"],
                    ["Lab COA portals", "Varies", "Medium",
                     "Iron Labs: public/enumerable; most labs: private"],
                    ["Lab technical guidance", "PDF", "High",
                     "Well-structured regulatory document; stable versioning"],
                    ["data.michigan.gov", "CSV/JSON", "Low",
                     "Thin datasets (CRA Scorecard); not the main reporting channel"],
                ],
            ),
            "",
            "## Key Data Gaps",
            "",
            "- **No bulk license download**: The CRA does not offer a CSV/JSON "
            "license registry. The Accela Civic Access portal requires manual "
            "search.",
            "- **No structured testing data**: Unlike MA (CCC testing releases) "
            "or CA (DCC lab results), Michigan does not publish statewide "
            "batch-level testing data.",
            "- **No COA aggregation**: Individual lab portals exist but there "
            "is no state-level COA verification system.",
            "- **No recall index**: Recalls are individual PDFs without a "
            "structured table or API.",
            "",
            "## COA Availability",
            "",
            "Michigan's COA landscape is fragmented across individual laboratory "
            "portals:",
            "",
            "- **Iron Laboratories**: The only known lab with publicly "
            "enumerable COA pages at `/sample/{id}`. Reports include full "
            "analyte panels with results, LOD/LOQ, and pass/fail status.",
            "- **PSI Labs**: COA results behind a login portal; QR codes on "
            "product labels link to restricted pages.",
            "- **ACT, North Coast, Steadfast, Viridis, Cambium, Candid**: "
            "Private portals requiring batch ID lookup or login.",
            "",
            callout("info",
                "See `reports/michigan-coa-source-discovery.md` for the "
                "full COA landscape analysis, including enumeration potential, "
                "identifier structures, and ingestion suitability for each "
                "discovered system."
            ),
            "",
            "## Retrieval Dates",
            "",
            "- Facilities/product CSVs: extracted 2026-08-09 from CRA Data.pdf.",
            "- Monthly report: February 2026 (retrieved 2026-08-09).",
            "- Recall bulletins: retrieved 2026-08-09.",
            "- Lab information: researched 2026-08-09.",
            "",
            callout("warning", DISCLAIMER),
        ]
        self._write_page(
            rel, entity_id=entity, title="Michigan Cannabis Data Landscape",
            parent="datasets",
            tags=["dataset", "michigan", "landscape", "data-quality"],
            relations=[self._jurisdiction_id()],
            body="\n".join(body),
        )
        return [rel]


# Alias so a generic state_ingest runner can construct the sync class.
StateSync = MichiganSync


# Required by the module import surface.
__all__ = [
    "STATE", "REGULATOR", "DISCLAIMER", "ID_PREFIXES", "ID_COLLECTIONS",
    "PAGE_POLICY", "PRIVACY_SPEC", "JURISDICTION_ENTITY", "MichiganSync",
    "StateSync", "TESTING_REQUIREMENTS", "KNOWN_LABS", "DATASET_META",
    "DATASETS", "CROSS_BRANCH_RESERVATION",
]
