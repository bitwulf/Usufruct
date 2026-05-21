"""LRS canonical data model.

Parallel to ``usufruct.model.schema`` but for the Louisiana Revised Statutes
corpus. Defines:

* ``RSSection`` — a single Revised Statutes section, the corpus atomic unit
  (analogous to ``Article`` in the Civil Code).
* ``Container`` + ``ContainerLevel`` — LRS hierarchy nodes (Title → Subtitle →
  Chapter → Part → Subpart → Subgroup). Different shape from CC because the
  hierarchy is keyed on Title within the section identifier (``T:S``).

``HierarchyNode`` and ``ActsCitation`` are reused unchanged from the CC schema
(see ``usufruct.model.schema``) — both shapes are corpus-agnostic.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ...model import ActsCitation

RSSectionStatus = Literal["active", "repealed", "reserved", "blank"]


_SECTION_NUMBER_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


def section_sort_key(num: str) -> Tuple[int, int, int]:
    """Order section numbers as humans expect.

    Examples:
        ``"30"``   -> ``(30, 0, 0)``
        ``"30.1"`` -> ``(30, 1, 0)``
        ``"43.1.1"`` -> ``(43, 1, 1)``
        ``"1:30"`` and other malformed inputs -> ``(0, 0, 0)`` so they sort
        first and are easy to spot.
    """
    m = _SECTION_NUMBER_RE.match(num.strip()) if isinstance(num, str) else None
    if not m:
        return (0, 0, 0)
    whole = int(m.group(1))
    dec1 = int(m.group(2)) if m.group(2) is not None else 0
    dec2 = int(m.group(3)) if m.group(3) is not None else 0
    return (whole, dec1, dec2)


class ContainerLevel(str, Enum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    CHAPTER = "chapter"
    PART = "part"
    SUBPART = "subpart"
    SUBGROUP = "subgroup"


class HierarchyNode(BaseModel):
    """A flattened hierarchy entry for an LRS section's path.

    Distinct from ``usufruct.model.HierarchyNode`` because the CC version's
    ``level`` is a Literal restricted to CC's container levels. LRS-side
    levels (``part``, ``subpart``, ``subgroup``, ``subtitle``) wouldn't
    validate.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    level: ContainerLevel
    number: str = Field(min_length=1)
    name: str


class Container(BaseModel):
    """An LRS hierarchy container."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    level: ContainerLevel
    number: str = Field(min_length=1)
    name: str
    # Parent chain as (level, number) tuples, root-first, *excluding* self.
    parent_chain: List[Tuple[str, str]] = Field(default_factory=list)
    # (start_section, end_section) — both inclusive. ``None`` when the
    # container has no direct sections (only subcontainers) or when the range
    # has not yet been computed.
    section_range_start: Optional[str] = None
    section_range_end: Optional[str] = None
    is_repealed: bool = False
    is_reserved: bool = False


class RSSection(BaseModel):
    """One Revised Statutes section — the corpus atomic unit."""

    model_config = ConfigDict(extra="forbid")

    urn: str
    title_number: str = Field(min_length=1)
    section_number: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    heading: Optional[str] = None
    text: Optional[str] = None
    status: RSSectionStatus

    hierarchy_path: List["HierarchyNode"] = Field(default_factory=list)
    breadcrumb: str = ""

    acts_citations: List[ActsCitation] = Field(default_factory=list)
    acts_citations_raw: Optional[str] = None

    source_url: Optional[str] = None
    website_law_id: Optional[int] = None
    scrape_timestamp: Optional[str] = None
    source_html_hash: Optional[str] = None

    schema_version: str = "1.0.0"
