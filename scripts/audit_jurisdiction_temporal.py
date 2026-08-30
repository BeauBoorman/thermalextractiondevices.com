#!/usr/bin/env python3
"""Audit jurisdiction legal-state statements against the temporal model.

`metadata/jurisdiction-sources.jsonl` has carried per-source effective_date
since the P03 sweep; `metadata/jurisdiction-legal-states.jsonl` (this model,
card P04) adds status semantics: enacted / pending / repealed. A jurisdiction
profile must be able to say "retail sales are not operational, and here is the
enacted implementation schedule that changes that in 2027" without collapsing
future law into present tense.

This audit validates the registry (JSON Schema shape plus cross-field rules
the schema cannot express) and the temporal invariants. It does NOT rewrite
jurisdiction pages — v1 is schema + validator only; the mass migration is a
later wave.

Rules
-----
TEM-01 (error)   registry row is valid JSON and satisfies
                 metadata/jurisdiction-schema.json (jsonschema when available;
                 a structural fallback checks types/enums/formats otherwise)
TEM-02 (error)   status is one of enacted/pending/repealed (schema enum, kept
                 here so the failure names the rule)
TEM-03 (error)   a pending statement carries effective_date = null ONLY when
                 note explains why (indeterminacy must be justified, never silent)
TEM-04 (error)   a repealed statement carries a non-null effective_date
                 (repeal is an event; it cannot be undated)
TEM-05 (warning) stale future-dated statement: status=pending/enacted with a
                 FUTURE effective_date that has since ELAPSED as of
                 verified_as_of comparison against --as-of (default: today),
                 without review_acknowledged=true — the record claims a
                 transition that should already have happened but was never
                 re-verified
TEM-06 (error)   effective_date earlier than verified_as_of while status=pending:
                 a transition recorded as pending that was already effective
                 when it was verified — the record lies about which state was seen
TEM-07 (warning) two statements of the same (jurisdiction, topic) both status
                 = enacted with overlapping validity (both effective, neither
                 repealed/superseded) — usually a missing repeal or supersedes link
TEM-08 (error)   supersedes_effective_date is not strictly earlier than
                 effective_date (Oklahoma-style extensions must move forward)
TEM-09 (error)   jurisdiction_id does not exist in metadata/id-map.jsonl
TEM-10 (warning) statement older than the staleness horizon
                 (--stale-days, default 400) since verified_as_of —
                 re-verification due, not a contradiction

Severity split follows the repo convention: mechanical impossibilities block
(TEM-01..04, 06, 08, 09); staleness and overlap are advisory first
(TEM-05, 07, 10) because they are review conditions, not contradictions.

Usage:
    python3 scripts/audit_jurisdiction_temporal.py
        --registry metadata/jurisdiction-legal-states.jsonl
        --schema  metadata/jurisdiction-schema.json
        --map     metadata/id-map.jsonl
        --as-of 2026-08-30            # evaluate staleness at a fixed date
        --warnings-only

Exit codes: 0 = no errors; 1 = blocking findings; 2 = tooling error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
STATUSES = {"enacted", "pending", "repealed"}
ISO_FORMAT = "%Y-%m-%d"

# Topics exempt from the staleness nag: structural facts that do not drift
# (e.g. the legal-disclaimer basis) would be noise; v1 keeps this empty and
# the rule general.
STALENESS_EXEMPT_TOPICS: set[str] = set()


def _parse_date(value: str):
    try:
        return _dt.datetime.strptime(value, ISO_FORMAT).date()
    except (TypeError, ValueError):
        return None


def load_jsonl(path: Path) -> list[tuple[int, dict]]:
    """Return (line_number, row) for every parseable row; parse failures raise."""
    rows: list[tuple[int, dict]] = []
    text = path.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}:{number}: invalid JSON: {error}") from error
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{number}: row is not a JSON object")
        rows.append((number, row))
    return rows


def _structural_check(row: dict) -> list[str]:
    """Schema-equivalent checks that run without the jsonschema package."""
    problems: list[str] = []
    required = [
        "schema_version", "jurisdiction_id", "topic", "status",
        "effective_date", "verified_as_of", "source_url", "retrieved_at",
    ]
    for key in required:
        if key not in row:
            problems.append(f"missing required key {key!r}")
    allowed = set(required) | {
        "review_acknowledged", "note", "supersedes_effective_date",
    }
    for key in row:
        if key not in allowed:
            problems.append(f"unknown key {key!r} (additionalProperties: false)")
    if row.get("schema_version") != "1.0":
        problems.append("schema_version must be '1.0'")
    jid = row.get("jurisdiction_id")
    if not isinstance(jid, str) or not re.fullmatch(r"jurisdictions/TJUR-[0-9]{4}", jid or ""):
        problems.append("jurisdiction_id must match jurisdictions/TJUR-XXXX")
    if not isinstance(row.get("topic"), str) or len(str(row.get("topic", ""))) < 3:
        problems.append("topic must be a string of at least 3 characters")
    if row.get("status") not in STATUSES:
        problems.append(f"status must be one of {sorted(STATUSES)}")
    for key in ("effective_date", "verified_as_of", "retrieved_at",
                "supersedes_effective_date"):
        value = row.get(key)
        if key in ("effective_date", "supersedes_effective_date"):
            if value is None:
                continue  # explicitly allowed null
        if value is None:
            problems.append(f"{key} must not be null")
        elif not isinstance(value, str) or not DATE_RE.fullmatch(str(value)):
            problems.append(f"{key} must be an ISO YYYY-MM-DD date")
    if not isinstance(row.get("source_url"), str) or len(str(row.get("source_url", ""))) < 8:
        problems.append("source_url must be a non-trivial string")
    if "review_acknowledged" in row and not isinstance(row["review_acknowledged"], bool):
        problems.append("review_acknowledged must be a boolean")
    if "note" in row and not isinstance(row["note"], str):
        problems.append("note must be a string")
    return problems


def validate_row_schema(row: dict, schema: dict) -> list[str]:
    """Full JSON Schema validation when jsonschema is importable."""
    try:
        import jsonschema
    except ImportError:
        return _structural_check(row)
    validator = jsonschema.Draft7Validator(schema)
    return [
        # message already names the failing keyword; keep the best-supported
        # fragment (last path element) for terse output
        f"{error.message}"
        for error in sorted(validator.iter_errors(row), key=lambda e: list(e.absolute_path))
    ]


def audit_rows(
    rows: list[tuple[int, dict]],
    known_ids: set[str],
    as_of: _dt.date,
    stale_days: int,
    schema: dict,
) -> list[tuple[str, str, str]]:
    """Return [(severity, rule, message)] for the whole registry."""
    findings: list[tuple[str, str, str]] = []

    seen_states: dict[tuple[str, str], list[tuple[int, dict]]] = {}

    for number, row in rows:
        where = f"row {number}"

        schema_problems = validate_row_schema(row, schema)
        for problem in schema_problems:
            findings.append(("error", "TEM-01", f"{where}: {problem}"))

        status = row.get("status")
        if status not in STATUSES:
            # TEM-02 named explicitly even when TEM-01 already caught it
            findings.append(("error", "TEM-02", f"{where}: status {status!r} not in enacted/pending/repealed"))
            continue  # invariants below assume a valid status

        effective = _parse_date(row.get("effective_date") or "")
        verified = _parse_date(row.get("verified_as_of") or "")
        superseded = _parse_date(row.get("supersedes_effective_date") or "")
        acknowledged = bool(row.get("review_acknowledged", False))
        note = str(row.get("note") or "")

        # TEM-03: pending + null effective_date requires a justifying note.
        if status == "pending" and effective is None and not note.strip():
            findings.append((
                "error", "TEM-03",
                f"{where}: pending statement without effective_date and without "
                "an explanatory note — indeterminacy must be justified, not silent",
            ))

        # TEM-04: repeal is an event; it must be dated.
        if status == "repealed" and effective is None:
            findings.append((
                "error", "TEM-04",
                f"{where}: repealed statement without effective_date",
            ))

        # TEM-05: future-dated transition whose date has elapsed unverified.
        if (
            effective is not None
            and verified is not None
            and effective > verified  # future-dated when verified
            and effective <= as_of    # and has since elapsed
            and not acknowledged
        ):
            findings.append((
                "warning", "TEM-05",
                f"{where}: {status} statement effective {row['effective_date']} was "
                f"future-dated at verification ({row['verified_as_of']}) and has now "
                f"elapsed as of {as_of.isoformat()} — re-verify or set "
                "review_acknowledged=true",
            ))

        # TEM-06: pending but already effective at verification time.
        if (
            status == "pending"
            and effective is not None
            and verified is not None
            and effective <= verified
        ):
            findings.append((
                "error", "TEM-06",
                f"{where}: pending statement was already effective "
                f"({row['effective_date']}) when verified ({row['verified_as_of']}) — "
                "the record claims a pending state that had already taken effect",
            ))

        # TEM-08: supersession must move strictly forward.
        if effective is not None and superseded is not None:
            if superseded >= effective:
                findings.append((
                    "error", "TEM-08",
                    f"{where}: supersedes_effective_date {row['supersedes_effective_date']} "
                    f"is not earlier than effective_date {row['effective_date']}",
                ))

        # TEM-09: jurisdiction must exist.
        jid = row.get("jurisdiction_id")
        if jid not in known_ids:
            findings.append((
                "error", "TEM-09",
                f"{where}: jurisdiction_id {jid!r} not present in the id map",
            ))

        # TEM-10: staleness horizon.
        if (
            verified is not None
            and row.get("topic") not in STALENESS_EXEMPT_TOPICS
            and (as_of - verified).days > stale_days
        ):
            findings.append((
                "warning", "TEM-10",
                f"{where}: verified_as_of {row['verified_as_of']} is older than "
                f"{stale_days} days as of {as_of.isoformat()} — re-verification due",
            ))

        seen_states.setdefault((jid, row.get("topic")), []).append((number, row))

    # TEM-07: overlapping enacted states for one topic.
    for (jid, topic), entries in sorted(seen_states.items()):
        active = [
            (n, r) for n, r in entries
            if r.get("status") == "enacted"
            and (_parse_date(r.get("effective_date") or "") or as_of) <= as_of
        ]
        dated = [r for _, r in active if r.get("effective_date") is not None]
        if len(dated) > 1:
            # Only an overlap when neither supersedes the other.
            unsuperseded = [
                r for r in dated
                if not any(
                    other.get("supersedes_effective_date") == r.get("effective_date")
                    for _, other in entries
                )
            ]
            if len(unsuperseded) > 1:
                findings.append((
                    "warning", "TEM-07",
                    f"{jid} topic {topic!r}: {len(unsuperseded)} enacted statements "
                    f"(rows {sorted(n for n, _ in active)}) both in force — add a "
                    "repeal or supersedes_effective_date link",
                ))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path,
                        default=Path("metadata/jurisdiction-legal-states.jsonl"))
    parser.add_argument("--schema", type=Path,
                        default=Path("metadata/jurisdiction-schema.json"))
    parser.add_argument("--map", type=Path,
                        default=Path("metadata/id-map.jsonl"))
    parser.add_argument("--as-of", type=str, default=None,
                        help="evaluate staleness at this ISO date (default: today)")
    parser.add_argument("--stale-days", type=int, default=400,
                        help="re-verification horizon in days (default: 400)")
    parser.add_argument("--warnings-only", action="store_true",
                        help="never exit non-zero; report findings only")
    args = parser.parse_args()

    as_of = _parse_date(args.as_of) if args.as_of else _dt.date.today()
    if as_of is None:
        print("Jurisdiction temporal audit: --as-of must be YYYY-MM-DD", file=sys.stderr)
        return 2

    try:
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        rows = load_jsonl(args.registry)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Jurisdiction temporal audit: error: {error}", file=sys.stderr)
        return 2

    known_ids: set[str] = set()
    try:
        for line in args.map.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                known_ids.add(entry.get("id"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Jurisdiction temporal audit: error loading id map: {error}", file=sys.stderr)
        return 2

    try:
        findings = audit_rows(rows, known_ids, as_of, args.stale_days, schema)
    except Exception as error:  # tool error => exit 2, never misread as findings
        print(f"Jurisdiction temporal audit: error: {error}", file=sys.stderr)
        return 2

    errors = [f for f in findings if f[0] == "error"]
    warnings = [f for f in findings if f[0] == "warning"]

    for severity, rule, message in findings:
        print(f"  [{severity.upper()}] {rule}: {message}")

    print(
        f"Jurisdiction temporal audit: {len(rows)} statement(s), "
        f"{len(errors)} error(s), {len(warnings)} warning(s) "
        f"across {len(findings)} finding(s) (as-of {as_of.isoformat()})"
    )
    if args.warnings_only:
        return 0
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
