"""Canonical data model for the Usufruct corpus.

Two distinct record types:

* ``Container`` — a node in the LSU hierarchical TOC (book / title / chapter /
  section / subsection / paragraph / preliminary_title) with an article-number
  range.
* ``Article`` — a single Civil Code article (the corpus atomic unit) with text,
  status, acts citations, hierarchy path, and provenance metadata.

``article_number`` is ALWAYS a string. Sub-numbered articles like ``2315.1``
are independent articles, not sub-parts of ``2315``.
"""
from __future__ import annotations

import re
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

ArticleStatus = Literal["active", "repealed", "reserved", "blank"]
ContainerLevel = Literal[
    "preliminary_title",
    "book",
    "title",
    "chapter",
    "section",
    "subsection",
    "paragraph",
]
ActsRole = Literal["enactment", "amendment"]


_ARTICLE_NUMBER_RE = re.compile(r"^(\d+)(?:\.(\d+))?$")


def article_number_sort_key(num: str) -> Tuple[int, int]:
    """Return a sort key that orders article numbers as humans expect.

    ``"2315"`` -> ``(2315, 0)``; ``"2315.1"`` -> ``(2315, 1)``;
    ``"2316"`` -> ``(2316, 0)``. Falls back to ``(0, 0)`` for malformed inputs
    so they sort first and are easy to spot in validation reports.
    """
    m = _ARTICLE_NUMBER_RE.match(num.strip()) if isinstance(num, str) else None
    if not m:
        return (0, 0)
    whole = int(m.group(1))
    decimal = int(m.group(2)) if m.group(2) is not None else 0
    return (whole, decimal)


class Container(BaseModel):
    """A hierarchy container from the LSU TOC."""

    model_config = ConfigDict(extra="forbid")

    level: ContainerLevel
    number: str = Field(min_length=1)
    name: str = Field(min_length=1)
    # Empty for [Repealed]/[Reserved] containers that carry no article range.
    range_start: str = ""
    range_end: str = ""
    status: ArticleStatus = "active"
    ancestors: List["Container"] = Field(default_factory=list)

    def chain(self) -> List["Container"]:
        """Ancestors plus self, in root-to-leaf order."""
        return [*self.ancestors, self]


class HierarchyNode(BaseModel):
    """A flattened hierarchy entry for an article's path (no ancestors of its own)."""

    model_config = ConfigDict(extra="forbid")

    level: ContainerLevel
    number: str
    name: str


class ActsCitation(BaseModel):
    """One parsed entry from an article's acts citation line."""

    model_config = ConfigDict(extra="forbid")

    act_year: int
    act_number: int
    section: Optional[int] = None
    effective_date: Optional[str] = None  # ISO 8601 (YYYY-MM-DD) when parseable
    effective_date_raw: Optional[str] = None  # original substring
    role: ActsRole


class Article(BaseModel):
    """One Civil Code article — the corpus atomic unit."""

    model_config = ConfigDict(extra="forbid")

    urn: str
    article_number: str = Field(min_length=1)
    heading: Optional[str] = None
    text: Optional[str] = None
    status: ArticleStatus

    hierarchy_path: List[HierarchyNode] = Field(default_factory=list)
    breadcrumb: str = ""

    acts_citations: List[ActsCitation] = Field(default_factory=list)
    acts_citations_raw: Optional[str] = None

    source_url: Optional[str] = None
    website_law_id: Optional[int] = None
    scrape_timestamp: Optional[str] = None  # ISO 8601
    source_html_hash: Optional[str] = None  # "sha256:..." or None for synthetic blank/repealed fill records

    schema_version: str = "1.0.0"


Container.model_rebuild()
