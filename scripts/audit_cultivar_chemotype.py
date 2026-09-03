#!/usr/bin/env python3
"""Flag cultivar pages that imply fixed chemotypes (card P18, automation half).

Chemistry belongs to reports and batches, never to cultivar names as genetic
objects (see ``content/reference/cultivar-name-vs-chemovar.md`` and the
chemistry-firewall Asides on every cultivar page). This audit scans
``content/cultivars/**`` for the marketing phrasings card P18 flags:

* "dominant terpene" / "typically dominant"
* "typical THC" / "typical CBD" (or any "typical <analyte>")
* "expected potency"
* "common primary terpenes"
* percentage / mg-g potency figures asserted as properties of the cultivar
  name rather than of a cited batch or report

Each phrase is only a finding when it is **not accompanied by an explicit
evidence anchor** on the same line. Evidence anchors are:

* a claim-registry machine record (``CLM-XXXX``)
* an explicit batch/report citation (``batch``, ``COA``, ``report``,
  ``TLAB-``, ``observed in``)
* a source-qualifier ("as reported by", "per <source>", "seed-bank listing")

This mirrors the suppression discipline of the other ``audit_*`` scripts via
``scripts/audit_common.py`` (findings carry stable ``CODE:path`` keys and can
be suppressed in the audit config), but it starts empty-suppression: the
correct fix is rewording, not suppressing.

Exit codes: 0 = no findings, 1 = findings at/above threshold, 2 = tool error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Pattern, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_common import (  # noqa: E402
    Finding,
    is_suppressed,
    load_config,
    severity_rank,
)

#: (code, severity, compiled pattern) — order defines report order.
FLAG_PATTERNS: Tuple[Tuple[str, str, Pattern[str]], ...] = (
    ("CUL-CHM-001", "high",
     re.compile(r"\bdominant terpene", re.IGNORECASE)),
    ("CUL-CHM-002", "high",
     re.compile(r"\btypical\s+(?:THC|CBD|CBN|CBC|THCA|CBDA|potency|analyte)",
                re.IGNORECASE)),
    ("CUL-CHM-003", "high",
     re.compile(r"\bexpected potency\b", re.IGNORECASE)),
    ("CUL-CHM-004", "medium",
     re.compile(r"\bcommon primary terpenes?\b", re.IGNORECASE)),
    ("CUL-CHM-005", "medium",
     re.compile(r"\bterpene profile of\b|\bprofile of the cultivar\b",
                re.IGNORECASE)),
    # Numeric chemistry asserted without a same-line evidence anchor.
    ("CUL-CHM-006", "high",
     re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|mg/g|mg\b|µg/g|ug/g)\b.{0,60}"
                r"(?:THC|CBD|myrcene|caryophyllene|limonene|pinene|"
                r"terpinolene|linalool|humulene)",
                re.IGNORECASE | re.DOTALL)),
)

#: Same-line evidence anchors that legitimate a flagged phrase.
EVIDENCE_ANCHORS: Tuple[Pattern[str], ...] = (
    re.compile(r"\bCLM-\d{4}\b"),
    re.compile(r"\bTLAB-\d{4}\b"),
    re.compile(r"\bbatch\b|\bCOA\b|\breport(?:s|ed)?\b|\blaborator",
               re.IGNORECASE),
    re.compile(r"\bobserved in\b|\bmeasured in\b|\bwas analyzed",
               re.IGNORECASE),
    re.compile(r"\bas reported\b|\bper the source\b|\bseed[- ]bank\b|\bretrieved",
               re.IGNORECASE),
)

FIREWALL_INCLUDE = "cultivar-identity-warning.md"
CHEMOVAR_LINK = "cultivar-name-vs-chemovar.md"


def line_has_evidence(line: str) -> bool:
    return any(pattern.search(line) for pattern in EVIDENCE_ANCHORS)


def audit_file(path: Path, root_label: str) -> List[Finding]:
    findings: List[Finding] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    body = text.split("---", 2)[2] if text.startswith("---") else text
    body_offset = len(text) - len(body)
    lines = body.split("\n")

    # Structural hygiene: the identity-warning include and the chemovar link
    # are the standing "chemistry firewall" markers on cultivar pages.
    if FIREWALL_INCLUDE not in text and CHEMOVAR_LINK not in text:
        findings.append(
            Finding(
                code="CUL-CHM-000",
                severity="medium",
                message="cultivar page carries neither the identity-warning "
                        "include nor the chemovar firewall link",
                path=root_label,
            )
        )

    for number, line in enumerate(lines, start=1):
        for code, severity, pattern in FLAG_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            if line_has_evidence(line):
                continue
            findings.append(
                Finding(
                    code=code,
                    severity=severity,
                    message=f"chemotype-implying phrasing without an evidence "
                            f"anchor: {match.group(0)!r}",
                    path=root_label,
                    line=number,
                    detail=line.strip()[:200],
                )
            )
            break  # one finding per line is enough to prompt a reword
    return findings


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "root", nargs="?", type=Path, default=ROOT / "content" / "cultivars",
        help="cultivar content tree (default content/cultivars)",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="audit suppression config (audit_common.load_config format)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as error:
        print(f"cultivar leakage audit: error: {error}", file=sys.stderr)
        return 2

    root: Path = args.root
    if not root.is_dir():
        print(f"cultivar leakage audit: error: {root} is not a directory",
              file=sys.stderr)
        return 2

    findings: List[Finding] = []
    files = sorted(p for p in root.rglob("*.md") if not p.name.startswith("_"))
    for path in files:
        rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        for finding in audit_file(path, rel):
            if not is_suppressed(finding, config):
                findings.append(finding)

    findings.sort(key=lambda f: (f.path, f.line or 0, f.code))

    threshold = severity_rank(str(config.get("fail_threshold", "high")))
    blocking = [f for f in findings if severity_rank(f.severity) >= threshold]

    for finding in findings:
        where = f"{finding.path}:{finding.line}" if finding.line else finding.path
        print(f"cultivar leakage: {finding.severity}: {where}: "
              f"[{finding.code}] {finding.message}")

    if findings:
        print(f"cultivar leakage audit: {len(findings)} finding(s) "
              f"({len(blocking)} blocking) across {len(files)} cultivar page(s)",
              file=sys.stderr)
        return 1 if blocking else 0

    print(f"cultivar leakage audit: {len(files)} cultivar page(s) clean — "
          f"no chemotype-implying phrasing without evidence anchors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
