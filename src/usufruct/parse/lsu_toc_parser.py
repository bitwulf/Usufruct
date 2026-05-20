"""Parse the LSU CCLS TOC page into a flat list of ``Container`` records.

The TOC is a nested ``<ul>/<li>`` tree. Each item carries:

    <a><b>{LABEL}</b>{REST}</a>

where LABEL is one of:

    Preliminary Title | Book {ROMAN} | Title {ROMAN}[-A] |
    Chapter {N} | Section {N} | Subsection {LETTER} | §{N}

and REST is a description plus an article-number range "(Art. X to Y)" or a
status marker "[Repealed]" / "[Reserved]" appended to the description.

Strategy: depth-first traversal of the outermost ``<ul>``. The recursion
depth is the hierarchy depth — we do not need to track indentation by hand.
"""
from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..model import Container, ContainerLevel

# Use Optional[...] in annotations to keep the file Python 3.9 compatible.

_RANGE_RE = re.compile(r"\(Art\.\s+([\w.\-]+)\s+to\s+([\w.\-]+)\)", re.IGNORECASE)
_SINGLE_RE = re.compile(r"\(Art\.\s+([\w.\-]+)\)", re.IGNORECASE)

_LEVEL_PATTERNS = [
    (re.compile(r"^Preliminary Title$", re.IGNORECASE), "preliminary_title"),
    (re.compile(r"^Book\s+([IVXLCDM]+)$", re.IGNORECASE), "book"),
    (re.compile(r"^Title\s+([IVXLCDM]+(?:-[A-Z])?)$", re.IGNORECASE), "title"),
    (re.compile(r"^Chapter\s+([\dA-Z]+)$", re.IGNORECASE), "chapter"),
    (re.compile(r"^Section\s+([\dA-Z]+)$", re.IGNORECASE), "section"),
    (re.compile(r"^Subsection\s+([A-Z]+)$", re.IGNORECASE), "subsection"),
    (re.compile(r"^§\s*(\d+)$"), "paragraph"),
]


def _normalize(s: str) -> str:
    """Strip NBSP and collapse whitespace."""
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _classify_label(label: str) -> Optional[tuple]:
    label = _normalize(label)
    for pat, lvl in _LEVEL_PATTERNS:
        m = pat.match(label)
        if m:
            number = m.group(1) if m.lastindex else label
            return lvl, number
    return None


def _split_name_and_range(rest: str) -> tuple:
    """Return (name, range_start, range_end, status)."""
    rest = _normalize(rest)
    status: str = "active"

    # detect status markers anywhere in the description
    if re.search(r"\[Repealed\]", rest, re.IGNORECASE):
        status = "repealed"
    elif re.search(r"\[Reserved\]", rest, re.IGNORECASE):
        status = "reserved"

    range_match = _RANGE_RE.search(rest)
    if range_match:
        range_start = range_match.group(1)
        range_end = range_match.group(2)
        name = rest[: range_match.start()].strip(" .-—:")
    else:
        single_match = _SINGLE_RE.search(rest)
        if single_match:
            range_start = single_match.group(1)
            range_end = single_match.group(1)
            name = rest[: single_match.start()].strip(" .-—:")
        else:
            range_start = ""
            range_end = ""
            name = rest

    # remove dangling status marker from the displayed name
    name = re.sub(r"\s*\[(Repealed|Reserved|Blank)\]\s*$", "", name, flags=re.IGNORECASE).strip()
    return name, range_start, range_end, status


def _li_to_container(li: Tag, ancestors: List[Container]) -> Optional[Container]:
    a = li.find("a", recursive=False)
    if a is None:
        a = li.find("a")
    if a is None:
        return None
    b = a.find("b")
    if b is None:
        return None
    label = b.get_text(" ", strip=True)
    classified = _classify_label(label)
    if classified is None:
        return None
    level, number = classified

    # Build "rest": the text inside <a> after the <b>
    rest_parts: List[str] = []
    for sib in b.next_siblings:
        if getattr(sib, "name", None) is None:
            rest_parts.append(str(sib))
        else:
            rest_parts.append(sib.get_text(" ", strip=True))
    rest = _normalize("".join(rest_parts))

    name, range_start, range_end, status = _split_name_and_range(rest)

    # Containers without explicit range can borrow from a single child or the
    # parent — but parents always have ranges in this TOC, so we keep empty
    # strings and let the orchestrator's validation catch any gaps.
    return Container(
        level=level,  # type: ignore[arg-type]
        number=str(number),
        name=name or label,
        range_start=range_start,
        range_end=range_end,
        status=status,  # type: ignore[arg-type]
        ancestors=list(ancestors),
    )


def _walk_ul(ul: Tag, ancestors: List[Container], out: List[Container]) -> None:
    for li in ul.find_all("li", recursive=False):
        container = _li_to_container(li, ancestors)
        if container is None:
            # skip nodes we can't classify but still descend in case children exist
            child_ul = li.find("ul", recursive=False)
            if child_ul is not None:
                _walk_ul(child_ul, ancestors, out)
            continue
        out.append(container)
        child_ul = li.find("ul", recursive=False)
        if child_ul is not None:
            _walk_ul(child_ul, [*ancestors, container], out)


def _find_top_ul(soup: BeautifulSoup) -> Optional[Tag]:
    """The outermost ``<ul>`` whose chain has no ``<li>`` ancestor."""
    for ul in soup.find_all("ul"):
        if ul.find_parent("ul") is None and ul.find_parent("li") is None:
            # Has at least one direct <li> with an <a><b>...</b></a> structure
            for li in ul.find_all("li", recursive=False):
                a = li.find("a")
                if a and a.find("b"):
                    return ul
    return None


def parse_lsu_toc(html: str) -> List[Container]:
    soup = BeautifulSoup(html, "lxml")
    top = _find_top_ul(soup)
    if top is None:
        return []
    out: List[Container] = []
    _walk_ul(top, [], out)

    # Backfill empty ranges by widest child range when possible
    for c in out:
        if c.range_start and c.range_end:
            continue
        children_starts = [
            o for o in out if c in o.ancestors
        ]
        if children_starts:
            starts = [o.range_start for o in children_starts if o.range_start]
            ends = [o.range_end for o in children_starts if o.range_end]
            if starts and ends:
                from ..model import article_number_sort_key

                c.range_start = min(starts, key=article_number_sort_key)
                c.range_end = max(ends, key=article_number_sort_key)
    return out
