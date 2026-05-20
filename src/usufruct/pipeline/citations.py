"""Citation-edges extractor — Phase 4 derived artifact.

Walks every active article's body text and emits a row per cross-reference to
another Civil Code article. Compound references ("Articles 102 and 103",
"Arts. 60 to 85") expand into one row per referenced article number.

The regex is deliberately narrow: we only match the Article/Articles/Art./Arts.
prefix followed by article-number tokens. We do NOT try to resolve abbreviated
references in the rest of the corpus (e.g., "this Article") because they
don't carry an external referent.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from ..model import Article, article_number_sort_key

# Prefix token: Article, Articles, Art., Arts.
_PREFIX_RE = re.compile(r"\b(Articles?|Arts?\.)(?=\s+\d)")
# Number token (one article number, optionally with .N decimal)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# Connector words that continue a compound list of article numbers
_CONNECTOR_RE = re.compile(
    r"\s*(?:,\s*(?:and\s+|or\s+|through\s+|to\s+)?|\s+(?:and|or|to|through)\s+)"
)


@dataclass
class CitationEdge:
    src_urn: str
    src_article: str
    dst_article: str
    raw_match: str
    char_offset: int


def _expand_compound(text: str, start: int) -> List[tuple]:
    """Starting just past a "Article(s)/Art(s)." prefix, scan a compound list.

    Returns ``[(dst_article, absolute_offset), ...]``. Stops at the first
    position that isn't a number or a recognised connector.
    """
    out: List[tuple] = []
    pos = start
    # Skip whitespace after the prefix
    while pos < len(text) and text[pos].isspace():
        pos += 1
    while pos < len(text):
        m = _NUMBER_RE.match(text, pos)
        if not m:
            break
        out.append((m.group(0), m.start()))
        pos = m.end()
        c = _CONNECTOR_RE.match(text, pos)
        if not c:
            break
        pos = c.end()
    return out


def extract_edges(article: Article) -> List[CitationEdge]:
    if article.status != "active" or not article.text:
        return []
    edges: List[CitationEdge] = []
    for prefix_match in _PREFIX_RE.finditer(article.text):
        prefix_end = prefix_match.end()
        for dst, offset in _expand_compound(article.text, prefix_end):
            edges.append(
                CitationEdge(
                    src_urn=article.urn,
                    src_article=article.article_number,
                    dst_article=dst,
                    raw_match=f"{prefix_match.group(0)} {dst}",
                    char_offset=offset,
                )
            )
    return edges


def collect_edges(articles: Iterable[Article]) -> List[CitationEdge]:
    out: List[CitationEdge] = []
    for art in articles:
        out.extend(extract_edges(art))
    out.sort(
        key=lambda e: (
            article_number_sort_key(e.src_article),
            e.char_offset,
            article_number_sort_key(e.dst_article),
        )
    )
    return out


def write_csv(out_path: Path, edges: Sequence[CitationEdge]) -> int:
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["src_urn", "src_article", "dst_article", "raw_match", "char_offset"]
        )
        for e in edges:
            writer.writerow(
                [e.src_urn, e.src_article, e.dst_article, e.raw_match, e.char_offset]
            )
    return len(edges)
