"""RAG-ready chunks JSONL — Phase 4 derived artifact.

One chunk per active article. Skips blank/repealed records because they carry
no body text and would just be noise in a retrieval index. Each chunk carries
breadcrumb context (for system-prompt framing) and prev/next article numbers
(for downstream re-expansion to neighboring context if desired).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .. import SCHEMA_VERSION
from ..model import Article, article_number_sort_key


def _neighbors(
    active_numbers: Sequence[str], number: str
) -> tuple[Optional[str], Optional[str]]:
    idx = active_numbers.index(number)
    prev_n = active_numbers[idx - 1] if idx > 0 else None
    next_n = active_numbers[idx + 1] if idx + 1 < len(active_numbers) else None
    return prev_n, next_n


def build_chunks(articles: Iterable[Article]) -> List[Dict]:
    active = [a for a in articles if a.status == "active" and a.text]
    active.sort(key=lambda a: article_number_sort_key(a.article_number))
    numbers = [a.article_number for a in active]

    out: List[Dict] = []
    for art in active:
        prev_n, next_n = _neighbors(numbers, art.article_number)
        out.append(
            {
                "chunk_id": f"art:{art.article_number}",
                "urn": art.urn,
                "article_number": art.article_number,
                "heading": art.heading,
                "breadcrumb": art.breadcrumb,
                "text": art.text,
                "prev_article": prev_n,
                "next_article": next_n,
                "source_url": art.source_url,
                "schema_version": SCHEMA_VERSION,
            }
        )
    return out


def write_chunks(out_path: Path, articles: Iterable[Article]) -> int:
    chunks = build_chunks(articles)
    with out_path.open("w") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return len(chunks)
