"""Parse one legis.la.gov ``Law.aspx?d=…`` article page.

Returns a ``ParsedArticle`` with article number, heading, body text, status,
and the raw + structured acts citation. Hierarchy is applied separately.

Heuristics (CSS class names are not reliable across articles):

1. Inside ``<span id="ctl00_PageBody_LabelDocument">``, walk ``<p>`` tags.
2. The first paragraph matching ``^Art\.\s*<number>\.`` is the article line;
   anything after the trailing period on that line is the heading (or — for
   repealed ranges — the repeal notice itself).
3. Subsequent paragraphs are body text. The last paragraph(s) starting with
   "Acts " or "Amended by Acts " are the acts citation block.
4. Repealed range pattern: ``Arts\.\s+\d.*\b[Rr]epealed by Acts\b`` appearing
   either in the heading slot or as the only body paragraph → status=repealed,
   text=None, the whole notice goes into ``acts_citations_raw``.
5. ``Reserved`` / ``[Reserved]`` as the body → status=reserved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..model import ArticleStatus

_ARTICLE_LINE_RE = re.compile(r"^Art\.\s+([\d.]+)\.\s*(.*)$", re.DOTALL)
_REPEALED_RANGE_RE = re.compile(
    r"^Arts?\.\s+[\d.]+(?:\s+to\s+[\d.]+)?\s+[Rr]epealed\b.*",
    re.DOTALL,
)
_RESERVED_RE = re.compile(r"^\[?Reserved\]?\.?$", re.IGNORECASE)
_ACTS_LINE_RE = re.compile(r"^(?:Amended\s+by\s+)?Acts\s+\d{4}\s*,", re.IGNORECASE)
_LABELNAME_RE = re.compile(r"^CC\s+([\d.]+)$")


@dataclass
class ParsedArticle:
    article_number: str
    heading: Optional[str]
    text: Optional[str]
    status: ArticleStatus
    acts_citations_raw: Optional[str]
    repealed_range_end: Optional[str] = None  # set when this article carries a repealed-range notice
    notes: List[str] = field(default_factory=list)


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _paragraph_lines(p: Tag) -> str:
    """Get the text of a <p>, preserving internal soft newlines as spaces."""
    return _normalize(p.get_text(" ", strip=True))


def _find_doc_paragraphs(soup: BeautifulSoup) -> List[Tag]:
    span = soup.find("span", id="ctl00_PageBody_LabelDocument")
    if span is None:
        # Fallback: try the div
        span = soup.find("div", id="ctl00_PageBody_divLaw")
        if span is None:
            return []
    return list(span.find_all("p"))


def _article_number_from_label(soup: BeautifulSoup) -> Optional[str]:
    label = soup.find("span", id="ctl00_PageBody_LabelName")
    if label is None:
        return None
    m = _LABELNAME_RE.match(label.get_text(strip=True))
    return m.group(1) if m else None


def _classify_article_line(line: str) -> Optional[Tuple[str, str]]:
    m = _ARTICLE_LINE_RE.match(line.strip())
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def parse_legis_article(html: str) -> ParsedArticle:
    soup = BeautifulSoup(html, "lxml")
    paragraphs = [_paragraph_lines(p) for p in _find_doc_paragraphs(soup)]
    paragraphs = [p for p in paragraphs if p]
    label_number = _article_number_from_label(soup)

    # Locate the article line.
    article_idx = None
    article_number: Optional[str] = None
    heading_inline: str = ""
    for i, line in enumerate(paragraphs):
        classified = _classify_article_line(line)
        if classified is not None:
            article_number, heading_inline = classified
            article_idx = i
            break

    if article_number is None:
        article_number = label_number or ""

    # Repealed-range short-circuit: the heading slot itself is the repeal note.
    if heading_inline and _REPEALED_RANGE_RE.match(heading_inline):
        end = _extract_repeal_range_end(heading_inline)
        return ParsedArticle(
            article_number=article_number,
            heading=None,
            text=None,
            status="repealed",
            acts_citations_raw=heading_inline.strip(),
            repealed_range_end=end,
        )

    # Gather body & acts paragraphs from after the article line.
    if article_idx is None:
        body_pool = paragraphs
    else:
        body_pool = paragraphs[article_idx + 1 :]

    body_paras: List[str] = []
    acts_paras: List[str] = []
    for p in body_pool:
        if _ACTS_LINE_RE.match(p) and not body_paras and acts_paras:
            # back-to-back acts paragraphs (rare) — append
            acts_paras.append(p)
        elif _ACTS_LINE_RE.match(p):
            acts_paras.append(p)
        else:
            # Once we've started collecting acts, ignore stray trailing notes
            if acts_paras:
                acts_paras.append(p)
            else:
                body_paras.append(p)

    # If body is empty but acts has content, status could be repealed (whole article = repeal note)
    body_text = "\n\n".join(body_paras).strip() if body_paras else ""
    acts_raw = " ".join(acts_paras).strip() if acts_paras else None
    acts_raw = _normalize(acts_raw) if acts_raw else None

    status: ArticleStatus = "active"
    text: Optional[str] = body_text or None
    heading: Optional[str] = heading_inline or None
    repealed_range_end: Optional[str] = None

    # Special body patterns
    if body_text and _REPEALED_RANGE_RE.match(body_text):
        status = "repealed"
        repealed_range_end = _extract_repeal_range_end(body_text)
        # The repeal note IS the citation source
        acts_raw = acts_raw or body_text
        text = None
        heading = None
    elif body_text and _RESERVED_RE.match(body_text):
        status = "reserved"
        text = None
        heading = None
    elif not body_text and acts_raw is None:
        # Whole article is empty (extremely rare).
        status = "blank"
        text = None
        heading = None

    # Heading "Repealed by ..." with no body is an explicit repeal.
    if heading and re.match(r"^Repealed\s+by\b", heading, re.IGNORECASE) and not text:
        status = "repealed"
        acts_raw = acts_raw or heading
        heading = None

    return ParsedArticle(
        article_number=article_number,
        heading=heading,
        text=text,
        status=status,
        acts_citations_raw=acts_raw,
        repealed_range_end=repealed_range_end,
    )


_RANGE_END_RE = re.compile(r"Arts?\.\s+[\d.]+\s+to\s+([\d.]+)\s+[Rr]epealed", re.IGNORECASE)


def _extract_repeal_range_end(notice: str) -> Optional[str]:
    m = _RANGE_END_RE.search(notice)
    return m.group(1) if m else None
