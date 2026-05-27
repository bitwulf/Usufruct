#!/usr/bin/env bash
# build_release.sh — package CC + LRS corpora into usufruct-<TAG>.zip for GitHub Releases.
#
# Output layout (matches verify_release.sh contract — single top-level dir):
#   usufruct-<TAG>/
#     articles.jsonl, chunks.jsonl, citation_edges.csv, tree.json,
#     hierarchy.json, article_index.json, manifest.json, validation_report.json
#     articles/, markdown/
#     rs/
#       sections.jsonl, citation_edges.csv, tree.json, hierarchy.json,
#       section_index.json, manifest.json, validation_report.json
#       sections/, markdown/
#
# Usage:
#   scripts/build_release.sh                # tag = today (YYYY-MM-DD)
#   scripts/build_release.sh 2026-05-22     # explicit tag

set -euo pipefail

TAG="${1:-$(date +%Y-%m-%d)}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${ROOT_DIR}/data"
RS_DIR="${DATA_DIR}/rs"
DIST_DIR="${ROOT_DIR}/dist"
STAGE_DIR="${DIST_DIR}/stage-${TAG}"
TOP_NAME="usufruct-${TAG}"
TOP_DIR="${STAGE_DIR}/${TOP_NAME}"

log()  { printf '[build-release] %s\n' "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }

need zip
need shasum
need rsync
need python3
need find
need awk
need wc

# CC: ships exactly what 2026-05-20 shipped (verify_release.sh enforces this list).
CC_FILES=(
  article_index.json
  articles.jsonl
  chunks.jsonl
  citation_edges.csv
  hierarchy.json
  manifest.json
  tree.json
  validation_report.json
)
CC_DIRS=(articles markdown)

# LRS: mirror of CC pattern. Excludes chunks.jsonl (80 MB, not needed pre-refactor)
# and scraper-internal artifacts (folder_map, justia_section_index, notes, phase2_gaps).
RS_FILES=(
  citation_edges.csv
  hierarchy.json
  manifest.json
  section_index.json
  sections.jsonl
  tree.json
  validation_report.json
)
RS_DIRS=(markdown sections)

log "tag:   ${TAG}"
log "data:  ${DATA_DIR}"
log "rs:    ${RS_DIR}"
log "stage: ${STAGE_DIR}"

# Pre-flight: every input present.
for f in "${CC_FILES[@]}"; do
  [ -f "${DATA_DIR}/${f}" ] || die "missing CC file: data/${f}"
done
for d in "${CC_DIRS[@]}"; do
  [ -d "${DATA_DIR}/${d}" ] || die "missing CC dir: data/${d}"
done
for f in "${RS_FILES[@]}"; do
  [ -f "${RS_DIR}/${f}" ] || die "missing LRS file: data/rs/${f}"
done
for d in "${RS_DIRS[@]}"; do
  [ -d "${RS_DIR}/${d}" ] || die "missing LRS dir: data/rs/${d}"
done

# Clean staging.
rm -rf "${STAGE_DIR}"
mkdir -p "${TOP_DIR}/rs"

# Patterns rsync excludes (defense-in-depth; verify_release.sh's leak detector
# treats any of these as fatal).
LEAK_PATTERNS=(
  --exclude='.DS_Store'
  --exclude='__pycache__'
  --exclude='.pytest_cache'
  --exclude='.mypy_cache'
  --exclude='.ruff_cache'
  --exclude='*.pyc'
  --exclude='.git'
  --exclude='.venv'
  --exclude='.env'
  --exclude='.env.*'
  --exclude='CLAUDE.md'
  --exclude='prompt.md'
)

log "staging CC at top of bundle"
for f in "${CC_FILES[@]}"; do
  cp "${DATA_DIR}/${f}" "${TOP_DIR}/${f}"
done
for d in "${CC_DIRS[@]}"; do
  rsync -a "${LEAK_PATTERNS[@]}" "${DATA_DIR}/${d}/" "${TOP_DIR}/${d}/"
done

log "staging LRS under rs/"
for f in "${RS_FILES[@]}"; do
  cp "${RS_DIR}/${f}" "${TOP_DIR}/rs/${f}"
done
for d in "${RS_DIRS[@]}"; do
  rsync -a "${LEAK_PATTERNS[@]}" "${RS_DIR}/${d}/" "${TOP_DIR}/rs/${d}/"
done

# Local verification mirroring verify_release.sh — fail fast before zipping.
log "verifying staged layout"

LEAKED=$(find "${STAGE_DIR}" \( \
    -name '.venv' -o -name '.git' -o -name '__pycache__' -o -name '.DS_Store' \
    -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' \
    -o -name 'CLAUDE.md' -o -name 'prompt.md' -o -name '.env' -o -name '.env.*' \
    \) 2>/dev/null || true)
if [ -n "${LEAKED}" ]; then
  log "leaked entries detected (would fail verify_release.sh):"
  printf '%s\n' "${LEAKED}" | sed 's|^|  |' >&2
  die "refusing to package; remove the offending files and retry"
fi

# Cross-check CC manifest.totals.articles_emitted vs articles.jsonl line count
# (the exact assertion verify_release.sh:164-170 runs on the published artifact).
CC_EMITTED=$(python3 -c "import json; print(json.load(open('${TOP_DIR}/manifest.json'))['totals']['articles_emitted'])")
CC_LINES=$(wc -l < "${TOP_DIR}/articles.jsonl" | tr -d ' ')
if [ "${CC_EMITTED}" != "${CC_LINES}" ]; then
  die "CC manifest.articles_emitted=${CC_EMITTED} but articles.jsonl has ${CC_LINES} lines"
fi
log "  CC manifest.articles_emitted matches articles.jsonl (${CC_LINES})"

# Parallel cross-check for LRS — manifest uses sections_emitted.
RS_EMITTED=$(python3 -c "import json; print(json.load(open('${TOP_DIR}/rs/manifest.json'))['totals']['sections_emitted'])")
RS_LINES=$(wc -l < "${TOP_DIR}/rs/sections.jsonl" | tr -d ' ')
if [ "${RS_EMITTED}" != "${RS_LINES}" ]; then
  die "LRS manifest.sections_emitted=${RS_EMITTED} but sections.jsonl has ${RS_LINES} lines"
fi
log "  LRS manifest.sections_emitted matches sections.jsonl (${RS_LINES})"

# Build the zip.
ZIP_NAME="${TOP_NAME}.zip"
ZIP_PATH="${DIST_DIR}/${ZIP_NAME}"
SHA_PATH="${ZIP_PATH}.sha256"

rm -f "${ZIP_PATH}" "${SHA_PATH}"
log "zipping → ${ZIP_PATH}"
( cd "${STAGE_DIR}" && zip -qr "${ZIP_PATH}" "${TOP_NAME}" )

log "writing sha256 sidecar"
( cd "${DIST_DIR}" && shasum -a 256 "${ZIP_NAME}" > "${ZIP_NAME}.sha256" )

# Cleanup staging.
rm -rf "${STAGE_DIR}"

# Report.
SIZE_HUMAN=$(du -h "${ZIP_PATH}" | awk '{print $1}')
SHA=$(awk '{print $1}' "${SHA_PATH}")
log "ok"
log "  zip:    ${ZIP_PATH}  (${SIZE_HUMAN})"
log "  sha256: ${SHA}"
log ""
log "next steps:"
log "  1. gh release create ${TAG} \\"
log "       --repo bitwulf/Usufruct \\"
log "       --title \"Snapshot ${TAG}\" \\"
log "       --notes \"Includes CC (unchanged) + LRS under rs/ subdir.\" \\"
log "       ${ZIP_PATH} ${SHA_PATH}"
log "  2. scripts/verify_release.sh ${TAG}"
