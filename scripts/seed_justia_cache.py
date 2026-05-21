#!/usr/bin/env python3
"""Bootstrap the SHA-keyed HTTP cache with pre-downloaded Justia LRS HTMLs.

Justia is Cloudflare-protected and brittle to scrape. The 54 per-Title HTML
pages plus the LRS root index were captured manually and stored under
``lars-test/html/`` as a working backup. This script copies each file into
the ``CachedClient`` cache at ``data/raw/{sha[:2]}/{sha}.html`` and registers
its source URL in ``data/raw/index.json``. After it runs, ``CachedClient.get``
for any Justia LRS URL returns a cache hit and the LRS Phase 1 walk
completes with **zero** network traffic to Justia.

Re-running is idempotent: existing entries are reused and the index is
merged with any pre-existing CC pipeline entries.

Usage:
    .venv/bin/python scripts/seed_justia_cache.py [--data-dir data]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LARS_DIR = REPO_ROOT / "lars-test" / "html"

JUSTIA_ROOT_URL = "https://law.justia.com/codes/louisiana/revised-statutes/"
JUSTIA_TITLE_URL_TEMPLATE = (
    "https://law.justia.com/codes/louisiana/revised-statutes/title-{title}/"
)

_TITLE_RE = re.compile(r"^title-(\d+)\.html$")


def url_for_fixture(filename: str) -> str | None:
    """Map a fixture filename to its source Justia URL."""
    if filename == "index.html":
        return JUSTIA_ROOT_URL
    m = _TITLE_RE.match(filename)
    if m:
        return JUSTIA_TITLE_URL_TEMPLATE.format(title=m.group(1))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data", help="Cache root (default: data)")
    ap.add_argument(
        "--lars-dir",
        default=str(DEFAULT_LARS_DIR),
        help=f"Justia fixture dir (default: {DEFAULT_LARS_DIR})",
    )
    args = ap.parse_args()

    data_root = Path(args.data_dir).resolve()
    cache_root = data_root / "raw"
    index_path = cache_root / "index.json"
    lars_dir = Path(args.lars_dir).resolve()

    if not lars_dir.is_dir():
        print(f"error: {lars_dir} not found or not a directory", file=sys.stderr)
        return 2

    cache_root.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        index = json.loads(index_path.read_text())
    else:
        index = {}

    added = 0
    updated = 0
    unchanged = 0
    skipped: list[str] = []

    for html_file in sorted(lars_dir.glob("*.html")):
        url = url_for_fixture(html_file.name)
        if url is None:
            skipped.append(html_file.name)
            continue
        text = html_file.read_text()
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cache_path = cache_root / sha[:2] / f"{sha}.html"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not cache_path.exists():
            cache_path.write_text(text)
        prior = index.get(url)
        if prior == sha:
            unchanged += 1
        elif prior is None:
            index[url] = sha
            added += 1
        else:
            index[url] = sha
            updated += 1

    index_path.write_text(json.dumps(index, indent=2, sort_keys=True))

    print(
        f"Justia cache seeded: {added} new, {updated} updated, "
        f"{unchanged} unchanged.",
        file=sys.stderr,
    )
    if skipped:
        print(f"Skipped {len(skipped)} unrecognized file(s): {skipped[:5]}...", file=sys.stderr)
    print(f"Cache index: {index_path} ({len(index)} total entries)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
