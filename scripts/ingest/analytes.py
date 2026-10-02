"""Canonical analyte identity: registry loading, alias resolution, validation.

The registry (``metadata/analyte-registry.json``) is the single source of
canonical analyte identity for every state adapter. v1 scope: the analytes
the Massachusetts CCC and Nevada CCB ingests actually emit, plus the
cannabinoid family by policy. Identity fields (CAS RN, InChIKey, wiki
cross-reference) come from the frozen cannabis-chemistry-wiki where a
profile exists (entry ``source: "wiki"``); adapter-sourced entries carry
spelling identity only and make no chemical identity claim.

Resolution is deliberately conservative and mirrors
``evidence.normalize_analyte_name``: strip matrix/unit decorations, slug
the remainder, and look the slug up in the alias table. Unmatched names
resolve to ``None`` — they are never guessed onto a canonical id — and the
validator reports them so the registry grows from real ingest surfaces.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = ROOT / "metadata" / "analyte-registry.json"

# Greek-spelling normalization: sources print both "α-Pinene" and
# "Alpha-Pinene"; the wiki's canonical names use the glyphs.
_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "α-": "alpha-", "β-": "beta-",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """Deterministic lowercase slug matching the registry alias vocabulary."""
    text = unicodedata.normalize("NFKC", str(text or "")).lower().strip()
    for glyph, word in _GREEK.items():
        text = text.replace(glyph, word)
    # Strip parenthetical unit/matrix decorations: "Arsenic (ppm) Raw Plant
    # Material" -> "arsenic raw plant material"; matrix words are removed by
    # the caller (matrix belongs to the sample, not the analyte).
    text = re.sub(r"\s*\([^)]*\)\s*", " ", text)
    return _SLUG_RE.sub("-", text).strip("-")


# Matrix suffixes observed in MA "ANALYTE/TEST ID" and NV "Test Type Name"
# strings. The analyte identity is the prefix; the matrix is sample context.
# Kept exhaustive for the known state surfaces: a name decorated with a word
# missing here resolves to None (never guessed), which is the intended signal
# to extend this tuple.
_MATRIX_WORDS = (
    "raw plant material", "whole wet plants", "whole wet plant",
    "non-solvent concentrate", "solvent based concentrate",
    "solvent-based concentrate", "infused edible", "infused non-edible",
    "r&d testing (infused products)", "r&d testing", "sub-contract",
)


def _strip_matrix(slug: str) -> str:
    for matrix in _MATRIX_WORDS:
        suffix = "-" + _SLUG_RE.sub("-", matrix)
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
    return slug.strip("-")


@lru_cache(maxsize=1)
def load_registry(path: Optional[Path] = None) -> dict:
    """Load and structurally validate the analyte registry.

    Raises ``ValueError`` on structural defects (duplicate ids, an alias
    claimed by two entries, a wiki-sourced entry missing its identity
    triple) so a bad registry fails loudly at import time, never silently.
    """
    registry_path = Path(path) if path else REGISTRY_PATH
    with open(registry_path, encoding="utf-8") as handle:
        data = json.load(handle)

    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("analyte registry: no entries")

    seen_ids: set[str] = set()
    alias_owner: dict[str, str] = {}
    for entry in entries:
        eid = entry.get("id")
        if not eid or not isinstance(eid, str):
            raise ValueError("analyte registry: entry without id")
        if eid in seen_ids:
            raise ValueError(f"analyte registry: duplicate id {eid!r}")
        seen_ids.add(eid)
        for key in ("display", "class"):
            if not entry.get(key):
                raise ValueError(f"analyte registry: {eid!r} missing {key}")
        aliases = entry.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            raise ValueError(f"analyte registry: {eid!r} missing aliases")
        for alias in aliases:
            slug = _slug(alias)
            if not slug:
                raise ValueError(f"analyte registry: {eid!r} empty alias")
            if slug in alias_owner and alias_owner[slug] != eid:
                raise ValueError(
                    f"analyte registry: alias {slug!r} claimed by both "
                    f"{alias_owner[slug]!r} and {eid!r}"
                )
            alias_owner[slug] = eid
        if entry.get("source") == "wiki":
            for key in ("cas", "inchikey", "wiki_path"):
                if not entry.get(key):
                    raise ValueError(
                        f"analyte registry: wiki entry {eid!r} missing {key}"
                    )

    data["_alias_table"] = alias_owner
    data["_by_id"] = {e["id"]: e for e in entries}
    return data


def resolve_analyte(raw: str) -> Optional[dict]:
    """Resolve a raw analyte/test name to its canonical registry entry.

    Returns the entry dict or ``None``. Matching: exact alias slug first,
    then the matrix-stripped slug (MA/NV decorate names with the sample
    matrix). There is deliberately no prefix tier: a slug that merely
    starts with a known alias ("beta-caryophyllene-oxide",
    "alpha-pinene-oxide", "thc-v") names a different substance than the
    alias and must resolve to its own entry or stay unresolved — never
    silently inherit a neighbor's identity (adversarial review F1).
    Unmatched names stay unresolved — the registry grows from real
    surfaces, it never guesses.
    """
    registry = load_registry()
    table = registry["_alias_table"]
    slug = _slug(raw)
    if not slug:
        return None
    if slug in table:
        return registry["_by_id"][table[slug]]
    stripped = _strip_matrix(slug)
    if stripped != slug and stripped in table:
        return registry["_by_id"][table[stripped]]
    return None


def canonical_id(raw: str) -> str:
    """Canonical id for ``raw`` or the deterministic slug when unmatched."""
    entry = resolve_analyte(raw)
    return entry["id"] if entry else _strip_matrix(_slug(raw))


def registry_entry(canonical: str) -> Optional[dict]:
    """Registry entry for a canonical id (None when unknown)."""
    return load_registry()["_by_id"].get(canonical)


__all__ = [
    "REGISTRY_PATH",
    "load_registry",
    "resolve_analyte",
    "canonical_id",
    "registry_entry",
]
