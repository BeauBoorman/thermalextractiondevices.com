"""Registry-backed analyte validation for state ingest surfaces.

Every distinct analyte/test name an adapter emits must resolve to a
canonical registry id. v1 runs this as a warning-tier check (the registry
is young and state spellings arrive unannounced): unresolved names are
reported, never fatal, so syncs keep flowing while the registry catches
up. Findings follow the archive's ``(severity, rule, message)`` convention
(see ``audit_coa_content``); pass ``fail_on_unresolved=True`` to harden a
surface once its registry coverage is complete.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Optional

from .analytes import canonical_id, resolve_analyte

RULE = "analyte-registry"


def validate_analytes(names: Iterable[str],
                      *,
                      source: str = "",
                      fail_on_unresolved: bool = False,
                      ) -> list[tuple[str, str, str]]:
    """Validate distinct analyte names against the canonical registry.

    Returns findings as ``(severity, rule, message)`` tuples:

    * ``("warning", RULE, ...)`` for each distinct unresolved name — the
      v1 tier. The name is preserved verbatim in the message so the
      registry can grow from real surfaces.
    * ``("error", RULE, ...)`` for the same condition only when
      ``fail_on_unresolved=True`` (hardened surfaces).
    """
    severity = "error" if fail_on_unresolved else "warning"
    findings: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for raw in names:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        entry = resolve_analyte(name)
        if entry is None:
            slug = canonical_id(name)
            where = f" [{source}]" if source else ""
            findings.append((
                severity, RULE,
                f"unmapped analyte{where}: {name!r} (deterministic slug {slug!r}); "
                "add it to metadata/analyte-registry.json or map its alias",
            ))
    return findings


def coverage(names: Iterable[str]) -> dict:
    """Resolution summary for a surface (registry growth driver)."""
    counts = Counter()
    unresolved: list[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        entry = resolve_analyte(name)
        if entry is None:
            counts["unresolved"] += 1
            unresolved.append(name)
        else:
            counts[entry["id"]] += 1
    total = sum(counts.values())
    resolved = total - counts["unresolved"]
    return {
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "ratio": (resolved / total) if total else 1.0,
    }


def report_findings(findings: list[tuple[str, str, str]],
                    *, stream=None) -> None:
    """Print findings in the archive's audit format."""
    import sys
    out = stream or sys.stdout
    for severity, rule, message in findings:
        print(f"  [{severity.upper()}] {rule}: {message}", file=out)


def assert_all_mapped(names: Iterable[str], *, source: str = "") -> None:
    """Hard-fail variant for tests and hardened surfaces."""
    findings = validate_analytes(names, source=source, fail_on_unresolved=True)
    if findings:
        details = "; ".join(message for _, _, message in findings)
        raise ValueError(f"unmapped analytes: {details}")


__all__ = [
    "RULE",
    "validate_analytes",
    "coverage",
    "report_findings",
    "assert_all_mapped",
]
