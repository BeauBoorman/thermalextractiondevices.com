"""Nevada Cannabis Compliance Board (CCB) ingestion adapter.

Second reference implementation of the shared state-ingest package, after
``massachusetts``. Owns everything Nevada-specific: regulator identity, the
official source catalog, dataset schemas, normalizers, privacy exclusions,
the WP-JSON advisory parser, and Boris content generation.

Official sources (verified 2026-08-28):

* Lab Library   https://ccb.nv.gov/lab-library/  (monthly + yearly ZIPs)
* Lab data ZIPs https://ccbdownload.wpenginepowered.com/wp-content/uploads/YYYY/MM/<Month YYYY>.zip
* Advisories    https://ccb.nv.gov/wp-json/wp/v2/posts?search=bulletin (WordPress REST API)
* Regulator     https://ccb.nv.gov/

Lab-library payload contract, verified against the June 2026 extract
(sha256 bf85d60a…, 6,262,370 bytes compressed, 221,338,604 uncompressed):

* each monthly ZIP contains one directory ``<Month YYYY>/`` with two CSVs
  covering half-month windows (``MM.DD.YYYY_MM.DD.YYYY.csv``);
* payloads are **UTF-16 LE with BOM, tab-separated**, not UTF-8 CSV — the
  reader sniffs BOM/encoding instead of trusting an extension;
* 16 columns, exactly the schema documented in the Lab Library README
  (``READ_ME_FIRST_LAB_DATA.xlsx``);
* one row per (sample, analyte) test detail; ``Lab Test Detail Id`` is a
  true primary key (0 duplicates across 569,766 rows in June 2026);
* ``PackageLabSampleId`` alone is NOT a report key: retests re-use the same
  sample id with a different ``Test Performed Date`` (1631 ``Overall Passed``
  conflicts and 333 date conflicts inside June 2026 alone). The report
  natural key is ``(PackageLabSampleId, Test Performed Date)``;
* ``PackageLabel`` is the Metrc package tag and the batch natural key;
* ``Test Type Name`` embeds unit and matrix (``Arsenic (ppm) Raw Plant
  Material``) in the same shape as Massachusetts analyte strings;
* ``Test Result Level`` is numeric or blank (blank only on pass rows).

Privacy: lab-test rows carry facility names (business names, already public
in license data), package tags, quantities, and analyte results — no
addresses, contacts, owner names, or coordinates. The allowlist still
enforces what may reach generated Markdown. Advisory posts (WP-JSON) are
published government notices; the parser keeps the facility name, city, and
license number from the retail-location line but never the street address.

Terminology is preserved exactly as the Board publishes it: "Public Health
and Safety Bulletin". We never relabel a bulletin as a recall.
"""

from __future__ import annotations

import html as _html
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from ..core import (
    ChangeReport,
    DatasetRun,
    IngestError,
    parse_date,
    utc_now,
)
from ..diff import DiffResult
from ..fetch import Fetcher, FixtureFetcher
from ..ids import NaturalKeyRegistry
from ..markdown import (
    callout,
    escape_cell,
    frontmatter,
    h1,
    mdlink,
    table,
)
from ..schema import (
    SchemaSpec,
    check_date_regression,
    check_duplicate_keys,
    check_row_collapse,
    check_source_staleness,
)
from ..storage import ArtifactStore, sha256_file
from ..validation import PrivacySpec

STATE = "nevada"

# ---------------------------------------------------------------------------
# Regulator identity
# ---------------------------------------------------------------------------

REGULATOR = {
    "slug": "nevada-ccb",
    "name": "Nevada Cannabis Compliance Board",
    "jurisdiction": "Nevada",
    "jurisdiction_code": "NV",
    "site": "https://ccb.nv.gov/",
    "lab_library": "https://ccb.nv.gov/lab-library/",
    "advisories_api": (
        "https://ccb.nv.gov/wp-json/wp/v2/posts?search=bulletin&per_page=100"
    ),
}

DISCLAIMER = (
    "Lab results are reported by licensed independent testing laboratories "
    "to the Nevada Cannabis Compliance Board through the state traceability "
    "system and published as-is. The Board does not guarantee completeness "
    "or accuracy of individual results. A single result implies nothing "
    "about consumer safety without the applicable requirement, unit, "
    "matrix, and action limit."
)

BULLETIN_DISCLAIMER = (
    "Public Health and Safety Bulletins are published by the Nevada "
    "Cannabis Compliance Board. The Board's terminology is preserved; "
    "bulletins are not relabeled as recalls unless the Board does so."
)

# ---------------------------------------------------------------------------
# Content policy
# ---------------------------------------------------------------------------

PAGE_POLICY = {
    # Verified lab-result pages (content/lab-results/TLAB-XXXX.md) require
    # one-to-one parity with durable verified COA records (audit rule
    # COA-08), and the lab-results/TLAB-XXXX id space is bounded by its
    # four-digit form id. The committed publication batch is therefore
    # deliberately bounded; full per-batch detail lives in the ingest
    # working artifacts and the durable dataset record.
    "verified_batch_max": 25,
    "generate_lab_pages": True,
    "generate_advisory_pages": True,
}

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

ID_PREFIXES = {
    "testing_laboratory": "TSTL", "dataset": "TDTS",
    "safety_advisory": "TSAD", "lab_report": "TLAB",
    "organization": "TORG", "reference": "TREF",
}

ID_COLLECTIONS = {
    "testing_laboratory": "testing-laboratories", "dataset": "datasets",
    "safety_advisory": "safety-advisories", "lab_report": "lab-results",
    "organization": "organizations", "reference": "reference",
}

# Existing editorial jurisdiction page for Nevada (already on main).
JURISDICTION_ENTITY = "jurisdictions/TJUR-0029"

# ---------------------------------------------------------------------------
# Source catalog
# ---------------------------------------------------------------------------


@dataclass
class DatasetDef:
    slug: str
    title: str
    url: str
    format: str                          # zip | json
    reporting_period: str
    source_last_updated: str
    description: str
    large: bool = False
    required_columns: list = field(default_factory=list)
    column_types: dict = field(default_factory=dict)
    key_columns: list = field(default_factory=list)
    duplicate_key_policy: str = "fail"
    disclaimer: str = DISCLAIMER
    clarification: str = ""

    def schema_spec(self) -> SchemaSpec:
        return SchemaSpec(
            name=self.slug,
            required=self.required_columns,
            column_types=self.column_types,
            key_columns=self.key_columns,
            duplicate_key_policy=self.duplicate_key_policy,
        )

    def check_headers(self, headers: list) -> None:
        self.schema_spec().check_headers(headers)


def _lab_zip_url(month_name: str, year: int) -> str:
    """Dated download URL pattern verified live 2026-08-28."""
    encoded = month_name.replace(" ", "%20")
    return (f"https://ccbdownload.wpenginepowered.com/wp-content/uploads/"
            f"{year}/04/{encoded}%20{year}.zip")


LAB_COLUMNS = [
    "Packaged By Facility Name", "Testing Facility Name", "Test Performed Date",
    "PackageLabSampleId", "Overall Passed", "Is Finished",
    "Contains Remediated Product", "Product Category Type Name", "Product Name",
    "PackageLabel", "Quantity", "Unit Of Measure Abbreviation",
    "Test Type Name", "Test Passed", "Lab Test Detail Id", "Test Result Level",
]

DATASETS: dict = {}


def _define(d: DatasetDef) -> None:
    DATASETS[d.slug] = d


_define(DatasetDef(
    slug="lab_library_2026_06",
    title="CCB Lab Library — June 2026 Monthly Extract",
    url=_lab_zip_url("June", 2026),
    format="zip",
    reporting_period="2026-06-01 .. 2026-06-30",
    source_last_updated="2026-07-14 (ZIP entry timestamps)",
    description=(
        "Per-batch Metrc laboratory testing extract: two half-month TSV "
        "payloads (UTF-16 LE), one row per (sample, analyte) test detail. "
        "Cannabinoids, terpenes, pesticides, heavy metals, mycotoxins, "
        "microbial, moisture, water activity. 9,228 packages in June 2026."
    ),
    large=True,
    required_columns=LAB_COLUMNS,
    column_types={"Test Performed Date": "date", "Quantity": "number"},
    key_columns=["Lab Test Detail Id"],
))

_define(DatasetDef(
    slug="safety_bulletins",
    title="CCB Public Health and Safety Bulletins (WP-JSON)",
    url=REGULATOR["advisories_api"],
    format="json",
    reporting_period="2020-11 .. present",
    source_last_updated="as issued by the Board",
    description=(
        "Machine-readable advisory posts from the CCB WordPress REST API: "
        "bulletin id/date/title, affected items (product, batch/lot), "
        "retail locations, consumer guidance, and the CDC/agency links the "
        "Board cites. Street addresses inside retail-location lines are "
        "excluded from generated pages."
    ),
    required_columns=[],          # WP-JSON; shape-checked in the parser
    disclaimer=BULLETIN_DISCLAIMER,
))

# ---------------------------------------------------------------------------
# Privacy policy
# ---------------------------------------------------------------------------

PRIVACY_SPEC = PrivacySpec(
    state="nevada",
    entity_allowlists={
        "testing_laboratory": [
            "legal_name", "jurisdiction", "related_jurisdiction",
            "test_rows_seen", "packages_tested", "samples_tested",
        ],
        "dataset": [
            "title", "slug", "official_source_url", "format",
            "reporting_period", "source_last_updated", "retrieval_date",
            "row_count", "disclaimer", "clarification",
        ],
        "safety_advisory": [
            "title", "bulletin_date", "canonical_url", "concern",
            "consumer_instructions", "affected_items", "retail_locations",
        ],
        "lab_report": [
            "title", "report_date", "laboratory", "batch_id", "metrc_tag",
            "sample_type", "matrix_detail", "package_quantity",
            "measurement_count", "overall_passed", "official_source_url",
        ],
        "organization": [
            "legal_name", "jurisdiction", "packages_tested",
        ],
    },
)

# ---------------------------------------------------------------------------
# TSV payload reader (UTF-16 with BOM, tab-separated, inside ZIP)
# ---------------------------------------------------------------------------


def sniff_encoding_and_delimiter(data: bytes) -> tuple:
    """Return ``(encoding, delimiter)`` for a CCB lab payload.

    CCB monthly ZIPs carry UTF-16 LE TSV payloads. Older or corrected
    exports could be UTF-8 CSV; the reader detects rather than assumes.
    """
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return "utf-16", "\t"
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", ","
    head = data[:4096].decode("utf-8", errors="replace")
    if head.count("\t") > head.count(","):
        return "utf-8", "\t"
    return "utf-8", ","


def iter_lab_rows(zip_path: Path, *, max_uncompressed_bytes: int = 2 << 30) -> Iterator[dict]:
    """Stream rows from every CSV member of a CCB monthly ZIP.

    Values are stripped; empty strings become ``None``. Raises
    :class:`IngestError` on decode failure, an empty archive, or a member
    whose decompressed size exceeds ``max_uncompressed_bytes`` (zip-bomb
    guard; the real June 2026 extract decompresses to ~110 MB per member,
    so the 2 GiB default leaves two orders of magnitude of headroom).
    The ``_csv_member`` provenance field records which half-month payload
    each row came from.
    """
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as error:
        raise IngestError(f"cannot open lab-library ZIP {zip_path}: {error}") from error
    with archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise IngestError(f"{zip_path}: no CSV payload inside lab-library ZIP")
        for name in sorted(names):
            info = archive.getinfo(name)
            if info.file_size > max_uncompressed_bytes:
                raise IngestError(
                    f"{zip_path}#{name}: decompressed size {info.file_size:,} bytes "
                    f"exceeds the {max_uncompressed_bytes:,}-byte cap; refusing "
                    "to inflate (zip-bomb guard)"
                )
            data = archive.read(name)
            encoding, delimiter = sniff_encoding_and_delimiter(data)
            try:
                text = data.decode(encoding, errors="strict")
            except UnicodeDecodeError as error:
                raise IngestError(
                    f"{zip_path}#{name}: decoding failed ({encoding}): {error}"
                ) from error
            if text.startswith("\ufeff"):
                text = text[1:]
            lines = text.splitlines()
            if not lines:
                raise IngestError(f"{zip_path}#{name}: empty payload")
            header = [c.strip() for c in lines[0].split(delimiter)]
            for number, line in enumerate(lines[1:], start=2):
                if not line.strip():
                    continue
                cells = line.split(delimiter)
                row = {}
                for index, column in enumerate(header):
                    value = cells[index].strip() if index < len(cells) else ""
                    row[column] = value if value else None
                row["_csv_member"] = name
                yield row


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _num(value: Any) -> Optional[float]:
    text = _clean(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

_TEST_TYPE_RE = re.compile(r"^(.+?)\s*\(([^)]+)\)\s+(.+)$")


def parse_test_type(source: str) -> dict:
    """Split ``Arsenic (ppm) Raw Plant Material`` deterministically.

    Returns ``{test_type, unit, matrix}``. Without a ``(unit)`` segment the
    whole string stays as ``test_type`` with empty unit/matrix — the
    component split is never guessed. Same grammar as the Massachusetts
    ``parse_analyte`` (the Metrc test-type namespace is shared).
    """
    text = _clean(source)
    match = _TEST_TYPE_RE.match(text)
    if match:
        return {"test_type": match.group(1).strip(),
                "unit": match.group(2).strip(),
                "matrix": match.group(3).strip()}
    return {"test_type": text, "unit": "", "matrix": ""}


def normalize_lab_row(row: dict) -> dict:
    """Normalize one lab-library row into the archive's working shape."""
    parsed_test_date = parse_date(row.get("Test Performed Date"))
    parsed = parse_test_type(row.get("Test Type Name"))
    return {
        "packaged_by": _clean(row.get("Packaged By Facility Name")),
        "lab": _clean(row.get("Testing Facility Name")),
        "test_date": _clean(row.get("Test Performed Date")),
        "test_date_iso": parsed_test_date.isoformat() if parsed_test_date else "",
        "sample_id": _clean(row.get("PackageLabSampleId")),
        "overall_passed": _clean(row.get("Overall Passed")),
        "is_finished": _clean(row.get("Is Finished")),
        "remediated": _clean(row.get("Contains Remediated Product")),
        "product_category": _clean(row.get("Product Category Type Name")),
        "product_name": _clean(row.get("Product Name")),
        "package_label": _clean(row.get("PackageLabel")),
        "quantity": _clean(row.get("Quantity")),
        "quantity_numeric": _num(row.get("Quantity")),
        "quantity_note": (
            "source prints Quantity=0 (metrc zero-quantity convention for "
            "non-inventoried samples); preserved verbatim, never treated as "
            "missing"
            if _num(row.get("Quantity")) == 0.0 else ""
        ),
        "uom": _clean(row.get("Unit Of Measure Abbreviation")),
        "test_type_name": _clean(row.get("Test Type Name")),
        "test_type": parsed["test_type"],
        "test_unit": parsed["unit"],
        "test_matrix": parsed["matrix"],
        "test_passed": _clean(row.get("Test Passed")),
        "lab_test_detail_id": _clean(row.get("Lab Test Detail Id")),
        "result_level": _clean(row.get("Test Result Level")),
        "result_numeric": _num(row.get("Test Result Level")),
        "csv_member": _clean(row.get("_csv_member")),
    }


# ---------------------------------------------------------------------------
# WP-JSON bulletin parsing
# ---------------------------------------------------------------------------


def _strip_tags(markup: str) -> str:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", markup, flags=re.S)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", text).strip()


_FACILITY_RE = re.compile(
    r"([A-Z0-9][A-Za-z0-9&'., /-]+?(?:\s+(?:LLC|L\.L\.C\.|Inc\.?|LC|L\.C\.|Co\.|Corp\.?|Company)))"
    r"(?:\s+DBA\s+([A-Za-z0-9&'., /-]+?))?"
    r"\s+([A-Z][A-Za-z .'-]+?)\s+\(License\s*#\s*:?\s*([0-9 ]+)\)",
    re.IGNORECASE,
)
_LICENSE_LINE_RE = re.compile(
    r"^\s*(.+?)\s+\(License\s*#\s*:?\s*([0-9 ]+)\)\s*$"
)
_CITY_STATE_RE = re.compile(
    r"([A-Z][A-Za-z .'-]+?),?\s+NV\s+\d{5}(?:-\d{4})?\b"
)
_BATCH_RE = re.compile(r"\b(\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4})\b")
_BATCH_RUN_RE = re.compile(r"\b([A-Z]{1,4}[A-Z0-9]*\s?\d{3,6})\b")
_SOLD_BETWEEN_RE = re.compile(
    r"between\s+([A-Z][a-z]+ \d{1,2}, \d{4})\s*[–—-]\s*([A-Z][a-z]+ \d{1,2}, \d{4})",
)
_CONCERN_RE = re.compile(r"Health impacts from ([^.]+?) may exist", re.IGNORECASE)


def _cell_text(markup: str) -> str:
    return _clean(_html.unescape(re.sub(r"<[^>]+>", " ", markup)))


def _strip_tags(markup: str) -> str:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", markup, flags=re.S)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", text).strip()


def _parse_item_tables(html_markup: str) -> list:
    """Extract affected items from the bulletin's HTML tables.

    CCB bulletins embed a small table whose header row names the columns
    (``Item | Batch/ Lot``, ``Product Name | Production Run Number``, …).
    The first column is always the product; the second is the batch/lot or
    production-run identifier. Header rows are skipped; a batch-shaped or
    run-shaped token in the second cell is captured verbatim.
    """
    items: list = []
    for table_match in re.finditer(r"<table.*?</table>", html_markup, flags=re.S):
        table = table_match.group(0)
        rows = re.findall(r"<tr.*?</tr>", table, flags=re.S)
        parsed_rows = []
        for row in rows:
            cells = [_cell_text(c) for c in
                     re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
            cells = [c for c in cells if c and c != "\xa0"]
            if cells:
                parsed_rows.append(cells)
        if not parsed_rows:
            continue
        header = " ".join(parsed_rows[0]).lower()
        if not any(k in header for k in ("item", "batch", "lot", "product",
                                         "production run")):
            continue
        for cells in parsed_rows[1:]:
            product = cells[0] if cells else ""
            identifier = cells[1] if len(cells) > 1 else ""
            batch = _BATCH_RE.search(identifier) or _BATCH_RUN_RE.search(identifier)
            items.append({
                "product_text": product[:160],
                "batch_lot": batch.group(1) if batch else identifier[:60],
            })
    return items


def _parse_retail_locations(html_markup: str, text: str) -> tuple:
    """Extract retail facilities: ``(rows, warnings)``.

    Retail entries are list items of the form ``LEGAL NAME LLC dba Trade
    Name City (License #: N…), street address, City, NV ZIP``. Only the
    facility name, city, and license number are kept — the street address
    is never captured. Address cuts are digit-anchored (a house number must
    precede the street suffix) so business names like "Circle S Farms"
    are never mistaken for addresses. Any license line whose facility name
    is consumed by cutting is returned as a loud warning, never silently
    dropped (gate M2).
    """
    retail: list = []
    retail_warnings: list = []
    seen: set = set()
    entries: list = []
    # Prefer structured list items; fall back to license-bearing fragments
    # of the flattened text.
    for li in re.findall(r"<li[^>]*>(.*?)</li>", html_markup, flags=re.S):
        entries.append(_strip_tags(li))
    if not entries:
        for match in re.finditer(r"[^.]*?\(License\s*#\s*:?\s*[0-9 ]+\)[^.]*\.", text):
            entries.append(match.group(0))
    for entry in entries:
        # Retail license tokens come in two shapes and either case:
        # labeled "(License #: N…)" / "(license # N…)" and bare trailing
        # "(N…)" / "(TRN…)" tokens (verified: bulletin 2021-2 uses lowercase
        # "license #", bulletin 2022-01 carries unlabeled tokens, and its
        # NuWu line has no license at all). A license-less line that still
        # looks like a retail address line ships with an empty license and
        # a loud warning — never a silent drop (gate M2 class).
        license_match = re.search(
            r"\(license\s*#\s*:?\s*([0-9 ]+)\)|\(([A-Z]{0,2}\d{15,})\)",
            entry, re.IGNORECASE)
        looks_like_retail = bool(re.search(r",\s*[A-Z][A-Za-z .'-]+?,?\s+NV\s+\d{5}", entry))
        if not license_match:
            if not looks_like_retail:
                continue
            license_number = ""
            head = entry
            retail_warnings.append(
                f"retail line without a license token: {entry[:100]}")
        else:
            raw_number = license_match.group(1) or license_match.group(2)
            license_number = re.sub(r"\s+", "", raw_number)
            head = entry[:license_match.start()].strip().rstrip(",")
        # License-bearing fallback fragments often start mid-sentence ("was
        # sold at the following … facility between …"); the facility clause
        # starts after that boilerplate.
        boilerplate = re.search(
            r"(?:was|were) sold at the following[^:]*?:\s*|"
            r"(?:was|were) sold at the following[^.]*?facility\s+between[^:]*?\d\s*", head)
        if boilerplate:
            head = head[boilerplate.end():].strip()
        dba = ""
        dba_match = re.search(r"\s+DBA\s+(.+)$", head, flags=re.IGNORECASE)
        if dba_match:
            dba = _clean(dba_match.group(1))
            head = head[:dba_match.start()].strip()
        # A dba tail that runs into a street address keeps only the trade
        # name. Cut candidates, earliest match wins:
        #   1. a comma followed by a house number (", 2900 …")
        #   2. a street-suffix marker preceded by a house number
        #      ("2900 E Desert Inn Rd") — the digit anchor is what separates
        #      a street address from a business name like "Circle S Farms"
        #      or "Dr Greenthumb"; suffix-only matching silently dropped
        #      real facilities (gate M2)
        #   3. a ", City, NV ZIP" clause
        cuts = [
            re.search(r",\s*\d", dba),
            re.search(r"\d\s*(?:[A-Z]\s*)?(?:#\d+|Suite|Ste|Blvd|Ave|Rd|Dr|Pkwy|Cir|Circle|Ln|St)\b", dba, re.I),
            re.search(r",\s*[A-Z][A-Za-z .'-]+?,?\s+NV\s+\d{5}", dba),
        ]
        cuts = [c for c in cuts if c]
        if cuts:
            dba = _clean(dba[:min(c.start() for c in cuts)])
        # Same digit-anchored discipline for the facility head.
        head_cuts = [
            re.search(r",\s*\d", head),
            re.search(r"\d\s*(?:[A-Z]\s*)?(?:#\d+|Suite|Ste|Blvd|Ave|Rd|Dr|Pkwy|Cir|Circle|Ln|St)\b", head, re.I),
        ]
        head_cuts = [c for c in head_cuts if c]
        if head_cuts:
            head = _clean(head[:min(c.start() for c in head_cuts)])
        # City: prefer the address city (", City, NV ZIP"); drop the raw
        # state token when the address was malformed.
        city = ""
        city_match = _CITY_STATE_RE.search(entry)
        if city_match:
            city = _clean(city_match.group(1))
        if city.upper() == "NV":
            city = ""
        key = (head, license_number)
        if not head:
            # A license line whose facility name was consumed by address
            # cutting is a parser defect, not a data condition: surface it
            # loudly instead of silently dropping the facility (gate M2).
            retail_warnings.append(entry[:120])
            continue
        if key not in seen:
            seen.add(key)
            retail.append({"facility": head, "dba": dba, "city": city,
                           "license_number": license_number})
    return retail, retail_warnings


def parse_bulletin_content(text: str, html_markup: str = "") -> dict:
    """Extract structured fields from a bulletin.

    Affected items come from the HTML tables when available (WP-rendered
    posts keep real ``<table>`` markup); retail locations come from list
    items or sentences naming a ``License #``. The street address that
    follows the license clause in the source line is never captured.
    """
    items = _parse_item_tables(html_markup) if html_markup else []
    if not items:
        # Fallback: flattened-text anchor parsing (older exports).
        anchor = text.find("Batch/ Lot")
        if anchor < 0:
            anchor = text.find("Batch/Lot")
        if anchor >= 0:
            tail = text[anchor:]
            cut = re.search(r"was sold at|were sold at|sold at the following", tail)
            if cut:
                tail = tail[:cut.start()]
            for chunk in re.split(r"\s{3,}", tail)[1:]:
                chunk = chunk.strip()
                if not chunk:
                    continue
                batch = _BATCH_RE.search(chunk)
                product = (chunk[:batch.start()] if batch else chunk).strip()
                if product and len(product) < 160:
                    items.append({"product_text": product,
                                  "batch_lot": batch.group(1) if batch else ""})
    retail, retail_warnings = _parse_retail_locations(html_markup, text)
    sold = _SOLD_BETWEEN_RE.search(text)
    concern = _CONCERN_RE.search(text)
    instructions = ""
    index = text.lower().find("requested to display this bulletin")
    if index >= 0:
        end = text.find(".", index)
        instructions = text[index:end + 1] if end >= 0 else text[index:]
    return {
        "affected_items": items,
        "retail_locations": retail,
        "retail_location_warnings": retail_warnings,
        "sold_between": [sold.group(1), sold.group(2)] if sold else [],
        "concern": _clean(concern.group(1)) if concern else "",
        "consumer_instructions": instructions,
    }


def normalize_bulletin(post: dict) -> dict:
    """Normalize one WP-JSON advisory post into a bulletin record."""
    html_markup = post.get("content", {}).get("rendered", "")
    text = _strip_tags(html_markup)
    raw_title = _clean(_html.unescape(post.get("title", {}).get("rendered", "")))
    return {
        "bulletin_id": str(post.get("id", "")),
        "slug": _clean(post.get("slug", "")),
        "title": raw_title,
        "bulletin_date": (post.get("date") or "")[:10],
        "modified_date": (post.get("modified") or "")[:10],
        "canonical_url": _clean(post.get("link", "")),
        **parse_bulletin_content(text, html_markup),
    }


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def aggregate_lab(rows: list) -> dict:
    """Full-volume aggregates from normalized lab rows."""
    by_lab: dict = defaultdict(
        lambda: {"rows": 0, "packages": set(), "samples": set(), "passed": 0, "failed": 0})
    by_category = Counter()
    by_month = Counter()
    by_matrix = Counter()
    test_types = set()
    passed = failed = 0
    packages = set()
    samples = set()
    for row in rows:
        lab = row.get("lab") or "Unknown"
        entry = by_lab[lab]
        entry["rows"] += 1
        if row.get("package_label"):
            entry["packages"].add(row["package_label"])
            packages.add(row["package_label"])
        if row.get("sample_id"):
            entry["samples"].add(row["sample_id"])
            samples.add(row["sample_id"])
        status = (row.get("test_passed") or "").lower()
        if status == "true":
            passed += 1
            entry["passed"] += 1
        elif status == "false":
            failed += 1
            entry["failed"] += 1
        by_category[row.get("product_category") or "Unknown"] += 1
        month = (row.get("test_date_iso") or "")[:7]
        if month:
            by_month[month] += 1
        by_matrix[row.get("test_matrix") or "Unknown"] += 1
        if row.get("test_type"):
            test_types.add(row["test_type"])
    return {
        "rows": len(rows),
        "by_lab": {name: {"rows": v["rows"],
                          "packages": len(v["packages"]),
                          "samples": len(v["samples"]),
                          "passed": v["passed"], "failed": v["failed"]}
                   for name, v in by_lab.items()},
        "by_category": dict(by_category.most_common()),
        "by_month": dict(sorted(by_month.items())),
        "by_matrix": dict(by_matrix.most_common()),
        "test_types": sorted(test_types),
        "packages": len(packages),
        "samples": len(samples),
        "passed": passed,
        "failed": failed,
    }


def aggregate_bulletins(posts: list) -> dict:
    by_year = Counter()
    with_items = 0
    for post in posts:
        by_year[(post.get("bulletin_date") or "")[:4] or "unknown"] += 1
        if post.get("affected_items"):
            with_items += 1
    return {"bulletins": len(posts), "by_year": dict(sorted(by_year.items())),
            "with_items": with_items}


# ---------------------------------------------------------------------------
# COA model bridge
# ---------------------------------------------------------------------------


def report_natural_key(row: dict) -> str:
    """Report natural key: ``(sample id, test date)`` — retests get distinct reports."""
    return f"nv-ccb:{row['sample_id']}:{row['test_date_iso']}"


def batch_natural_key(row: dict) -> str:
    """Batch natural key: the Metrc package tag."""
    return f"nv-ccb:pkg:{row['package_label']}"


SAMPLE_TYPE_BY_CATEGORY = {
    "Buds": "flower", "ShakeTrim": "trim", "Concentrate": "extract",
    "InfusedEdible": "edible", "InfusedNonEdible": "infused", "Plants": "flower",
    "Other": "unknown",
}


def sample_type_for(category: str) -> str:
    return SAMPLE_TYPE_BY_CATEGORY.get(_clean(category), "unknown")


# Unit tokens that appear in CCB test-type names, mapped to the COA model's
# canonical units. Anything unmapped stays "other" (never a silent loss).
CANONICAL_UNIT_MAP = {
    "%": "% w/w", "ppm": "ppm", "ppb": "ppb", "mg/g": "mg/g",
    "ug/g": "ug/g", "CFU/g": "CFU/g", "CFU/mL": "CFU/mL",
    "mg/package": "other", "mg/serving": "other", "Aw": "other", "pH": "other",
}


def canonical_unit(unit: str) -> str:
    key = _clean(unit)
    if not key:
        return "other"
    return CANONICAL_UNIT_MAP.get(key, "other")


# Tests that are report-derived totals, not independent compounds: they keep
# their printed name and never receive a compound_id.
CALCULATED_TESTS = {
    "Total THC", "Total CBD", "Total Cannabinoids", "Total Potential THC",
    "Total Terpenes", "Total Yeast and Mold", "Total Coliforms",
    "Total Enterobacteriaceae", "Total Viable Aerobic Bacteria",
}

# Canonical compound ids that exist in the archive today (content pages are
# the allocation authority; anything else stays unmapped by design).
_KNOWN_COMPOUND_IDS = {
    "Lead": "contaminants/TCNT-0007",
}


def group_rows_to_reports(rows: list) -> list:
    """Group normalized lab rows into report-shaped dicts (COA bridge input).

    Groups on the report natural key ``(sample id, test date)``. Package-
    level metadata disagreements inside a group are collected as conflict
    strings (retest splits, source corrections) — reported, never guessed
    away. Reports are returned sorted by report key for determinism.
    """
    groups: dict = defaultdict(list)
    for row in rows:
        groups[report_natural_key(row)].append(row)
    reports = []
    for key in sorted(groups):
        members = groups[key]
        first = members[0]
        conflicts = []
        for name in ("lab", "package_label", "product_category", "product_name",
                     "packaged_by", "quantity", "uom", "overall_passed"):
            values = {m.get(name, "") for m in members}
            if len(values) > 1:
                conflicts.append(f"{name}: {' | '.join(sorted(values))}")
        reports.append({
            "report_key": key,
            "sample_id": first["sample_id"],
            "test_date": first["test_date_iso"],
            "lab": first["lab"],
            "package_label": first["package_label"],
            "product_category": first["product_category"],
            "product_name": first["product_name"],
            "packaged_by": first["packaged_by"],
            "package_quantity": _clean(f'{first["quantity"]} {first["uom"]}'),
            "overall_passed": first["overall_passed"],
            "rows": len(members),
            "conflicts": conflicts,
            "measurements": members,
        })
    return reports


def rows_to_coa_measurements(rows: list) -> list:
    """Map normalized CCB rows to COA-model measurement dicts.

    Uses ``scripts.coa_model.decode_result`` so censoring semantics (zero,
    nd, missing, invalid) follow the archive's COA rules exactly. The COA
    model requires analyte uniqueness within a report keyed on
    ``compound_id or compound_name``; CCB data legitimately repeats a test
    name across matrix variants (verified June 2026: ``Total Cannabinoids``
    under both ``Whole Wet Plant`` and ``Whole Wet Plants`` with distinct
    ``Lab Test Detail Id``s). Such repeats are disambiguated by appending
    the matrix to the compound name, with the verbatim name and both
    provenance ids preserved in the quantitation note — never dropped or
    merged.
    """
    from scripts.coa_model import decode_result

    # First pass: decode everything.
    decoded = []
    for row in rows:
        state, value, note = decode_result(row.get("result_level"))
        decoded.append((row, state, value, note))

    # Detect test names that repeat within this report group.
    name_counts = Counter(row["test_type"] for row, *_ in decoded)

    out = []
    for row, state, value, note in decoded:
        unit = canonical_unit(row.get("test_unit"))
        is_calculated = row.get("test_type") in CALCULATED_TESTS
        compound_name = row["test_type"]
        notes = []
        if note:
            notes.append(note)
        if name_counts[row["test_type"]] > 1:
            # Same test name appears under more than one matrix/unit: keep
            # every row, disambiguated by matrix so the COA model's
            # per-report uniqueness rule holds.
            suffix = row.get("test_matrix") or row.get("test_unit") or "variant"
            compound_name = f"{row['test_type']} [{suffix}]"
            notes.append(
                f"name repeated in source under matrix {row.get('test_matrix', '')!r} "
                f"(detail id {row.get('lab_test_detail_id', '')}); disambiguated "
                "by matrix, values preserved verbatim"
            )
        out.append({
            "compound_name": compound_name,
            "compound_id": None if is_calculated else _KNOWN_COMPOUND_IDS.get(row["test_type"]),
            "reported_value": row.get("result_level") or "",
            "reported_unit": row.get("test_unit"),
            "state": state.value,
            "value": value,
            "unit": unit,
            "test_date": row.get("test_date_iso") or None,
            "quantitation_note": "; ".join(notes) if notes else None,
            "calculation_formula": ("report-derived total" if is_calculated else None),
            "test_passed": row.get("test_passed"),
        })
    return out


def report_to_coa_record(report: dict, *, report_id: str, record_kind: str,
                         provenance: dict, sample_type: Optional[str] = None,
                         producer_id: Optional[str] = None,
                         laboratory_lab_id: Optional[str] = None) -> dict:
    """Build one durable COA record dict from a grouped report.

    The output is the ``coa-measurement.schema.json`` shape; the caller
    validates it through :mod:`scripts.coa_model` (``CoaRecord`` /
    ``coa_problems``) before it may be written to the durable registry.
    """
    measurements = rows_to_coa_measurements(report["measurements"])
    lab_name = report.get("lab") or ""
    batch_id = report.get("package_label") or report.get("sample_id") or ""
    return {
        "schema_version": "1.0",
        "report": {
            "report_id": report_id,
            "revision": 1,
            "supersedes": None,
            "source_reference": (
                f"CCB Lab Library June 2026 extract; sample {report['sample_id']}"
            ),
            "report_date": report.get("test_date") or None,
            "test_date": report.get("test_date") or None,
            "sample_date": None,
            "sample_id": report.get("sample_id"),
            "laboratory": (
                {"name": lab_name, "lab_id": laboratory_lab_id, "jurisdiction": "NV"}
                if lab_name else None
            ),
            "jurisdiction": "NV",
            "license_references": [],
            "test_panels": sorted({m["test_matrix"] for m in report["measurements"]
                                   if m.get("test_matrix")}),
            "provenance": provenance,
            "method": None,
        },
        "batch": {
            "batch_id": batch_id,
            "metrc_tag": report.get("package_label") or "",
            "lot_number": "",
            "producer_id": producer_id,
            "product_id": None,
            "cultivar_labels": [report["product_name"]] if report.get("product_name") else [],
            "cultivar_claims": [],
            "sample_type": sample_type or sample_type_for(report.get("product_category")),
            "matrix_detail": report.get("product_category") or "",
            "basis": "unknown",
            "decarb_convention": "native",
            "record_kind": record_kind,
            "jurisdiction": "NV",
            "harvest_date": None,
            "production_date": None,
            "package_date": None,
        },
        "measurements": measurements,
    }


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------


def _retrieval_note(store: ArtifactStore, slug: str) -> str:
    latest = store.latest_snapshot(slug)
    return (latest or {}).get("retrieval_timestamp", "not yet ingested")


def _row_count_note(store: ArtifactStore, slug: str) -> str:
    latest = store.latest_snapshot(slug)
    return str((latest or {}).get("row_count", "—"))


class NevadaSync:
    """Runs the Nevada CCB ingestion pipeline."""

    def __init__(self, *, fetch, store: ArtifactStore, registry: NaturalKeyRegistry,
                 content_root: Path, datasets: Optional[list] = None,
                 refresh: bool = False, fixtures_only: bool = False,
                 allow_fixture_content: bool = False,
                 verified_batch_max: Optional[int] = None):
        self.fetch = fetch
        self.store = store
        self.registry = registry
        self.content_root = content_root
        self.datasets = datasets
        self.refresh = refresh
        self.fixtures_only = fixtures_only
        self.allow_fixture_content = allow_fixture_content
        self.verified_batch_max = (verified_batch_max
                                   if verified_batch_max is not None
                                   else PAGE_POLICY["verified_batch_max"])
        self.aggregates: dict = {}
        self.normalized: dict = {}
        self.lab_reports: list = []
        self.bulletins: list = []

    # ------------------------------------------------------------ dataset run
    def run_dataset(self, slug: str, report: ChangeReport) -> DatasetRun:
        if self.fixtures_only and not self.allow_fixture_content:
            # Same hard guard as Massachusetts: fixture/synthetic records must
            # never populate the durable manifest.
            raise IngestError(
                "fixture-only mode must not record snapshots or generate "
                "content; supply an explicit development flag "
                "(--allow-fixture-content) or ingest live official sources"
            )
        spec = DATASETS[slug]
        run = DatasetRun(slug=slug)
        try:
            if spec.format == "zip":
                self._run_lab_dataset(spec, run, report)
            elif spec.format == "json":
                self._run_bulletin_dataset(spec, run, report)
            else:
                raise IngestError(f"{slug}: unsupported format {spec.format}")
        except IngestError as error:
            run.status = "error"
            run.message = str(error)
            report.errors.append(f"{slug}: {error}")
        report.datasets[slug] = run.to_dict()
        return run

    def _run_lab_dataset(self, spec: DatasetDef, run: DatasetRun,
                         report: ChangeReport) -> None:
        import shutil

        url = spec.url
        tmp = self.store.working_root / "tmp" / f"{spec.slug}.zip"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        result = self.fetch.download(url, tmp)
        raw_sha = result.sha256

        raw_path = self.store.raw_snapshot_path(slug=spec.slug, sha256=raw_sha, ext=".zip")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.is_file():
            # Same checksum as an existing immutable snapshot: reuse it.
            if result.path is not None and result.path.resolve() != raw_path.resolve():
                result.path.unlink(missing_ok=True)
            run.status = "unchanged"
        elif result.path is not None:
            shutil.move(str(result.path), str(raw_path))
            run.status = "fetched"
        else:
            raw_path.write_bytes(result.data)
            run.status = "fetched"

        rows = [normalize_lab_row(row) for row in iter_lab_rows(raw_path)]
        warnings = self._guards(spec, rows, report)
        run.row_count = len(rows)

        normalized_path = self._write_normalized(spec.slug, rows)
        normalized_sha = sha256_file(normalized_path)
        self.aggregates[spec.slug] = aggregate_lab(rows)
        self.normalized[spec.slug] = rows
        self.lab_reports = group_rows_to_reports(rows)

        prior = self.store.latest_snapshot(spec.slug)
        prior_count = (prior or {}).get("row_count")
        if prior_count is None or prior_count == len(rows):
            run.change = "first snapshot" if prior_count is None else "no change"
        else:
            run.change = f"row count {prior_count} -> {len(rows)}"

        if run.status == "fetched":
            source_updated = result.last_modified or spec.source_last_updated
            warnings.extend(check_source_staleness(
                (prior or {}).get("source_last_updated"), source_updated,
                has_clarification=bool(spec.clarification),
            ))
            self.store.record_snapshot(
                spec.slug, url,
                raw_sha256=raw_sha,
                raw_path=raw_path,
                content_type=result.content_type,
                size_bytes=result.size_bytes,
                retrieved_at=utc_now(),
                reporting_period=spec.reporting_period,
                source_last_updated=source_updated,
                disclaimer=spec.disclaimer,
                clarification=spec.clarification,
                row_count=len(rows),
                columns=LAB_COLUMNS,
                normalized_sha256=normalized_sha,
                normalized_path=normalized_path,
                max_reported_date=max(
                    (r["test_date_iso"] for r in rows if r.get("test_date_iso")),
                    default=""),
            )
            run.normalized_sha256 = normalized_sha
        run.raw_sha256 = raw_sha
        for warning in warnings:
            report.warnings.append(f"{spec.slug}: {warning}")

    def _run_bulletin_dataset(self, spec: DatasetDef, run: DatasetRun,
                              report: ChangeReport) -> None:
        payload = self.fetch.fetch_bytes(spec.url)
        try:
            posts = json.loads(payload.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise IngestError(f"{spec.slug}: WP-JSON decode failed: {error}") from error
        if not isinstance(posts, list):
            raise IngestError(f"{spec.slug}: expected a WP-JSON post array")
        bulletins = [normalize_bulletin(post) for post in posts]
        self.bulletins = bulletins
        self.aggregates[spec.slug] = aggregate_bulletins(bulletins)
        self.normalized[spec.slug] = bulletins
        run.row_count = len(bulletins)
        run.status = "fetched"
        run.raw_sha256 = payload.sha256
        run.change = f"{len(bulletins)} bulletins parsed"

    def _guards(self, spec: DatasetDef, rows: list, report: ChangeReport) -> list:
        warnings: list = []
        spec.check_headers(LAB_COLUMNS)
        # Type checks run on the normalized copies of the typed columns.
        typed = [{"Test Performed Date": r["test_date"], "Quantity": r["quantity"]}
                 for r in rows]
        warnings.extend(spec.schema_spec().check_types(typed))
        # Lab Test Detail Id is a true primary key (verified June 2026).
        warnings.extend(check_duplicate_keys(
            rows, ["lab_test_detail_id"], spec.slug, policy="fail",
        ))
        prior = self.store.latest_snapshot(spec.slug) or {}
        warnings.extend(check_row_collapse(
            spec.schema_spec(), len(rows), prior.get("row_count")))
        new_max = max((r["test_date_iso"] for r in rows if r.get("test_date_iso")),
                      default="")
        warnings.extend(check_date_regression(
            prior.get("max_reported_date"), new_max,
            has_clarification=bool(spec.clarification),
        ))
        return warnings

    def _write_normalized(self, slug: str, rows: list) -> Path:
        import csv as _csv
        import hashlib

        columns = list(rows[0].keys()) if rows else []
        buffer = io.StringIO()
        writer = _csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        digest = hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()
        return self.store.write_normalized(slug, digest, rows)

    # --------------------------------------------------------- content generation
    def generate_content(self, report: ChangeReport) -> list:
        if self.fixtures_only and not self.allow_fixture_content:
            raise IngestError(
                "fixture-only mode must not generate publishable content; "
                "supply an explicit development flag (--allow-fixture-content) "
                "or ingest from live official sources"
            )
        pages: list = []
        self._preallocate_ids()
        pages += self._write_organization_pages()
        pages += self._write_dataset_pages()
        pages += self._write_lab_pages()
        pages += self._write_advisory_pages()
        pages.append(self._write_privacy_spec_page())
        self._write_durable_artifacts()
        report.pages_generated = list(dict.fromkeys(pages))
        return pages

    def _preallocate_ids(self) -> None:
        aggr = self.aggregates.get("lab_library_2026_06", {})
        for lab in sorted(aggr.get("by_lab", {})):
            if lab != "Unknown":
                self._entity_id("testing_laboratory", f"NV:lab:{lab}", label=lab)
        self._entity_id("dataset", "NV:dataset:lab_library_2026_06",
                        label=DATASETS["lab_library_2026_06"].title)
        self._entity_id("dataset", "NV:dataset:safety_bulletins",
                        label=DATASETS["safety_bulletins"].title)
        for bulletin in self.bulletins:
            self._entity_id("safety_advisory",
                            f"NV:adv:{bulletin['bulletin_id']}",
                            label=bulletin["title"])
        # Producers of the bounded verified batch get organization records
        # (COA-05: every lab-results page needs a batch-traceable relation).
        for rpt in self.lab_reports[:self.verified_batch_max]:
            if rpt.get("packaged_by"):
                self._entity_id("organization", f"NV:org:{rpt['packaged_by']}",
                                label=rpt["packaged_by"])
        for rpt in self.lab_reports[:self.verified_batch_max]:
            self._entity_id("lab_report", rpt["report_key"], label=rpt["report_key"])

    def _entity_id(self, entity_type: str, natural_key: str, label: str = "") -> str:
        return self.registry.id_for(entity_type, natural_key, label=label)

    def _write_page(self, rel_path: str, *, entity_id: str, title: str,
                    parent: Optional[str], tags: list, relations: list,
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
                         status="published", tags=tags, relations=relations)
        path.write_text(fm + "\n\n" + body + "\n", encoding="utf-8")
        return rel_path

    # ---------------------------------------------------------------- datasets
    def _write_dataset_pages(self) -> list:
        return [self._write_dataset_record_page(slug)
                for slug in ("lab_library_2026_06", "safety_bulletins")]

    def _write_dataset_record_page(self, slug: str) -> str:
        spec = DATASETS[slug]
        entity = self._entity_id("dataset", f"NV:dataset:{slug}", label=spec.title)
        rel = f"datasets/{entity.rsplit('/', 1)[-1]}.md"
        aggr = self.aggregates.get(slug, {})
        body = [h1(spec.title), ""]
        body.append(callout("info", spec.disclaimer))
        body += ["", "## Source Record", ""]
        rows = [
            ["Official source", mdlink(spec.url, "CCB Lab Library ZIP"
                                       if spec.format == "zip" else "WP-JSON API")],
            ["Format", spec.format],
            ["Reporting period", spec.reporting_period],
            ["Source last updated", spec.source_last_updated],
            ["Retrieval", _retrieval_note(self.store, slug)],
            ["Rows (latest snapshot)", _row_count_note(self.store, slug)],
            ["Clarification / correction", spec.clarification or "—"],
        ]
        if slug == "lab_library_2026_06":
            rows += [
                ["Packages", str(aggr.get("packages", "—"))],
                ["Samples", str(aggr.get("samples", "—"))],
                ["Distinct test types", str(len(aggr.get("test_types", [])))],
                ["Payload encoding", "UTF-16 LE TSV inside ZIP (auto-detected)"],
            ]
        body.append(table(["Field", "Value"], rows))
        body += ["", "## Dataset Notes", ""]
        body.append(spec.description)
        if slug == "lab_library_2026_06" and aggr:
            body += ["", "## Aggregate Summary (June 2026)", ""]
            body.append(table(
                ["Laboratory", "Test rows", "Packages", "Samples", "Passed", "Failed"],
                [[escape_cell(name), str(v["rows"]), str(v["packages"]),
                  str(v["samples"]), str(v["passed"]), str(v["failed"])]
                 for name, v in sorted(aggr.get("by_lab", {}).items())]))
            body += ["", "## By Product Category", ""]
            body.append(table(["Category", "Test rows"],
                              [[k, str(v)] for k, v in aggr.get("by_category", {}).items()]))
        if slug == "safety_bulletins" and aggr:
            body += ["", "## Bulletin Coverage", ""]
            body.append(table(["Year", "Bulletins"],
                              [[k, str(v)] for k, v in aggr.get("by_year", {}).items()]))
        self._write_page(rel, entity_id=entity, title=spec.title,
                         parent="datasets", tags=["dataset", "nevada", slug],
                         relations=[JURISDICTION_ENTITY], body="\n".join(body))
        return rel

    # -------------------------------------------------------------------- labs
    def _write_lab_pages(self) -> list:
        pages = []
        aggr = self.aggregates.get("lab_library_2026_06", {})
        for name in sorted(aggr.get("by_lab", {})):
            if name == "Unknown":
                continue
            entity = self._entity_id("testing_laboratory", f"NV:lab:{name}", label=name)
            rel = f"testing-laboratories/{entity.rsplit('/', 1)[-1]}.md"
            stats = aggr["by_lab"][name]
            body = [h1(name), "",
                    "Nevada testing laboratory appearing in the CCB Lab "
                    "Library extracts.", "",
                    "## Approved Public Fields", ""]
            body.append(table(["Field", "Value"], [
                ["Licensed name", escape_cell(name)],
                ["Jurisdiction", "Nevada"],
                ["Source", mdlink(DATASETS["lab_library_2026_06"].url,
                                  "CCB Lab Library June 2026")],
                ["Test rows (June 2026)", str(stats["rows"])],
                ["Packages tested", str(stats["packages"])],
                ["Samples tested", str(stats["samples"])],
                ["Test rows passed / failed", f"{stats['passed']} / {stats['failed']}"],
            ]))
            body += ["", callout("warning",
                "This page does not rank or grade laboratory performance. "
                "Counts derive from one monthly Lab Library extract; a "
                "single result implies nothing about consumer safety without "
                "the applicable requirement, unit, matrix, and action "
                "limit."), ""]
            self._write_page(rel, entity_id=entity, title=name,
                             parent="testing-laboratories",
                             tags=["testing-laboratory", "nevada", "ccb"],
                             relations=[JURISDICTION_ENTITY], body="\n".join(body))
            pages.append(rel)
        return pages

    # ----------------------------------------------------------- organizations
    def _write_organization_pages(self) -> list:
        """Producer organizations for the bounded verified batch only."""
        pages = []
        seen: set = set()
        by_producer: dict = defaultdict(lambda: {"packages": set(), "rows": 0})
        for rpt in self.lab_reports[:self.verified_batch_max]:
            name = (rpt.get("packaged_by") or "").strip()
            if not name:
                continue
            by_producer[name]["packages"].add(rpt["package_label"])
            by_producer[name]["rows"] += rpt["rows"]
        for name in sorted(by_producer):
            if name in seen:
                continue
            seen.add(name)
            entity = self._entity_id("organization", f"NV:org:{name}", label=name)
            rel = f"organizations/{entity.rsplit('/', 1)[-1]}.md"
            stats = by_producer[name]
            body = [h1(name), "",
                    "Nevada licensed entity identified as the packaging "
                    "facility in CCB Lab Library testing records. No "
                    "lineage, ownership, or operational inference is made "
                    "beyond what the source states.", "",
                    "## Approved Public Fields", ""]
            body.append(table(["Field", "Value"], [
                ["Legal entity name", escape_cell(name)],
                ["Jurisdiction", "Nevada"],
                ["Source", mdlink(DATASETS["lab_library_2026_06"].url,
                                  "CCB Lab Library June 2026")],
                ["Packages in verified batch", str(len(stats["packages"]))],
            ]))
            self._write_page(rel, entity_id=entity, title=name,
                             parent="organizations",
                             tags=["organization", "nevada", "ccb"],
                             relations=[JURISDICTION_ENTITY], body="\n".join(body))
            pages.append(rel)
        return pages

    # -------------------------------------------------------------- advisories
    def _write_advisory_pages(self) -> list:
        pages = []
        for bulletin in self.bulletins:
            entity = self._entity_id("safety_advisory",
                                     f"NV:adv:{bulletin['bulletin_id']}",
                                     label=bulletin["title"])
            rel = f"safety-advisories/{entity.rsplit('/', 1)[-1]}.md"
            body = [h1(bulletin["title"]), "", "## Advisory Facts", ""]
            rows = [
                ["Bulletin date", bulletin.get("bulletin_date", "")],
                ["Canonical URL", mdlink(bulletin["canonical_url"], "official notice")]
                if bulletin.get("canonical_url") else ["Canonical URL", "—"],
                ["Concern", escape_cell(bulletin.get("concern", ""))],
                ["Affected items", str(len(bulletin.get("affected_items", [])))],
                ["Revision status", "as published by the Board"],
                ["Source provenance", "CCB WordPress REST API"],
            ]
            if bulletin.get("sold_between"):
                rows.append(["Sold between", " – ".join(bulletin["sold_between"])])
            body.append(table(["Field", "Value"], rows))
            items = bulletin.get("affected_items", [])
            if items:
                body += ["", "## Affected Items", ""]
                body.append(table(
                    ["Item", "Batch/Lot"],
                    [[escape_cell(i.get("product_text", "")),
                      escape_cell(i.get("batch_lot", ""))] for i in items]))
            retail = bulletin.get("retail_locations", [])
            if retail:
                body += ["", "## Retail Locations", ""]
                body.append(table(
                    ["Facility", "City", "License number"],
                    [[escape_cell(r.get("facility", "")), escape_cell(r.get("city", "")),
                      escape_cell(r.get("license_number", ""))] for r in retail]))
                body.append(
                    "\n_Only facility name, city, and license number are "
                    "published; street addresses from the official bulletin "
                    "are excluded._"
                )
            if bulletin.get("consumer_instructions"):
                body += ["", "## Posting Requirement", ""]
                body.append(callout("info", bulletin["consumer_instructions"]))
            body += ["", callout("info",
                "The Board uses **Public Health and Safety Bulletin** as the "
                "official term for these notices. This archive preserves that "
                "terminology and does not relabel the notice as a recall."), ""]
            self._write_page(rel, entity_id=entity, title=bulletin["title"],
                             parent="safety-advisories",
                             tags=["safety-advisory", "bulletin", "nevada", "ccb"],
                             relations=[JURISDICTION_ENTITY], body="\n".join(body))
            pages.append(rel)
        return pages

    # ------------------------------------------------------------ privacy spec
    def _write_privacy_spec_page(self) -> str:
        entity = self._entity_id(
            "reference", "NV:ref:privacy-spec",
            label="Nevada Ingestion: Privacy and Excluded-Field Specification",
        )
        rel = f"reference/{entity.rsplit('/', 1)[-1]}.md"
        body = [h1("Nevada Ingestion: Privacy and Excluded-Field Specification")]
        body.append(
            "Generated pages from Nevada official data publish only fields "
            "on the explicit allowlists below. The machine-readable "
            "specification is committed at `data/nevada-ccb/privacy-spec.md` "
            "and enforced by an automated scan of generated Markdown."
        )
        body += ["", "## Excluded Fields and Values", ""]
        body.append(table(
            ["Category", "Examples"],
            [["Street addresses (bulletin retail locations)",
              "full bulletin address lines"],
             ["Owner/officer names", "license-registry owner fields (not ingested)"],
             ["Contact fields", "emails, phones (not present in lab extracts)"],
             ["Coordinates", "latitude/longitude (not present in lab extracts)"],
             ["Fields present merely because they exist in source JSON",
              "all non-allowlisted source fields"]],
        ))
        body += ["", "## Entity Allowlists", ""]
        for entity_type, fields in sorted(PRIVACY_SPEC.entity_allowlists.items()):
            body.append(f"**{entity_type}**: `{'`, `'.join(fields)}`")
        self._write_page(rel, entity_id=entity,
                         title="Nevada Privacy and Excluded-Field Specification",
                         parent="reference",
                         tags=["privacy", "allowlist", "nevada", "ingest"],
                         relations=[], body="\n".join(body))
        return rel

    # ----------------------------------------------------------- durable artifacts
    def _write_durable_artifacts(self) -> None:
        # Full-volume aggregates (durable, small, derived)
        lab_aggr = self.aggregates.get("lab_library_2026_06", {})
        if lab_aggr:
            self.store.write_durable_json("lab-aggregates.json", lab_aggr)
        # Per-report index for the full month (working dir, never committed)
        if self.lab_reports:
            index_path = self.store.working_root / "reports-by-package.json"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump([{k: v for k, v in rpt.items() if k != "measurements"}
                           for rpt in self.lab_reports], handle, indent=1)
        # Privacy spec (durable)
        spec_lines = [
            "# Nevada CCB — Privacy and Excluded-Field Specification", "",
            f"State: {STATE}  ·  Generator: {self.store.importer_version}", "",
            "## Entity allowlists", "",
        ]
        for entity_type, fields in sorted(PRIVACY_SPEC.entity_allowlists.items()):
            spec_lines += [f"### {entity_type}", ""]
            spec_lines += [f"- `{f}`" for f in fields]
            spec_lines.append("")
        self.store.write_durable_markdown("privacy-spec.md", "\n".join(spec_lines))
        # Source catalog (durable)
        catalog = {
            "regulator": REGULATOR,
            "disclaimer": DISCLAIMER,
            "datasets": [
                {"slug": d.slug, "title": d.title, "url": d.url,
                 "format": d.format, "reporting_period": d.reporting_period,
                 "source_last_updated": d.source_last_updated,
                 "description": d.description}
                for d in DATASETS.values()
            ],
        }
        self.store.write_durable_json("source-catalog.json", catalog)


# Alias so the generic state_ingest runner can construct the sync class.
StateSync = NevadaSync

__all__ = [
    "STATE", "REGULATOR", "DISCLAIMER", "BULLETIN_DISCLAIMER", "DATASETS",
    "PRIVACY_SPEC", "PAGE_POLICY", "ID_PREFIXES", "ID_COLLECTIONS",
    "NevadaSync", "LAB_COLUMNS", "parse_test_type", "normalize_lab_row",
    "normalize_bulletin", "parse_bulletin_content", "iter_lab_rows",
    "sniff_encoding_and_delimiter", "aggregate_lab", "aggregate_bulletins",
    "group_rows_to_reports", "rows_to_coa_measurements",
    "report_to_coa_record", "report_natural_key", "batch_natural_key",
    "canonical_unit", "sample_type_for", "CALCULATED_TESTS",
    "CANONICAL_UNIT_MAP", "StateSync",
]
