"""Map an LRS (title, section) pair to a hierarchy path.

The LRS hierarchy is keyed by (title_number, section_sort_key). Each
container is an interval inside one Title; the deepest container whose range
covers the section wins.

This is a corpus-specific reimplementation of the CC ``hierarchy.py``
pattern. The CC version operates on integer-and-decimal article keys; the
LRS version operates on (title, integer, decimal-1, decimal-2) keys to
handle sub-decimal sections like ``14:43.1.1``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..model import Container, ContainerLevel, HierarchyNode, section_sort_key


SectionKey = Tuple[int, int, int]


def _level_value(c: Container) -> str:
    return c.level if isinstance(c.level, str) else c.level.value


def _level_label(c: Container) -> str:
    label_map = {
        ContainerLevel.TITLE.value: f"Title {c.number}",
        ContainerLevel.SUBTITLE.value: f"Subtitle {c.number}",
        ContainerLevel.CODE_PRELIMINARY_TITLE.value: "Code Preliminary Title",
        ContainerLevel.CODE_BOOK.value: f"Code Book {c.number}",
        ContainerLevel.CODE_TITLE.value: f"Code Title {c.number}",
        ContainerLevel.CHAPTER.value: f"Chapter {c.number}",
        ContainerLevel.PART.value: f"Part {c.number}",
        ContainerLevel.SUBPART.value: f"Subpart {c.number}",
        ContainerLevel.SUBGROUP.value: f"Subgroup {c.number}",
    }
    return label_map.get(_level_value(c), f"{_level_value(c)} {c.number}")


@dataclass(frozen=True)
class _Interval:
    title_number: str
    start_key: SectionKey
    end_key: SectionKey
    depth: int  # number of ancestors above the container
    container: Container
    container_chain: Tuple[Container, ...]  # root-to-leaf (including self)


def _covers(itv: _Interval, title: str, key: SectionKey) -> bool:
    return itv.title_number == title and itv.start_key <= key <= itv.end_key


class LRSHierarchyIndex:
    """Look up a section's hierarchy chain by (title_number, section_number)."""

    def __init__(self, intervals: Sequence[_Interval]):
        self._intervals: List[_Interval] = list(intervals)

    def lookup(self, title_number: str, section_number: str) -> List[HierarchyNode]:
        key = section_sort_key(section_number)
        best: Optional[_Interval] = None
        for itv in self._intervals:
            if not _covers(itv, title_number, key):
                continue
            if best is None or itv.depth > best.depth:
                best = itv
        if best is None:
            return []
        return [
            HierarchyNode(level=_level_value(c), number=c.number, name=c.name)
            for c in best.container_chain
        ]

    def breadcrumb(self, title_number: str, section_number: str) -> str:
        chain = self._chain(title_number, section_number)
        return " › ".join(_level_label(c) for c in chain)

    def _chain(self, title_number: str, section_number: str) -> Tuple[Container, ...]:
        key = section_sort_key(section_number)
        best: Optional[_Interval] = None
        for itv in self._intervals:
            if not _covers(itv, title_number, key):
                continue
            if best is None or itv.depth > best.depth:
                best = itv
        return best.container_chain if best else ()


def build_lrs_hierarchy_index(
    containers: Sequence[Container],
    section_index: Sequence[Tuple[str, str]],
) -> LRSHierarchyIndex:
    """Build an interval index for the given containers + observed sections.

    ``section_index`` is the list of all ``(title_number, section_number)``
    pairs harvested from Justia. We use it to compute each container's
    ``section_range`` by min/max over the sections that fall under it.

    Algorithm:
    1. Index containers by (title, level, number) so we can resolve parent
       chains stated as (level_value, number) tuples back to actual
       Container objects.
    2. Compute each container's section range from the sections that
       reference it in their container chain (the Justia walk stamped these
       references onto each section entry separately; here we get the same
       info indirectly by re-deriving deepest-container per section).
    3. Build the interval list.
    """

    # Index by (title, level, number) within the same title.
    by_key: Dict[Tuple[str, str, str], Container] = {}
    # For LRS the title number itself anchors each container chain — Title 1's
    # CHAPTER 1 is distinct from Title 14's CHAPTER 1. Containers are stored
    # as their level + number; the title is implicit through the chain.
    # We rely on the fact that the only Container with level=TITLE will be
    # the root of its sub-tree, and all others list its (TITLE, T) at index 0
    # of parent_chain.
    title_for_container: Dict[int, str] = {}
    for c in containers:
        if _level_value(c) == ContainerLevel.TITLE.value:
            title_for_container[id(c)] = c.number
        else:
            # First entry of parent_chain is (TITLE, number)
            t = ""
            for lvl, num in c.parent_chain:
                if lvl == ContainerLevel.TITLE.value:
                    t = num
                    break
            title_for_container[id(c)] = t
        by_key[(title_for_container[id(c)], _level_value(c), c.number)] = c

    def resolve_chain(c: Container) -> Tuple[Container, ...]:
        title = title_for_container[id(c)]
        chain: List[Container] = []
        for lvl, num in c.parent_chain:
            parent = by_key.get((title, lvl, num))
            if parent is not None:
                chain.append(parent)
        chain.append(c)
        return tuple(chain)

    return _build_intervals_from(containers, section_index, title_for_container, by_key, resolve_chain)


def _build_intervals_from(
    containers,
    section_index,
    title_for_container,
    by_key,
    resolve_chain,
) -> LRSHierarchyIndex:
    # Group observed sections by title.
    by_title: Dict[str, List[Tuple[SectionKey, str]]] = {}
    for title_num, sect_num in section_index:
        by_title.setdefault(title_num, []).append((section_sort_key(sect_num), sect_num))
    for v in by_title.values():
        v.sort(key=lambda kv: kv[0])

    intervals: List[_Interval] = []
    for c in containers:
        title = title_for_container[id(c)]
        chain = resolve_chain(c)
        depth = len(chain) - 1
        if c.section_range_start and c.section_range_end:
            start_key = section_sort_key(c.section_range_start)
            end_key = section_sort_key(c.section_range_end)
        else:
            # Fall back to looking up the min/max section in this title that
            # this container's chain covers. We do not have per-container
            # section attribution yet at this point — that comes from the
            # caller in Phase 1. So at index build time we accept missing
            # ranges and skip these containers; the lookup degrades to a
            # shallower match (the TITLE container, which always exists).
            continue
        intervals.append(
            _Interval(
                title_number=title,
                start_key=start_key,
                end_key=end_key,
                depth=depth,
                container=c,
                container_chain=chain,
            )
        )
    return LRSHierarchyIndex(intervals)


def assign_ranges_from_sections(
    containers: Sequence[Container],
    sections_with_chain: Sequence[
        Tuple[str, str, List[Tuple[str, str, str]]]
    ],
) -> None:
    """Mutate containers in-place: set section_range_start/end.

    ``sections_with_chain`` is a list of (title_number, section_number,
    container_chain), where the chain is the ordered list of
    (level_value, number, name) triples stored on each
    ``JustiaSectionEntry``.

    For each container we collect the section numbers whose chain
    contains it (by (title, level, number, name)) and set the range to
    [min, max] of those keys.
    """
    # Build a key -> container map.
    title_for_container: Dict[int, str] = {}
    for c in containers:
        if _level_value(c) == ContainerLevel.TITLE.value:
            title_for_container[id(c)] = c.number
        else:
            t = ""
            for lvl, num in c.parent_chain:
                if lvl == ContainerLevel.TITLE.value:
                    t = num
                    break
            title_for_container[id(c)] = t

    sections_under: Dict[Tuple[str, str, str, str], List[Tuple[SectionKey, str]]] = {}
    for title, section, chain in sections_with_chain:
        key = section_sort_key(section)
        for (lvl, num, name) in chain:
            sections_under.setdefault((title, lvl, num, name), []).append((key, section))

    for c in containers:
        title = title_for_container[id(c)]
        bucket = sections_under.get((title, _level_value(c), c.number, c.name))
        if not bucket:
            continue
        bucket.sort(key=lambda kv: kv[0])
        c.section_range_start = bucket[0][1]
        c.section_range_end = bucket[-1][1]
