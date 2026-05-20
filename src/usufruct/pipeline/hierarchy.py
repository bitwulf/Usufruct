"""Map article numbers to hierarchy paths using LSU containers as intervals.

For any article number, the *deepest* matching container chain wins. Two
containers can have overlapping ranges only when one nests inside the other,
so "deepest" is well-defined: we walk containers and keep the longest
ancestor chain whose range covers the query.

Article numbers outside every container range are still recorded so the
orchestrator can flag them in the validation report (typically post-2015
articles that legis.la.gov adds but LSU has not yet updated).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..model import Container, HierarchyNode, article_number_sort_key


@dataclass(frozen=True)
class _Interval:
    start_key: Tuple[int, int]
    end_key: Tuple[int, int]
    depth: int  # length of ancestor chain (excludes self)
    container: Container

    def covers(self, key: Tuple[int, int]) -> bool:
        return self.start_key <= key <= self.end_key


class HierarchyIndex:
    def __init__(self, intervals: Sequence[_Interval]):
        self._intervals = list(intervals)

    def lookup(self, article_number: str) -> List[HierarchyNode]:
        """Deepest matching container chain → flattened ``HierarchyNode`` list."""
        key = article_number_sort_key(article_number)
        best: Optional[_Interval] = None
        for itv in self._intervals:
            if not itv.covers(key):
                continue
            if best is None or itv.depth > best.depth:
                best = itv
        if best is None:
            return []
        chain = best.container.chain()
        return [
            HierarchyNode(level=c.level, number=c.number, name=c.name)
            for c in chain
        ]

    def nearest(self, article_number: str) -> List[HierarchyNode]:
        """Fallback for articles outside every range: borrow the nearest by numeric distance."""
        key = article_number_sort_key(article_number)
        if not self._intervals:
            return []
        # Score by midpoint distance to a leaf interval
        leaves = [i for i in self._intervals if i.depth >= 1]
        candidates = leaves or self._intervals
        def distance(itv: _Interval) -> int:
            if itv.covers(key):
                return 0
            if key < itv.start_key:
                return itv.start_key[0] - key[0]
            return key[0] - itv.end_key[0]
        # Prefer deepest among the nearest
        candidates_sorted = sorted(candidates, key=lambda i: (distance(i), -i.depth))
        chosen = candidates_sorted[0]
        return [
            HierarchyNode(level=c.level, number=c.number, name=c.name)
            for c in chosen.container.chain()
        ]

    def all_article_numbers(self) -> List[str]:
        """Every integer article number covered by at least one leaf container.

        Used by the orchestrator to detect blank holes.
        """
        seen: set = set()
        # Use leaf containers (no descendant of theirs in the list). For
        # simplicity, treat every container as leaf-or-not by checking if any
        # other container has it as an ancestor.
        all_containers = [i.container for i in self._intervals]
        leaf_set = set(range(len(all_containers)))
        for idx, c in enumerate(all_containers):
            for anc in c.ancestors:
                # find ancestor index
                for j, other in enumerate(all_containers):
                    if other is anc:
                        leaf_set.discard(j)
                        break
        out: List[str] = []
        for idx in leaf_set:
            c = all_containers[idx]
            if not c.range_start or not c.range_end:
                continue
            start_key = article_number_sort_key(c.range_start)
            end_key = article_number_sort_key(c.range_end)
            # Iterate integer parts only (we don't synthesize decimals)
            for n in range(start_key[0], end_key[0] + 1):
                key = str(n)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        out.sort(key=article_number_sort_key)
        return out


def build_hierarchy_index(containers: Sequence[Container]) -> HierarchyIndex:
    intervals: List[_Interval] = []
    for c in containers:
        if not c.range_start or not c.range_end:
            continue
        intervals.append(
            _Interval(
                start_key=article_number_sort_key(c.range_start),
                end_key=article_number_sort_key(c.range_end),
                depth=len(c.ancestors),
                container=c,
            )
        )
    return HierarchyIndex(intervals)
