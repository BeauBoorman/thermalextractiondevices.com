#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BORIS_BIN=${BORIS_BIN:-$("$ROOT/scripts/ensure-boris.sh")}
CONTENT_DIR=${CONTENT_DIR:-content}
THEME=${THEME:-themes/cantilever}
DIST_DIR=${DIST_DIR:-dist/cantilever}
SITE_URL=${SITE_URL:-https://thermalextractiondevices.com}
BORIS_JOBS=${BORIS_JOBS:-1}

cd "$ROOT"

# Boris audits the output directory while compiling. Remove prior generated
# output first so stale crosslink index pages cannot be treated as source
# links before the post-build crosslink step recreates the current set.
DIST_PATH="$DIST_DIR"
if [[ "$DIST_PATH" != /* ]]; then
  DIST_PATH="$ROOT/$DIST_PATH"
fi
if ! DIST_PATH=$(python3 -c '
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve(strict=False))
' "$DIST_PATH"); then
  echo "ted-build: cannot resolve DIST_DIR=$DIST_DIR" >&2
  exit 2
fi
case "$DIST_PATH" in
  "$ROOT/dist"/*) ;;
  *)
    echo "ted-build: refusing unsafe DIST_DIR=$DIST_DIR" >&2
    exit 2
    ;;
esac
rm -rf -- "$DIST_PATH"

python3 scripts/ted_ids.py --root "$CONTENT_DIR" --map metadata/id-map.jsonl --all-state-maps
python3 scripts/audit_markdown_links.py "$CONTENT_DIR"

"$BORIS_BIN" \
  --input "$CONTENT_DIR" \
  --theme "$THEME" \
  --html-dir "$DIST_DIR" \
  --sitemap \
  --site-url "$SITE_URL" \
  --layout-rule default glob:botanicals/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:changelog/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:cultivars/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:devices/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:guides/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:lab-results/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:law-and-use/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:manufacturers/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:products/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:reference/* "$THEME/layouts/compact.html" \
  --layout-rule default glob:terpenes/* "$THEME/layouts/compact.html" \
  --jobs "$BORIS_JOBS"

# Evidence-aware crosslinking: derive labeled, bounded navigation from
# structured relationships (frontmatter relations, claim registry, COA
# records) and inject it into the rendered pages. Runs before the HTML ID
# audit and the public-release audit so the injected HTML is fully checked.
python3 scripts/crosslinks.py \
  --root "$CONTENT_DIR" \
  --map metadata/id-map.jsonl \
  --claims metadata/cultivar-claims.jsonl \
  --coa metadata/coa-records.jsonl \
  --out exports/crosslinks.json \
  --rag exports/crosslinks-rag.md \
  --html-dir "$DIST_DIR" \
  --inject

python3 scripts/audit_html_ids.py "$DIST_DIR"

if [[ -f "$DIST_DIR/_boris/proof/checks.json" ]]; then
  bad_checks=$(jq -r '[.checks[] | select(.status != "passed" and .status != "not-applicable")] | length' "$DIST_DIR/_boris/proof/checks.json")
  if [[ "$bad_checks" -ne 0 ]]; then
    echo "Thermal Extraction Devices publication checks failed: $bad_checks check(s) are not green." >&2
    exit 1
  fi
fi

# Copy the committed security-headers manifest into the build output so
# Cloudflare Pages applies it on deploy. The source of truth is repo-root
# _headers; dist/ is generated and must never be edited directly.
if [[ -f "$ROOT/_headers" ]]; then
  cp "$ROOT/_headers" "$DIST_DIR/_headers"
  echo "==> Security headers manifest copied to $DIST_DIR/_headers"
fi

# Syndication feeds: generate RSS 2.0 + Atom from the time-ordered
# collections after the HTML ID audit so feed URLs reference the final
# output layout. Feeds land at the dist root, where Cloudflare Pages
# serves them beside index.html.
python3 scripts/generate_feeds.py \
  --content "$CONTENT_DIR" \
  --output "$DIST_DIR" \
  --site-url "$SITE_URL"

# Public-release audit hook. Audit failures and audit-tool errors are release
# failures; a broken audit cannot silently produce a deployable build.
if [[ -f "$ROOT/docs/audit-config.json" ]]; then
  set +e
  python3 scripts/audit_public_release.py --config docs/audit-config.json --root "$ROOT" >&2
  AUDIT_RC=$?
  set -e
  if [[ "$AUDIT_RC" -eq 0 ]]; then
    echo "==> Public-release audit passed"
  elif [[ "$AUDIT_RC" -eq 2 ]]; then
    echo "❌ Public-release audit tool failure (output above)" >&2
    exit 2
  else
    echo "❌ Public-release audit: blocking findings (output above)" >&2
    exit 1
  fi
fi

echo "Thermal Extraction Devices build passed: $DIST_DIR"
