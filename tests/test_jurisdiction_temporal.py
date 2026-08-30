"""Unit tests for scripts/audit_jurisdiction_temporal.py (temporal model P04).

Fixture philosophy: every rule gets a pending-vs-effective contrast — the
same topic shape with the temporal fields twisted, asserting the rule fires
on the impossible/stale variant and stays silent on the honest one. The
realistic NJ/Virginia rows from metadata/jurisdiction-legal-states.jsonl are
exercised through the same audit_rows entry point the CLI uses.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from audit_jurisdiction_temporal import (  # noqa: E402
    audit_rows,
    load_jsonl,
    validate_row_schema,
)

SCHEMA = json.loads(
    (REPO_ROOT / "metadata" / "jurisdiction-schema.json").read_text(encoding="utf-8")
)
REGISTRY = REPO_ROOT / "metadata" / "jurisdiction-legal-states.jsonl"
KNOWN_IDS = {"jurisdictions/TJUR-0031", "jurisdictions/TJUR-0047",
             "jurisdictions/TJUR-0037", "jurisdictions/TJUR-0074"}
AS_OF = _dt.date(2026, 8, 30)


def statement(**overrides) -> dict:
    """A canonical honest enacted statement (Virginia possession shape)."""
    base = {
        "schema_version": "1.0",
        "jurisdiction_id": "jurisdictions/TJUR-0047",
        "topic": "adult-use possession",
        "status": "enacted",
        "effective_date": "2021-07-01",
        "verified_as_of": "2026-08-09",
        "source_url": "https://cca.virginia.gov/laws/",
        "retrieved_at": "2026-08-09",
    }
    base.update(overrides)
    return base


def audit(rows: list[dict], as_of: _dt.date = AS_OF,
          known_ids: set[str] = KNOWN_IDS) -> dict[tuple[str, str], list[str]]:
    numbered = [(i + 1, row) for i, row in enumerate(rows)]
    findings = audit_rows(numbered, known_ids, as_of, 400, SCHEMA)
    out: dict[tuple[str, str], list[str]] = {}
    for severity, rule, message in findings:
        out.setdefault((severity, rule), []).append(message)
    return out


def rules(findings: dict[tuple[str, str], list[str]]) -> set[str]:
    return {rule for _, rule in findings}


class SchemaShapeTestCase(unittest.TestCase):
    def test_honest_statement_passes_schema(self):
        self.assertEqual(validate_row_schema(statement(), SCHEMA), [])

    def test_unknown_key_rejected(self):
        problems = validate_row_schema(
            statement(legislative_epoch="2021"), SCHEMA)
        self.assertTrue(any("legislative_epoch" in p for p in problems))

    def test_bad_status_rejected(self):
        problems = validate_row_schema(statement(status="legal"), SCHEMA)
        self.assertTrue(problems)

    def test_malformed_date_rejected(self):
        problems = validate_row_schema(
            statement(effective_date="07/01/2021"), SCHEMA)
        self.assertTrue(any("effective_date" in p for p in problems))

    def test_null_effective_date_is_schema_valid(self):
        self.assertEqual(
            validate_row_schema(statement(effective_date=None), SCHEMA), [])


class PendingVsEffectiveTestCase(unittest.TestCase):
    """The core P04 contrasts: same topic, twisted temporal fields."""

    def test_pending_with_future_effective_date_is_honest(self):
        # Virginia retail: pending, effective in the future relative to
        # verification — the canonical enacted-implementation-schedule shape.
        findings = audit([statement(
            topic="adult-use commercial sales",
            status="pending",
            effective_date="2027-07-01",
            note="CCA statutory schedule; conditional, not operational.",
        )])
        self.assertNotIn(
            "TEM-06", rules(findings),
            "future-effective pending statement must not read as contradiction")

    def test_pending_already_effective_at_verification_is_impossible(self):
        # Same statement but verified AFTER its effective date: the record
        # claims a pending state that had already taken effect.
        findings = audit([statement(
            topic="adult-use commercial sales",
            status="pending",
            effective_date="2026-07-01",   # before verified_as_of 2026-08-09
            note="Scheduled transition.",
        )])
        self.assertIn("TEM-06", rules(findings))

    def test_enacted_future_effective_is_honest(self):
        # Virginia hemp: enacted, not yet effective at verification — the
        # P03 flagship case. No rule may fire (it is not stale yet).
        findings = audit([statement(
            topic="hemp product THC limit",
            effective_date="2026-08-15",   # future vs verified 2026-08-09
        )], as_of=_dt.date(2026, 8, 10))
        self.assertEqual(
            rules(findings), set(),
            "enacted-but-not-yet-effective is a legal state, not a finding")

    def test_elapsed_future_dated_statement_flags_stale(self):
        # The same row evaluated AFTER the effective date elapsed.
        findings = audit([statement(
            topic="hemp product THC limit",
            effective_date="2026-08-15",
        )], as_of=_dt.date(2026, 8, 30))
        self.assertIn("TEM-05", rules(findings))

    def test_review_acknowledged_silences_stale_gate_only(self):
        findings = audit([statement(
            topic="hemp product THC limit",
            effective_date="2026-08-15",
            review_acknowledged=True,
        )], as_of=_dt.date(2026, 8, 30))
        self.assertNotIn("TEM-05", rules(findings))
        # Acknowledgement must never silence an impossibility.
        impossible = audit([statement(
            status="pending",
            effective_date="2026-07-01",
            review_acknowledged=True,
            note="x",
        )])
        self.assertIn("TEM-06", rules(impossible))

    def test_pending_without_date_requires_note(self):
        # NJ home grow: proposed, no date — legitimate ONLY with a note.
        silent = audit([statement(
            topic="home cultivation (adult use)",
            status="pending",
            effective_date=None,
            note="Proposed legislation; passage indeterminate.",
        )])
        self.assertNotIn("TEM-03", rules(silent))

        unjustified = audit([statement(
            topic="home cultivation (adult use)",
            status="pending",
            effective_date=None,
        )])
        self.assertIn("TEM-03", rules(unjustified))

    def test_repeal_must_be_dated(self):
        findings = audit([statement(
            topic="home cultivation (adult use)",
            status="repealed",
            effective_date=None,
        )])
        self.assertIn("TEM-04", rules(findings))


class SupersessionTestCase(unittest.TestCase):
    def test_oklahoma_extension_shape_is_valid(self):
        # HB 3143 moves the moratorium end from 2026-08-01 to 2028-08-01.
        findings = audit([statement(
            jurisdiction_id="jurisdictions/TJUR-0037",
            topic="commercial license moratorium",
            effective_date="2028-08-01",
            supersedes_effective_date="2026-08-01",
        )])
        self.assertNotIn("TEM-08", rules(findings))

    def test_non_forward_supersession_is_impossible(self):
        findings = audit([statement(
            topic="commercial license moratorium",
            effective_date="2026-08-01",
            supersedes_effective_date="2028-08-01",
        )])
        self.assertIn("TEM-08", rules(findings))

    def test_equal_dates_are_not_forward(self):
        findings = audit([statement(
            topic="t",
            effective_date="2026-01-01",
            supersedes_effective_date="2026-01-01",
        )])
        self.assertIn("TEM-08", rules(findings))

    def test_overlapping_enacted_states_flag(self):
        rows = [
            statement(topic="adult-use possession",
                      effective_date="2021-07-01"),
            statement(topic="adult-use possession",
                      effective_date="2024-01-01"),
        ]
        findings = audit(rows)
        self.assertIn("TEM-07", rules(findings))

    def test_superseded_state_resolves_overlap(self):
        rows = [
            statement(topic="adult-use possession",
                      effective_date="2021-07-01"),
            statement(topic="adult-use possession",
                      effective_date="2024-01-01",
                      supersedes_effective_date="2021-07-01"),
        ]
        findings = audit(rows)
        self.assertNotIn("TEM-07", rules(findings))


class IdentityAndStalenessTestCase(unittest.TestCase):
    def test_unknown_jurisdiction_fails(self):
        findings = audit([statement(
            jurisdiction_id="jurisdictions/TJUR-9999")],
            known_ids={"jurisdictions/TJUR-0047"})
        self.assertIn("TEM-09", rules(findings))

    def test_staleness_horizon(self):
        findings = audit([statement(
            verified_as_of="2024-01-01")], as_of=_dt.date(2026, 8, 30))
        self.assertIn("TEM-10", rules(findings))

    def test_recent_verification_not_stale(self):
        findings = audit([statement()], as_of=_dt.date(2026, 8, 30))
        self.assertNotIn("TEM-10", rules(findings))


class ShippedRegistryTestCase(unittest.TestCase):
    """The committed sample records must be clean at their own as-of date."""

    @classmethod
    def setUpClass(cls):
        cls.rows = load_jsonl(REGISTRY)

    def test_registry_has_expected_coverage(self):
        ids = {row["jurisdiction_id"] for _, row in self.rows}
        self.assertIn("jurisdictions/TJUR-0031", ids)  # NJ
        self.assertIn("jurisdictions/TJUR-0047", ids)  # Virginia
        topics = {row["topic"] for _, row in self.rows}
        self.assertIn("adult-use commercial sales", topics)

    def test_registry_clean_at_verification_snapshot(self):
        # Evaluated as-of the day after the last verification — nothing has
        # elapsed (VA hemp 2026-08-15 elapsed but carries
        # review_acknowledged=true from the P03 finding write-up).
        findings = audit(
            [row for _, row in self.rows], as_of=_dt.date(2026, 8, 30))
        errors = {rule for (severity, rule) in findings if severity == "error"}
        self.assertEqual(errors, set())

    def test_registry_pending_never_overwrites_enacted(self):
        # NJ home grow: both a repealed (current-law) and a pending
        # (proposed) statement coexist for one topic — the P04 invariant.
        nj_homegrow = [row for _, row in self.rows
                       if row["jurisdiction_id"] == "jurisdictions/TJUR-0031"
                       and row["topic"] == "home cultivation (adult use)"]
        statuses = {row["status"] for row in nj_homegrow}
        self.assertIn("repealed", statuses)
        self.assertIn("pending", statuses)
        # And the pair must not trip the overlap rule (only enacted states do).
        findings = audit([row for _, row in self.rows])
        self.assertNotIn("TEM-07", rules(findings))


if __name__ == "__main__":
    unittest.main()
