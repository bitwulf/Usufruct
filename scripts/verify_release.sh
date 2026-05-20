#!/usr/bin/env bash
# Verify a Usufruct release is publicly accessible and intact.
#
# Usage:
#   scripts/verify_release.sh                # verify latest release
#   scripts/verify_release.sh 2026-05-20     # verify a specific tag
#   USUFRUCT_REPO=fork/Usufruct scripts/verify_release.sh   # verify a fork
#
# Requires: bash, curl, python3, shasum, unzip, find.
# Exit codes: 0 = all checks passed, 1 = one or more checks failed.

set -euo pipefail

REPO="${USUFRUCT_REPO:-bitwulf/Usufruct}"
TAG="${1:-}"
API="https://api.github.com/repos/$REPO"

# Colorize only when stdout is a terminal.
if [ -t 1 ]; then
    R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
else
    R=""; G=""; Y=""; B=""; N=""
fi

fail_count=0
ok()   { printf '  %sOK%s    %s\n'   "$G" "$N" "$*"; }
warn() { printf '  %sWARN%s  %s\n'   "$Y" "$N" "$*"; }
fail() { printf '  %sFAIL%s  %s\n'   "$R" "$N" "$*"; fail_count=$((fail_count + 1)); }

require() {
    command -v "$1" >/dev/null 2>&1 || { printf '%sMissing dependency: %s%s\n' "$R" "$1" "$N" >&2; exit 1; }
}
for dep in curl python3 shasum unzip find awk; do require "$dep"; done

# Resolve tag (default: latest).
if [ -z "$TAG" ]; then
    TAG=$(curl -fsSL "$API/releases/latest" | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
fi
printf '%sVerifying%s %s @ tag %s%s%s\n\n' "$B" "$N" "$REPO" "$B" "$TAG" "$N"

# Fetch release metadata once.
RELEASE_JSON=$(curl -fsSL "$API/releases/tags/$TAG")

ZIP_URL=$(printf '%s' "$RELEASE_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
urls = [a['browser_download_url'] for a in d['assets'] if a['name'].endswith('.zip')]
print(urls[0] if urls else '')
")
SHA_URL=$(printf '%s' "$RELEASE_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
urls = [a['browser_download_url'] for a in d['assets'] if a['name'].endswith('.sha256')]
print(urls[0] if urls else '')
")

if [ -z "$ZIP_URL" ]; then
    printf '%sNo .zip asset found on release %s%s\n' "$R" "$TAG" "$N" >&2
    exit 1
fi

echo "Assets:"
echo "  zip: $ZIP_URL"
[ -n "$SHA_URL" ] && echo "  sha: $SHA_URL"
echo

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# 1. Asset URLs reachable without auth.
echo "[1/5] Public asset reachable (unauthenticated)"
HTTP=$(curl -sIL -o /dev/null -w '%{http_code}' "$ZIP_URL")
[ "$HTTP" = "200" ] && ok "zip returns HTTP 200" || fail "zip returns HTTP $HTTP"
if [ -n "$SHA_URL" ]; then
    HTTP=$(curl -sIL -o /dev/null -w '%{http_code}' "$SHA_URL")
    [ "$HTTP" = "200" ] && ok "sha256 sidecar returns HTTP 200" || fail "sha256 sidecar returns HTTP $HTTP"
fi
echo

# 2. Download zip.
echo "[2/5] Download zip"
curl -fsSL -o "$WORK/release.zip" "$ZIP_URL"
SIZE=$(wc -c <"$WORK/release.zip" | tr -d ' ')
ok "downloaded $SIZE bytes"
echo

# 3. SHA-256 verification.
echo "[3/5] SHA-256 verification"
if [ -n "$SHA_URL" ]; then
    PUBLISHED=$(curl -fsSL "$SHA_URL" | awk '{print $1}')
    ACTUAL=$(shasum -a 256 "$WORK/release.zip" | awk '{print $1}')
    if [ "$PUBLISHED" = "$ACTUAL" ]; then
        ok "SHA-256 matches ($ACTUAL)"
    else
        fail "SHA mismatch — published: $PUBLISHED, actual: $ACTUAL"
    fi
else
    warn "skipped — no .sha256 sidecar published with this release"
fi
echo

# 4. Contents audit.
echo "[4/5] Contents audit"
mkdir -p "$WORK/unpack"
unzip -q "$WORK/release.zip" -d "$WORK/unpack"
# Expect a single top-level directory.
TOP_ENTRIES=$(ls "$WORK/unpack")
TOP_COUNT=$(echo "$TOP_ENTRIES" | wc -l | tr -d ' ')
if [ "$TOP_COUNT" != "1" ]; then
    fail "expected single top-level directory in zip, found $TOP_COUNT entries"
    TOPDIR=""
else
    TOPDIR="$WORK/unpack/$TOP_ENTRIES"
    ok "single top-level directory: $TOP_ENTRIES/"
fi

if [ -n "$TOPDIR" ] && [ -d "$TOPDIR" ]; then
    for f in articles.jsonl chunks.jsonl citation_edges.csv tree.json hierarchy.json article_index.json manifest.json validation_report.json; do
        [ -e "$TOPDIR/$f" ] && ok "$f present" || fail "$f missing"
    done
    for d in articles markdown; do
        if [ -d "$TOPDIR/$d" ]; then
            COUNT=$(find "$TOPDIR/$d" -type f | wc -l | tr -d ' ')
            ok "$d/ present ($COUNT files)"
        else
            fail "$d/ missing"
        fi
    done
fi
echo

# 5. No leaked files.
echo "[5/5] No leaked files (venv/git/caches/secrets/AI-assist)"
LEAKED=$(find "$WORK/unpack" \( \
    -name '.venv' -o -name '.git' -o -name '__pycache__' -o -name '.DS_Store' \
    -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' \
    -o -name 'CLAUDE.md' -o -name 'prompt.md' -o -name '.env' -o -name '.env.*' \
    \) 2>/dev/null || true)
if [ -z "$LEAKED" ]; then
    ok "none found"
else
    fail "leaked entries:"
    printf '%s\n' "$LEAKED" | sed 's|^|        |'
fi
echo

# Manifest summary and cross-check articles.jsonl line count.
if [ -n "$TOPDIR" ] && [ -f "$TOPDIR/manifest.json" ]; then
    echo "Manifest:"
    python3 - "$TOPDIR/manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print(f"  schema_version: {m.get('schema_version')}")
print(f"  generated_at:   {m.get('generated_at')}")
totals = m.get('totals', {})
print(f"  totals: containers={totals.get('containers')} "
      f"articles_in_index={totals.get('articles_in_index')} "
      f"articles_emitted={totals.get('articles_emitted')}")
by_status = totals.get('by_status', {})
if by_status:
    parts = ', '.join(f"{k}={v}" for k, v in by_status.items())
    print(f"  by_status: {parts}")
PY
    EMITTED=$(python3 -c "import json; print(json.load(open('$TOPDIR/manifest.json'))['totals']['articles_emitted'])")
    LINES=$(wc -l <"$TOPDIR/articles.jsonl" | tr -d ' ')
    if [ "$EMITTED" = "$LINES" ]; then
        ok "articles.jsonl line count ($LINES) matches articles_emitted"
    else
        fail "articles.jsonl has $LINES lines but manifest says articles_emitted=$EMITTED"
    fi
fi
echo

if [ "$fail_count" -eq 0 ]; then
    printf '%sAll checks passed.%s\n' "$G" "$N"
    exit 0
else
    printf '%s%d check(s) failed.%s\n' "$R" "$fail_count" "$N"
    exit 1
fi
