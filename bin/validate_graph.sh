#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BORIS_BIN=${BORIS_BIN:-$("$ROOT/scripts/ensure-boris.sh")}

CONTENT_DIR=${CONTENT_DIR:-content}
DIST_DIR=${DIST_DIR:-dist/cantilever}

cd "$ROOT"

echo "==> Validating Thermal Extraction Devices form IDs"
python3 scripts/ted_ids.py --root "$CONTENT_DIR" --map metadata/id-map.jsonl --all-state-maps

echo "==> Auditing device records against the Device Architecture Taxonomy"
python3 scripts/audit_device_taxonomy.py "$CONTENT_DIR" --vocab metadata/device-taxonomy.json

echo "==> Auditing COA / lab-result content against the COA graph rules"
python3 scripts/audit_coa_content.py "$CONTENT_DIR" --map metadata/id-map.jsonl

echo "==> Auditing cannabinoid thermal-property conditions"
python3 scripts/audit_cannabinoid_thermal.py "$CONTENT_DIR"

echo "==> Auditing device records against the record-completeness floor"
python3 scripts/audit_record_completeness.py "$CONTENT_DIR" --vocab metadata/device-taxonomy.json

echo "==> Validating cultivar identity claim registry"
python3 scripts/validate_cultivar_claims.py --root "$CONTENT_DIR" --claims metadata/cultivar-claims.jsonl

echo "==> Auditing cultivar pages for chemotype-implying phrasing (P18)"
# Medium-severity findings (e.g. unsourced 'Common Primary Terpene Descriptors')
# do not block the build; high-severity ones (unsourced dominant-terpene or
# typical-THC claims) do. Rewording, not suppression, is the fix.
python3 scripts/audit_cultivar_chemotype.py "$CONTENT_DIR/cultivars"

echo "==> Validating the evidence-aware crosslink layer"
python3 scripts/validate_crosslinks.py --root "$CONTENT_DIR" --map metadata/id-map.jsonl --claims metadata/cultivar-claims.jsonl --coa metadata/coa-records.jsonl

echo "==> Running Boris graph diagnostics"
CHECK_REPORT=$(mktemp "${TMPDIR:-/tmp}/ted-boris-check.XXXXXX")
trap 'rm -f "$CHECK_REPORT"' EXIT

if "$BORIS_BIN" check --input "$CONTENT_DIR" --format json 2>"$CHECK_REPORT"; then
  echo "✅ Boris graph diagnostics passed"
else
  # Boris >= eb49644 (BORIS-08) makes `unreferenced_page` informational by
  # default, so a non-zero exit here is a real failure. The former filter
  # that tolerated those findings is gone; use `--fail-on-unreferenced`
  # for a deliberate strict audit instead.
  echo "❌ Boris graph diagnostics failed" >&2
  cat "$CHECK_REPORT" >&2
  exit 1
fi

echo "==> Compiling primary Cantilever publication"
BORIS_BIN="$BORIS_BIN" CONTENT_DIR="$CONTENT_DIR" DIST_DIR="$DIST_DIR" ./scripts/ted-build.sh

echo "🎉 Graph, form IDs, HTML IDs, and publication checks passed cleanly."
