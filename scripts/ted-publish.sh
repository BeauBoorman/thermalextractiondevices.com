#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BORIS_BIN=${BORIS_BIN:-./bin/boris}
CONTENT_DIR=${CONTENT_DIR:-content}
PUBLISH_DIR=${PUBLISH_DIR:-publish}
THEME=${THEME:-themes/cantilever}
SITE_URL=${SITE_URL:-https://thermalextractiondevices.com}
BORIS_JOBS=${BORIS_JOBS:-1}
RAG_SPLIT_SIZE=${RAG_SPLIT_SIZE:-131072}
RAG_BUNDLE_DIR=${RAG_BUNDLE_DIR:-$PUBLISH_DIR/rag-bundle}
RAG_COMPLETE_DIR=${RAG_COMPLETE_DIR:-$PUBLISH_DIR/rag-complete}
RAG_BUNDLE_NAME=${RAG_BUNDLE_NAME:-thermal-extraction-devices}
RAG_RESOLVED_DIR=${RAG_RESOLVED_DIR:-$PUBLISH_DIR/rag-resolved}
RAG_RESOLVED_BUNDLE_DIR=${RAG_RESOLVED_BUNDLE_DIR:-$PUBLISH_DIR/rag-resolved-bundle}
RAG_RESOLVED_BUNDLE_NAME=${RAG_RESOLVED_BUNDLE_NAME:-${RAG_BUNDLE_NAME}-resolved}

cd "$ROOT"

if [[ ! -x "$BORIS_BIN" ]]; then
  echo "ERROR: Boris binary is not executable: $BORIS_BIN" >&2
  exit 2
fi

mkdir -p "$PUBLISH_DIR"

echo "==> Exporting Thermal Extraction Devices publishing artifacts to $PUBLISH_DIR"
python3 scripts/ted_ids.py --root "$CONTENT_DIR" --map metadata/id-map.jsonl --all-state-maps

"$BORIS_BIN" --input "$CONTENT_DIR" --theme "$THEME" --html-dir "$PUBLISH_DIR/site" --sitemap --site-url "$SITE_URL" --jobs "$BORIS_JOBS" --quiet
"$BORIS_BIN" --input "$CONTENT_DIR" --out "$PUBLISH_DIR/ir" --quiet
"$BORIS_BIN" --input "$CONTENT_DIR" --rag-dir "$PUBLISH_DIR/rag" --split-size "$RAG_SPLIT_SIZE" --quiet
python3 scripts/audit_rag_includes.py --content "$CONTENT_DIR"
python3 scripts/name_rag_bundle.py \
  --input "$PUBLISH_DIR/rag" \
  --output "$RAG_BUNDLE_DIR" \
  --name "$RAG_BUNDLE_NAME"
python3 scripts/resolve_rag_includes.py \
  --input "$PUBLISH_DIR/rag" \
  --output "$RAG_RESOLVED_DIR" \
  --content "$CONTENT_DIR"
python3 scripts/audit_rag_includes.py --export "$RAG_RESOLVED_DIR"
python3 scripts/name_rag_bundle.py \
  --input "$RAG_RESOLVED_DIR" \
  --output "$RAG_RESOLVED_BUNDLE_DIR" \
  --name "$RAG_RESOLVED_BUNDLE_NAME"
"$BORIS_BIN" --input "$CONTENT_DIR" --rag --complete --rag-dir "$RAG_COMPLETE_DIR" --quiet
"$BORIS_BIN" --input "$CONTENT_DIR" --context-dir "$PUBLISH_DIR/context" --split-size "$RAG_SPLIT_SIZE" --quiet

if "$BORIS_BIN" --input "$CONTENT_DIR" --llms-path "$PUBLISH_DIR/llms.txt" --quiet; then
  if python3 -c 'from pathlib import Path; import sys; Path(sys.argv[1]).read_text(encoding="utf-8")' "$PUBLISH_DIR/llms.txt"; then
    echo "✅ llms.txt exported and is valid UTF-8"
  fi
fi

cat > "$PUBLISH_DIR/README.txt" <<'EOF'
Thermal Extraction Devices publishing artifacts

site/      Public HTML site.
           site/recalls.xml, site/safety-advisories.xml  RSS 2.0 syndication feeds.
           site/feed.xml                                Combined Atom 1.0 update feed.
llms.txt   Public crawler/discovery index.
context/   Provenance-rich bundle for LLM context uploads.
rag/       Canonical Boris working-context RAG export + sidecar manifest.
rag-bundle/ Semantically named raw Boris working-context .md packs.
rag-resolved/ Derived working-context packs with content/includes expanded.
rag-resolved-bundle/ Semantically named resolved RAG packs for upload.
rag-complete/ Full Boris RAG corpus with pages, graph, and catalog.
ir/        Machine-readable graph and reverse-index artifacts.
claims.jsonl  Cultivar identity claim registry (machine-readable, provenance-annotated).
EOF

if [[ -f "$ROOT/metadata/cultivar-claims.jsonl" ]]; then
  cp "$ROOT/metadata/cultivar-claims.jsonl" "$PUBLISH_DIR/claims.jsonl"
  echo "✅ cultivar identity claim registry exported to $PUBLISH_DIR/claims.jsonl"
fi

# Syndication feeds: RSS 2.0 (recalls, safety advisories) and combined
# Atom (feed.xml) generated from the content tree into the served site
# directory, beside the Boris-compiled HTML.
python3 scripts/generate_feeds.py \
  --content "$CONTENT_DIR" \
  --output "$PUBLISH_DIR/site" \
  --site-url "$SITE_URL"

echo "==> Running release audits"
python3 scripts/audit_public_release.py --config docs/audit-config.json --root "$ROOT" --report "$PUBLISH_DIR/public-release-report.json"
python3 scripts/audit_sensitive_content.py --config docs/audit-config.json
python3 scripts/audit_large_files.py --config docs/audit-config.json

echo "✅ Publishing export complete: $PUBLISH_DIR"
