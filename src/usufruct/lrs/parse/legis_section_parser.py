"""Parse one legis.la.gov LRS section page (``Law.aspx?d=NNNNNN``).

Page anatomy:

* ``<span id="ctl00_PageBody_LabelName">RS T:S</span>`` — canonical citation.
* ``<span id="ctl00_PageBody_LabelDocument"><div id="WPMainDoc">…</div></span>``
  — the section body.
* First ``<p>`` inside ``WPMainDoc`` matches ``^§N. Heading`` (decoded from
  ``&sect;``). The N is the section-local number only — no Title prefix.
* Final ``<p>`` matching ``^Amended by Acts`` or ``^Acts `` is the acts
  citation block. Format identical to CC; we parse with the shared
  ``parse_acts_citation_line`` from ``usufruct.parse.acts_parser``.
* Body paragraphs between the header and the acts block are the statutory
  text. Preserve subdivision letters/numbers (``A.``, ``(1)``, ``(a)``) inline.
"""
from __future__ import annotations

import html as _htmllib
import re
from dataclasses import dataclass
from typing import List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

_LABELNAME_RE = re.compile(r"^RS\s+(\d+(?:-[A-Z])?)\s*:\s*([0-9A-Za-z.\-]+)$", re.IGNORECASE)
_SECTION_LINE_RE = re.compile(r"^§\s*([0-9.]+)\.\s*(.*)$", re.DOTALL)
# Three legis-side prefixes mark a paragraph as the acts-citation block:
#   * bare ``Acts YYYY, ...`` (single enactment, common in the Civil Code).
#   * ``Amended by Acts YYYY, ...`` (enacted before 1950, accumulated amendments).
#   * ``Added by Acts YYYY, ...`` (section first appeared after the 1950
#     codification — e.g., R.S. 14:30.1 "Second degree murder", added 1973).
# The body→acts split classifies the *whole* paragraph as acts; downstream
# parsing (LRS normalizer + shared CC parser) handles the "Added by" prefix.
_ACTS_LINE_RE = re.compile(
    r"^(?:(?:Amended|Added)\s+by\s+)?Acts\s+\d{4}\s*,", re.IGNORECASE
)
_REPEALED_INLINE_RE = re.compile(r"^Repealed\b", re.IGNORECASE)
_RESERVED_RE = re.compile(r"^\[?Reserved\]?\.?$", re.IGNORECASE)


@dataclass
class ParsedRSSection:
    title_number: str
    section_number: str
    citation: str  # canonical "R.S. T:S"
    heading: Optional[str]
    text: Optional[str]
    status: str  # "active" | "repealed" | "reserved" | "blank"
    acts_citations_raw: Optional[str]


def _normalize(text: str) -> str:
    decoded = _htmllib.unescape(text or "")
    decoded = decoded.replace("\xa0", " ")
    return re.sub(r"\s+", " ", decoded).strip()


def _paragraph_text(p: Tag) -> str:
    return _normalize(p.get_text(" ", strip=True))


def _find_doc(soup: BeautifulSoup) -> Optional[Tag]:
    span = soup.find("span", id="ctl00_PageBody_LabelDocument")
    if span is None:
        return None
    inner = span.find("div", id="WPMainDoc")
    return inner if inner is not None else span


def _extract_label_name(soup: BeautifulSoup) -> Optional[tuple]:
    label = soup.find("span", id="ctl00_PageBody_LabelName")
    if label is None:
        return None
    m = _LABELNAME_RE.match(_normalize(label.get_text()))
    if not m:
        return None
    return m.group(1), m.group(2)


def parse_legis_section(html: str) -> ParsedRSSection:
    soup = BeautifulSoup(html, "lxml")
    label = _extract_label_name(soup)
    if label is None:
        raise ValueError("Could not locate LabelName on legis section page")
    title_number, section_number_from_label = label

    doc = _find_doc(soup)
    if doc is None:
        raise ValueError("Could not locate WPMainDoc on legis section page")

    paragraphs = [_paragraph_text(p) for p in doc.find_all("p")]
    paragraphs = [p for p in paragraphs if p]

    # First paragraph must match §N. Heading
    section_local_number: Optional[str] = None
    heading_inline: str = ""
    body_pool: List[str] = []
    for idx, line in enumerate(paragraphs):
        m = _SECTION_LINE_RE.match(line)
        if m:
            section_local_number = m.group(1)
            heading_inline = m.group(2).strip()
            body_pool = paragraphs[idx + 1 :]
            break

    section_number = section_local_number or section_number_from_label

    citation = f"R.S. {title_number}:{section_number}"

    # Repealed-in-place: heading slot contains "Repealed by Acts ..."
    if heading_inline and _REPEALED_INLINE_RE.match(heading_inline):
        return ParsedRSSection(
            title_number=title_number,
            section_number=section_number,
            citation=citation,
            heading=None,
            text=None,
            status="repealed",
            acts_citations_raw=heading_inline.rstrip(".") + "."
            if not heading_inline.endswith(".")
            else heading_inline,
        )

    # Reserved-only stub.
    if not body_pool and (not heading_inline or _RESERVED_RE.match(heading_inline)):
        if heading_inline and _RESERVED_RE.match(heading_inline):
            return ParsedRSSection(
                title_number=title_number,
                section_number=section_number,
                citation=citation,
                heading=None,
                text=None,
                status="reserved",
                acts_citations_raw=None,
            )

    # Body vs acts split.
    body_paras: List[str] = []
    acts_paras: List[str] = []
    for p in body_pool:
        if _ACTS_LINE_RE.match(p):
            acts_paras.append(p)
        elif acts_paras:
            # Once acts started, any further paragraphs are continuation of the
            # acts block (rare).
            acts_paras.append(p)
        else:
            body_paras.append(p)

    # Body-only repeal notice (no header content; whole section is the note).
    if not body_paras and not acts_paras and heading_inline:
        # Heading exists but no body and no acts — treat as active heading-only
        # entry (extremely rare).
        return ParsedRSSection(
            title_number=title_number,
            section_number=section_number,
            citation=citation,
            heading=heading_inline,
            text=None,
            status="active",
            acts_citations_raw=None,
        )

    if body_paras and _REPEALED_INLINE_RE.match(body_paras[0]):
        # The entire body is the repeal notice.
        notice = " ".join(body_paras).strip()
        return ParsedRSSection(
            title_number=title_number,
            section_number=section_number,
            citation=citation,
            heading=None,
            text=None,
            status="repealed",
            acts_citations_raw=notice.rstrip(".") + "."
            if not notice.endswith(".")
            else notice,
        )

    text = "\n\n".join(body_paras).strip() or None
    acts_raw = _normalize(" ".join(acts_paras)) if acts_paras else None
    heading: Optional[str] = heading_inline or None

    status = "active" if text else "blank"

    return ParsedRSSection(
        title_number=title_number,
        section_number=section_number,
        citation=citation,
        heading=heading,
        text=text,
        status=status,
        acts_citations_raw=acts_raw,
    )
