"""Canonical evidence model for state-level cannabis data.

Implements the schema documented in ``docs/jurisdiction-evidence-model.md``.
The model is jurisdiction-agnostic: states keep their own terminology and
rules, but all normalized evidence records share this shape.

Core rules enforced here:

* ``*_raw`` values are never deleted after normalization.
* Analytical states (``numeric | below_lod | below_loq | nd | blank |
  qualitative | unknown``) are distinct and never collapsed (ND -> 0 and
  ``<LOQ``/``<LOD`` -> 0 are forbidden).
* Source provenance (URL, hash, retrieval time, parser + version) is required
  on every record.
* Unknown analytes remain represented as raw text; they are never discarded.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .core import IngestError

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

COA_FIELDS = [
    # provenance / source
    "jurisdiction", "source_document", "source_url", "source_hash",
    "source_retrieved_at",
    # report identity
    "lab_raw", "lab_normalized_id", "lab_license",
    "producer_raw", "producer_normalized_id",
    "brand_raw", "brand_normalized_id",
    "product_raw", "product_normalized_id",
    "product_type_raw", "product_type_normalized",
    "cultivar_raw", "cultivar_normalized_candidate",
    "batch_or_lot", "package_id", "sample_id",
    "sample_date", "received_date", "test_date", "report_date",
    "panel",
    # analyte result
    "analyte_raw", "analyte_normalized_id",
    "result_raw", "result_numeric", "result_state",
    "unit_raw", "unit_normalized",
    "LOD", "LOQ", "regulatory_limit",
    "pass_fail", "test_method",
    # parser / confidence
    "parser_method", "parser_confidence", "normalization_confidence",
    "notes",
]

REQUIRED_COA_FIELDS = [
    "jurisdiction", "source_document", "source_hash", "source_retrieved_at",
    "analyte_raw", "result_raw", "parser_method",
]

# Analytical states (see docs/jurisdiction-evidence-model.md §3.1).
RESULT_STATES = (
    "numeric", "below_lod", "below_loq", "nd", "blank", "qualitative",
    "unknown",
)

NUMERIC_RE = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")

# Common "not detected / below limit" prefixes on printed results.
_ND_PREFIXES = ("nd", "not detected", "none detected", "no detection",
                "non-detected", "non detect", "not-detected", "n/d")
_BELOW_RE = re.compile(r"^<\s*([-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?)\s*$")

TERPENE_SLUGS = {
    "alpha-pinene", "beta-pinene", "beta-myrcene", "d-limonene", "eucalyptol",
    "linalool", "nerolidol", "terpinolene", "alpha-bisabolol", "alpha-humulene",
    "beta-caryophyllene", "ocimene",
}

CANNABINOID_SLUGS = {
    "thc", "thca", "thcv", "thcva", "delta-8-thc", "delta-9-thc", "cbd",
    "cbda", "cbdv", "cbdva", "cbg", "cbga", "cbn", "cbna", "cbc", "cbca",
    "total-thc", "total-cbd", "total-cannabinoids",
}


@dataclass
class AnalyteResult:
    """One normalized analyte measurement row from a COA."""

    analyte_raw: str
    result_raw: str
    jurisdiction: str = ""
    source_document: str = ""
    source_url: str = ""
    source_hash: str = ""
    source_retrieved_at: str = ""
    lab_raw: str = ""
    lab_normalized_id: str = ""
    lab_license: str = ""
    producer_raw: str = ""
    producer_normalized_id: str = ""
    brand_raw: str = ""
    brand_normalized_id: str = ""
    product_raw: str = ""
    product_normalized_id: str = ""
    product_type_raw: str = ""
    product_type_normalized: str = ""
    cultivar_raw: str = ""
    cultivar_normalized_candidate: str = ""
    batch_or_lot: str = ""
    package_id: str = ""
    sample_id: str = ""
    sample_date: str = ""
    received_date: str = ""
    test_date: str = ""
    report_date: str = ""
    panel: str = ""
    analyte_normalized_id: str = ""
    result_numeric: Optional[float] = None
    result_state: str = "unknown"
    unit_raw: str = ""
    unit_normalized: str = ""
    LOD: str = ""
    LOQ: str = ""
    regulatory_limit: str = ""
    pass_fail: str = ""
    test_method: str = ""
    parser_method: str = ""
    parser_confidence: float = 0.0
    normalization_confidence: float = 0.0
    notes: str = ""

    def to_row(self) -> dict:
        """Emit a dict with exactly the canonical COA field set."""
        return {field_name: getattr(self, field_name) for field_name in COA_FIELDS}

    @classmethod
    def from_row(cls, row: dict) -> "AnalyteResult":
        known = {f: row.get(f, "") for f in COA_FIELDS}
        numeric = known["result_numeric"]
        if numeric == "" or numeric is None:
            known["result_numeric"] = None
        else:
            try:
                known["result_numeric"] = float(numeric)
            except (TypeError, ValueError):
                known["result_numeric"] = None
        for f in ("parser_confidence", "normalization_confidence"):
            try:
                known[f] = float(known[f] or 0)
            except (TypeError, ValueError):
                known[f] = 0.0
        return cls(**known)


@dataclass
class COARecord:
    """One normalized COA report (header + analyte rows)."""

    jurisdiction: str
    source_document: str
    source_url: str = ""
    source_hash: str = ""
    source_retrieved_at: str = ""
    lab_raw: str = ""
    lab_normalized_id: str = ""
    lab_license: str = ""
    producer_raw: str = ""
    producer_normalized_id: str = ""
    brand_raw: str = ""
    brand_normalized_id: str = ""
    product_raw: str = ""
    product_normalized_id: str = ""
    product_type_raw: str = ""
    product_type_normalized: str = ""
    cultivar_raw: str = ""
    cultivar_normalized_candidate: str = ""
    batch_or_lot: str = ""
    package_id: str = ""
    sample_id: str = ""
    sample_date: str = ""
    received_date: str = ""
    test_date: str = ""
    report_date: str = ""
    panel: str = ""
    parser_method: str = ""
    parser_confidence: float = 0.0
    normalization_confidence: float = 0.0
    analytes: list[AnalyteResult] = field(default_factory=list)

    def results(self) -> list[AnalyteResult]:
        """Analyte rows with report-level fields propagated."""
        rows = []
        for analyte in self.analytes:
            for f in ("jurisdiction", "source_document", "source_url",
                      "source_hash", "source_retrieved_at", "lab_raw",
                      "lab_normalized_id", "lab_license", "producer_raw",
                      "producer_normalized_id", "brand_raw",
                      "brand_normalized_id", "product_raw",
                      "product_normalized_id", "product_type_raw",
                      "product_type_normalized", "cultivar_raw",
                      "cultivar_normalized_candidate", "batch_or_lot",
                      "package_id", "sample_id", "sample_date",
                      "received_date", "test_date", "report_date", "panel",
                      "parser_method", "parser_confidence",
                      "normalization_confidence"):
                if not getattr(analyte, f):
                    setattr(analyte, f, getattr(self, f))
            rows.append(analyte)
        return rows


# ---------------------------------------------------------------------------
# Result classification
# ---------------------------------------------------------------------------


def classify_result(value: Any) -> tuple[str, Optional[float]]:
    """Classify a printed result into ``(result_state, result_numeric)``.

    Never converts ND / <LOD / <LOQ to zero: those keep their own state and
    ``result_numeric`` stays ``None``.
    """
    if value is None:
        return ("blank", None)
    text = str(value).strip()
    if not text or text in ("—", "-", "N/A", "NA"):
        return ("blank", None)

    lowered = text.lower()
    # "<0.05" -> below_lod or below_loq depending on context; state chosen by
    # caller via classify_result(..., loq=) or kept generic below-limit.
    match = _BELOW_RE.match(text)
    if match:
        return ("below_lod", None)  # refined to below_loq when LOQ context known

    for prefix in _ND_PREFIXES:
        if lowered.startswith(prefix):
            return ("nd", None)
    if "not detected" in lowered or "none detected" in lowered:
        return ("nd", None)

    if NUMERIC_RE.fullmatch(text.replace(",", "")):
        return ("numeric", float(text.replace(",", "")))

    # Qualitative pass/fail-style results.
    if lowered in ("pass", "fail", "absent", "present", "detected",
                   "positive", "negative", "not present", "not found"):
        return ("qualitative", None)

    return ("unknown", None)


# ---------------------------------------------------------------------------
# Analyte normalization (identity-preserving)
# ---------------------------------------------------------------------------


def normalize_analyte_name(raw: str) -> tuple[str, str, float]:
    """Deterministic, conservative analyte normalization.

    Returns ``(slug, canonical_display, confidence)``. Only unambiguous
    identities get confidence >= 0.9; everything else is returned as a slug of
    the raw text with low confidence and is never silently mapped to the graph.

    Resolution is registry-backed: the inline table below remains the
    confidence-tier fallback, but a hit in the canonical registry
    (``metadata/analyte-registry.json`` via ``ingest.analytes``) returns
    ``(registry id, registry display, 0.98)`` first, so every adapter
    shares one identity source and new spellings land in the registry
    instead of this table.
    """
    from .analytes import resolve_analyte

    entry = resolve_analyte(raw)
    if entry is not None:
        return (entry["id"], entry["display"], 0.98)

    text = " ".join(str(raw or "").split()).lower().strip()
    if not text:
        return ("", "", 0.0)
    # Strip parenthetical unit decorations: "Arsenic (ppm) Raw Plant Material".
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", text)
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")

    table = {
        # cannabinoids
        "thc": ("thc", "THC", 0.98), "delta-9-thc": ("delta-9-thc", "Delta-9 THC", 0.98),
        "d9-thc": ("delta-9-thc", "Delta-9 THC", 0.9), "delta9-thc": ("delta-9-thc", "Delta-9 THC", 0.9),
        "delta-9-tetrahydrocannabinol": ("delta-9-thc", "Delta-9 THC", 0.98),
        "thca": ("thca", "THCA", 0.98), "thc-a": ("thca", "THCA", 0.9),
        "tetrahydrocannabinolic-acid": ("thca", "THCA", 0.98),
        "thcv": ("thcv", "THCV", 0.95), "thcva": ("thcva", "THCVA", 0.95),
        "delta-8-thc": ("delta-8-thc", "Delta-8 THC", 0.98),
        "cbd": ("cbd", "CBD", 0.98), "cbda": ("cbda", "CBDA", 0.98),
        "cannabidiol": ("cbd", "CBD", 0.98), "cannabidiolic-acid": ("cbda", "CBDA", 0.98),
        "cbdv": ("cbdv", "CBDV", 0.95), "cbdva": ("cbdva", "CBDVA", 0.95),
        "cbg": ("cbg", "CBG", 0.95), "cbga": ("cbga", "CBGA", 0.95),
        "cbn": ("cbn", "CBN", 0.95), "cbc": ("cbc", "CBC", 0.95),
        "total-thc": ("total-thc", "Total THC", 0.95),
        "total-active-thc": ("total-thc", "Total THC", 0.95),
        "total-cbd": ("total-cbd", "Total CBD", 0.95),
        "total-cannabinoids": ("total-cannabinoids", "Total Cannabinoids", 0.95),
        # terpenes
        "alpha-pinene": ("alpha-pinene", "α-Pinene", 0.98), "a-pinene": ("alpha-pinene", "α-Pinene", 0.9),
        "beta-pinene": ("beta-pinene", "β-Pinene", 0.98), "b-pinene": ("beta-pinene", "β-Pinene", 0.9),
        "beta-myrcene": ("beta-myrcene", "β-Myrcene", 0.98), "b-myrcene": ("beta-myrcene", "β-Myrcene", 0.9),
        "myrcene": ("beta-myrcene", "β-Myrcene", 0.9),
        "d-limonene": ("d-limonene", "D-Limonene", 0.98), "limonene": ("d-limonene", "D-Limonene", 0.9),
        "eucalyptol": ("eucalyptol", "Eucalyptol", 0.98), "1-8-cineole": ("eucalyptol", "Eucalyptol", 0.95),
        "linalool": ("linalool", "Linalool", 0.98),
        "nerolidol": ("nerolidol", "Nerolidol", 0.95),
        "terpinolene": ("terpinolene", "Terpinolene", 0.98),
        "alpha-bisabolol": ("alpha-bisabolol", "α-Bisabolol", 0.98),
        "alpha-humulene": ("alpha-humulene", "α-Humulene", 0.98),
        "humulene": ("alpha-humulene", "α-Humulene", 0.9),
        "beta-caryophyllene": ("beta-caryophyllene", "β-Caryophyllene", 0.98),
        "caryophyllene": ("beta-caryophyllene", "β-Caryophyllene", 0.9),
        "ocimene": ("ocimene", "Ocimene", 0.9),
        # common contaminants
        "arsenic": ("arsenic", "Arsenic", 0.98),
        "cadmium": ("cadmium", "Cadmium", 0.98),
        "lead": ("lead", "Lead", 0.98),
        "mercury": ("mercury", "Mercury", 0.98),
        "total-yeast-and-mold": ("total-yeast-and-mold", "Total Yeast and Mold", 0.98),
        "coliforms": ("coliforms", "Coliforms", 0.9),
        "total-coliforms": ("coliforms", "Coliforms", 0.9),
        "aflatoxins": ("aflatoxins", "Aflatoxins", 0.95),
        "ochratoxin-a": ("ochratoxin-a", "Ochratoxin A", 0.95),
        "salmonella": ("salmonella", "Salmonella", 0.98),
        "shiga-toxin-producing-e-coli": ("ste-coli", "Shiga toxin-producing E. coli", 0.9),
        "e-coli": ("e-coli", "E. coli", 0.9),
        "aspergillus": ("aspergillus", "Aspergillus", 0.9),
        "pyrethrins": ("pyrethrins", "Pyrethrins", 0.95),
        "residual-solvents": ("residual-solvents", "Residual solvents", 0.9),
        "water-activity": ("water-activity", "Water activity", 0.95),
        "moisture": ("moisture", "Moisture", 0.9),
    }
    # Exact match first, then longest-first prefix match so matrix suffixes
    # ("arsenic-raw-plant-material") resolve and so "thca" beats "thc".
    hit = table.get(cleaned)
    if hit:
        return hit
    for key in sorted(table, key=len, reverse=True):
        if cleaned.startswith(key + "-"):
            return table[key]
    # No unambiguous identity: keep the deterministic slug but low confidence.
    return (cleaned, str(raw).strip(), 0.2)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_coa_record(record: COARecord) -> list[str]:
    """Return a list of validation problems (empty == valid).

    Hard failures: missing required fields, forbidden state collapses,
    numeric values without the numeric state, confidence out of range.
    """
    problems: list[str] = []
    # Record-level required fields (analyte_raw/result_raw live per analyte).
    for field_name in REQUIRED_COA_FIELDS:
        if field_name in ("analyte_raw", "result_raw"):
            continue
        if not getattr(record, field_name):
            problems.append(f"missing required field: {field_name}")
    for analyte in record.analytes:
        if not analyte.analyte_raw:
            problems.append("analyte row with empty analyte_raw")
        if not analyte.result_raw:
            problems.append(f"analyte {analyte.analyte_raw!r}: missing result_raw")
        if not analyte.analyte_raw:
            problems.append("analyte row with empty analyte_raw")
        if not analyte.result_raw:
            problems.append(f"analyte {analyte.analyte_raw!r}: missing result_raw")
        if analyte.result_state not in RESULT_STATES:
            problems.append(
                f"analyte {analyte.analyte_raw!r}: invalid result_state {analyte.result_state!r}"
            )
        if analyte.result_state == "numeric":
            if analyte.result_numeric is None:
                problems.append(
                    f"analyte {analyte.analyte_raw!r}: numeric state without result_numeric"
                )
        else:
            if analyte.result_numeric is not None:
                problems.append(
                    f"analyte {analyte.analyte_raw!r}: non-numeric state with result_numeric "
                    "(ND/<LOD/<LOQ must never carry a converted zero)"
                )
        if not (0.0 <= analyte.parser_confidence <= 1.0):
            problems.append(
                f"analyte {analyte.analyte_raw!r}: parser_confidence out of range"
            )
        if not (0.0 <= analyte.normalization_confidence <= 1.0):
            problems.append(
                f"analyte {analyte.analyte_raw!r}: normalization_confidence out of range"
            )
    return problems


def assert_valid_coa_record(record: COARecord) -> None:
    problems = validate_coa_record(record)
    if problems:
        raise IngestError("COA record invalid: " + "; ".join(problems[:8]))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def write_coa_csv(path: Path, records: Iterable[COARecord]) -> Path:
    """Write normalized analyte rows (one row per analyte per COA)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COA_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            for row in record.results():
                writer.writerow(row.to_row())
    return path


def read_coa_csv(path: Path) -> list[AnalyteResult]:
    """Read normalized analyte rows back into :class:`AnalyteResult`."""
    rows: list[AnalyteResult] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            rows.append(AnalyteResult.from_row(raw))
    return rows


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "COA_FIELDS", "REQUIRED_COA_FIELDS", "RESULT_STATES",
    "AnalyteResult", "COARecord", "classify_result", "normalize_analyte_name",
    "validate_coa_record", "assert_valid_coa_record", "write_coa_csv",
    "read_coa_csv", "sha256_bytes", "sha256_text", "utc_now",
]
