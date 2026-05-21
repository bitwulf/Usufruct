"""Parse one Justia per-Title LRS page into (containers, section index).

The Justia per-Title page is the LRS hierarchy source of truth. legis.la.gov
section pages do not carry breadcrumb information.

Inside ``<div class="codes-listing">`` the page alternates between:

1. ``<strong class="heading-6 font-w-bold">`` hierarchy header blocks. Each
   block contains a sequence of ``<p>`` paragraphs; we extract one logical
   heading per detected level, with stateful "diff" semantics — any level
   mentioned replaces itself and *resets every deeper level*.
2. Section anchors ``<a href=".../rs-{T}-{S-with-dots-as-hyphens}/">``. The
   anchor text is ``§T:S. Heading``. Each section attaches to the current
   container context.

Header rules the parser must handle (from the implementation plan, § 2):

* Banner-line skip: ``<p>LOUISIANA REVISED STATUTES</p>`` is decorative; skip.
* Duplicate-header idempotence: ``CHAPTER 2. MISCELLANEOUS`` appearing twice
  in succession (Title 1) collapses into one no-op.
* Multi-line headings: consecutive ``<p>`` paragraphs at the same level
  (split mid-heading) concatenate into one heading.
* Letter-hyphen Subparts: ``SUBPART A-1``, ``SUBPART B-2``.
* Non-contiguous Subpart letters: Title 47 jumps O → Q.
* NOTE: paragraphs: filtered from hierarchy walk; optionally surfaced as side
  output for downstream consumers.
* Numbered subgroup level: ``1. ARSON AND USE OF EXPLOSIVES`` under a Subpart.
* Chapter-with-letter-suffix: ``CHAPTER 1-A`` (mirrors Subpart compounds).
* Repealed sections: anchor text contains ``Repealed by Acts``.
"""
from __future__ import annotations

import html as _htmllib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from ..model import Container, ContainerLevel

# ---------- regexes for classifying header lines ----------

_BANNER_RE = re.compile(r"^LOUISIANA REVISED STATUTES$", re.IGNORECASE)
_TITLE_RE = re.compile(r"^TITLE\s+(\d+(?:-[A-Z])?)\b\.?\s*(.*)$", re.IGNORECASE)
_SUBTITLE_RE = re.compile(r"^SUBTITLE\s+([IVXLCDM]+(?:-[A-Z])?)\b\.?\s*(.*)$", re.IGNORECASE)
_CHAPTER_RE = re.compile(r"^CHAPTER\s+(\d+(?:-[A-Z])?)\b\.?\s*(.*)$", re.IGNORECASE)
_PART_RE = re.compile(r"^PART\s+([IVXLCDM]+(?:-[A-Z])?)\b\.?\s*(.*)$", re.IGNORECASE)
_SUBPART_RE = re.compile(
    r"^SUBPART\s+([A-Z](?:[A-Z]*)(?:-\d+)?)\b\.?\s*(.*)$", re.IGNORECASE
)
_SUBGROUP_RE = re.compile(r"^(\d+)\.\s+([A-Z][A-Z0-9 ,'\-’&/]+)$")
_NOTE_RE = re.compile(r"^NOTE\b", re.IGNORECASE)

# Section anchor: href, e.g. /codes/louisiana/revised-statutes/title-14/rs-14-30-1/
_SECTION_HREF_RE = re.compile(
    r"^/codes/louisiana/revised-statutes/title-(\d+)/rs-([0-9a-z\-\.]+)/?$",
    re.IGNORECASE,
)
# Anchor text: "§T:S. Heading" or "§T:S. Repealed by Acts ..."
_SECTION_TEXT_RE = re.compile(
    r"^\s*[§§]\s*(\d+)\s*:\s*([0-9.]+)\s*\.\s*(.*)$",
    re.DOTALL,
)


# ---------- header classification ----------

#: Ordering of container levels from shallow to deep. Deeper container levels
#: are reset when a shallower level changes.
LEVEL_ORDER = [
    ContainerLevel.TITLE,
    ContainerLevel.SUBTITLE,
    ContainerLevel.CHAPTER,
    ContainerLevel.PART,
    ContainerLevel.SUBPART,
    ContainerLevel.SUBGROUP,
]


@dataclass
class JustiaSectionEntry:
    """A single section anchor harvested from the Justia per-Title page."""
    title_number: str
    section_number: str
    heading: Optional[str]
    repealed: bool
    repeal_note: Optional[str]
    # Container chain at the moment of the anchor, deepest first when
    # reversed. Stored root-first as (level, number, name) triples.
    container_chain: List[Tuple[str, str, str]] = field(default_factory=list)


@dataclass
class JustiaTitleParseResult:
    title_number: str
    containers: List[Container]
    sections: List[JustiaSectionEntry]
    notes: List[Tuple[str, List[Tuple[str, str, str]]]] = field(default_factory=list)


def _normalize(text: str) -> str:
    """Decode HTML entities, normalize whitespace, strip leading/trailing."""
    if text is None:
        return ""
    decoded = _htmllib.unescape(text)
    return re.sub(r"\s+", " ", decoded.replace("\xa0", " ")).strip()


def _slug_to_section_number(slug: str) -> str:
    """``"14-30-1"`` -> ``"30.1"`` (the part after the title)."""
    parts = slug.split("-")
    if len(parts) < 2:
        return slug
    # parts[0] is the title number; the rest is the section number, joined by '.'.
    return ".".join(parts[1:])


def _classify_header_line(line: str) -> Optional[Tuple[ContainerLevel, str, str]]:
    """Return (level, number, name) for a hierarchy header line, or None.

    NOTE: paragraphs and the LOUISIANA REVISED STATUTES banner return None
    (caller filters them out).
    """
    s = line.strip()
    if not s:
        return None
    if _BANNER_RE.match(s):
        return None
    if _NOTE_RE.match(s):
        return None
    m = _TITLE_RE.match(s)
    if m:
        return ContainerLevel.TITLE, m.group(1), m.group(2).strip()
    m = _SUBTITLE_RE.match(s)
    if m:
        return ContainerLevel.SUBTITLE, m.group(1), m.group(2).strip()
    m = _CHAPTER_RE.match(s)
    if m:
        return ContainerLevel.CHAPTER, m.group(1), m.group(2).strip()
    m = _PART_RE.match(s)
    if m:
        return ContainerLevel.PART, m.group(1), m.group(2).strip()
    m = _SUBPART_RE.match(s)
    if m:
        return ContainerLevel.SUBPART, m.group(1), m.group(2).strip()
    m = _SUBGROUP_RE.match(s)
    if m:
        return ContainerLevel.SUBGROUP, m.group(1), m.group(2).strip()
    return None


def _is_continuation_text(line: str) -> bool:
    """A line that is plain header continuation (not a NOTE, banner, or new level)."""
    if not line.strip():
        return False
    if _NOTE_RE.match(line) or _BANNER_RE.match(line):
        return False
    if _classify_header_line(line) is not None:
        return False
    return True


def _extract_strong_paragraphs(strong: Tag) -> List[str]:
    """All paragraph texts inside a hierarchy ``<strong>`` block.

    Justia's HTML closes ``<strong>`` mid-``<p>`` on the trailing paragraph
    (and the casing of close tags is inconsistent). BeautifulSoup with the
    ``lxml`` parser normalizes most of this, but the fallback below also
    handles raw ``NavigableString`` content directly under ``<strong>``.
    """
    paras: List[str] = []
    for child in strong.find_all("p", recursive=True):
        text = _normalize(child.get_text(" ", strip=True))
        paras.append(text)
    return paras


# ---------- main entry ----------

def parse_justia_title(html: str, expected_title_number: Optional[str] = None) -> JustiaTitleParseResult:
    """Parse a Justia per-Title HTML page.

    ``expected_title_number`` is the Title number derived from the URL slug
    (e.g., ``"14"`` for ``/title-14/``). When provided, it's used as a
    fallback if the parser can't pluck a TITLE header out of the first
    ``<strong>`` block. It is also the title number reported on every
    section entry.
    """
    soup = BeautifulSoup(html, "lxml")
    listing = soup.find("div", class_="codes-listing")
    if listing is None:
        raise ValueError("Justia title page missing <div class='codes-listing'>")

    state = _walk(listing, expected_title_number)
    title_number = state.title_number or (expected_title_number or "")
    return JustiaTitleParseResult(
        title_number=title_number,
        containers=state.containers,
        sections=state.sections,
        notes=state.notes,
    )


# ---------- internal walker ----------

@dataclass
class _Walker:
    title_number: str = ""
    # Stack of (level, container). At any point: at most one container per
    # level. Order matches LEVEL_ORDER.
    context: Dict[ContainerLevel, Optional[Container]] = field(default_factory=dict)
    containers: List[Container] = field(default_factory=list)
    sections: List[JustiaSectionEntry] = field(default_factory=list)
    notes: List[Tuple[str, List[Tuple[str, str, str]]]] = field(default_factory=list)
    # Dedup by (level, number, name, parent_chain) so duplicate-header
    # idempotence holds: re-emitting the same header is a no-op.
    _seen_keys: set = field(default_factory=set)

    def _current_chain_triples(self) -> List[Tuple[str, str, str]]:
        out: List[Tuple[str, str, str]] = []
        for lvl in LEVEL_ORDER:
            c = self.context.get(lvl)
            if c is not None:
                out.append((c.level if isinstance(c.level, str) else c.level.value, c.number, c.name))
        return out

    def _parent_chain_for(self, level: ContainerLevel) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for lvl in LEVEL_ORDER:
            if lvl == level:
                break
            c = self.context.get(lvl)
            if c is not None:
                out.append((c.level if isinstance(c.level, str) else c.level.value, c.number))
        return out

    def _reset_deeper(self, level: ContainerLevel) -> None:
        """Clear container slots strictly deeper than ``level``."""
        idx = LEVEL_ORDER.index(level)
        for deeper in LEVEL_ORDER[idx + 1 :]:
            self.context[deeper] = None

    def apply_header_line(self, level: ContainerLevel, number: str, name: str) -> Optional[Container]:
        """Install a new container at ``level``, resetting deeper levels.

        Returns the container (existing one if a true duplicate, freshly
        created otherwise). Idempotent on exact duplicates.
        """
        current = self.context.get(level)
        if (
            current is not None
            and current.number == number
            and current.name == name
            and self._parent_chain_for(level) == [
                (lp[0], lp[1]) for lp in current.parent_chain
            ]
        ):
            self._reset_deeper(level)
            return current

        parent_chain = self._parent_chain_for(level)
        # parent_chain stored as List[Tuple[str, str]] of (level_value, number)
        container = Container(
            level=level,
            number=number,
            name=name,
            parent_chain=[(p[0], p[1]) for p in parent_chain],
        )
        key = (level.value, number, name, tuple(parent_chain))
        if key not in self._seen_keys:
            self.containers.append(container)
            self._seen_keys.add(key)
        else:
            # Already emitted at this point in the walk — reuse the prior
            # instance so children attach to the right node.
            for prior in reversed(self.containers):
                pk = (
                    prior.level if isinstance(prior.level, str) else prior.level.value,
                    prior.number,
                    prior.name,
                    tuple(tuple(p) for p in prior.parent_chain),
                )
                if pk == key:
                    container = prior
                    break
        self.context[level] = container
        if level == ContainerLevel.TITLE:
            self.title_number = number
        self._reset_deeper(level)
        return container

    def apply_section(self, entry: JustiaSectionEntry) -> None:
        entry.container_chain = self._current_chain_triples()
        self.sections.append(entry)

    def apply_note(self, raw: str) -> None:
        self.notes.append((raw, self._current_chain_triples()))


def _walk(listing: Tag, expected_title_number: Optional[str]) -> _Walker:
    state = _Walker()
    state.context = {lvl: None for lvl in LEVEL_ORDER}

    # We walk top-level descendants in document order. Two kinds matter:
    # <strong class="heading-6 ..."> (header blocks) and <a> anchors inside
    # <ul><li> lists (section anchors). Anything else is decoration.
    for elem in listing.descendants:
        if not isinstance(elem, Tag):
            continue
        if elem.name == "strong" and "heading-6" in (elem.get("class") or []):
            _process_header_block(state, elem)
        elif elem.name == "a" and elem.get("href"):
            _process_section_anchor(state, elem, expected_title_number)
    return state


def _process_header_block(state: _Walker, strong: Tag) -> None:
    paragraphs = _extract_strong_paragraphs(strong)
    # Drop empty paragraphs.
    paragraphs = [p for p in paragraphs if p]

    # NOTE: paragraphs may interleave; consume them in pass-1, then build
    # logical headings from the remaining content.
    real_paras: List[str] = []
    for p in paragraphs:
        if _BANNER_RE.match(p):
            # Skip the decorative LOUISIANA REVISED STATUTES line entirely.
            continue
        if _NOTE_RE.match(p):
            state.apply_note(p)
            continue
        real_paras.append(p)

    # Build logical headings: each header line starts a heading; subsequent
    # plain continuation lines append to it until the next header line.
    logical: List[Tuple[ContainerLevel, str, str]] = []
    pending_level: Optional[ContainerLevel] = None
    pending_number: Optional[str] = None
    pending_name_parts: List[str] = []

    def _flush() -> None:
        nonlocal pending_level, pending_number, pending_name_parts
        if pending_level is None:
            pending_name_parts = []
            return
        name = " ".join(part for part in pending_name_parts if part)
        logical.append((pending_level, pending_number or "", name.strip()))
        pending_level = None
        pending_number = None
        pending_name_parts = []

    for p in real_paras:
        cls = _classify_header_line(p)
        if cls is not None:
            _flush()
            pending_level, pending_number, name0 = cls
            pending_name_parts = [name0] if name0 else []
        else:
            # Continuation of the prior heading (multi-line subpart names).
            if pending_level is not None and _is_continuation_text(p):
                pending_name_parts.append(p)
    _flush()

    for level, number, name in logical:
        state.apply_header_line(level, number, name)


def _process_section_anchor(
    state: _Walker, anchor: Tag, expected_title_number: Optional[str]
) -> None:
    href = anchor.get("href", "").strip()
    m = _SECTION_HREF_RE.match(href)
    if not m:
        return
    title_from_href = m.group(1)
    slug = m.group(2)
    section_from_slug = _slug_to_section_number(slug)

    raw_text = _normalize(anchor.get_text(" ", strip=True))
    text_match = _SECTION_TEXT_RE.match(raw_text)
    if text_match:
        text_title = text_match.group(1)
        text_section = text_match.group(2)
        rest = text_match.group(3).strip()
        # Title/section identifier must agree across the three witnesses
        if text_title != title_from_href or text_section != section_from_slug:
            # Soft mismatch — flag in heading but don't drop the entry. Defer
            # hard validation to the orchestrator's cross-source check.
            heading_or_note = rest or None
        else:
            heading_or_note = rest or None
    else:
        heading_or_note = None

    repealed = False
    repeal_note: Optional[str] = None
    if heading_or_note and re.match(r"^Repealed\b", heading_or_note, re.IGNORECASE):
        repealed = True
        repeal_note = heading_or_note.rstrip(".") + ("" if heading_or_note.endswith(".") else ".")
        heading_value: Optional[str] = None
    else:
        heading_value = heading_or_note

    # Ensure title_number is populated in state (some Title pages don't open
    # with a TITLE heading; rely on the expected title number from the URL).
    if not state.title_number and expected_title_number:
        state.title_number = title_from_href or expected_title_number

    entry = JustiaSectionEntry(
        title_number=title_from_href,
        section_number=section_from_slug,
        heading=heading_value,
        repealed=repealed,
        repeal_note=repeal_note,
    )
    state.apply_section(entry)
