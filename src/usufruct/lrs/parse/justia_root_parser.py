"""Parse the Justia LRS root page into the list of all 54 Titles.

Each Title appears as:

    <a href="/codes/louisiana/revised-statutes/title-1/">Title 1 - General Provisions</a>

We collect ``(title_number, title_name)`` tuples in document order. Expected
volume: 54 entries (Titles 1-4, 6, 8-56; numbers 5 and 7 are reserved gaps).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from bs4 import BeautifulSoup

_TITLE_HREF_RE = re.compile(
    r"^/codes/louisiana/revised-statutes/title-(\d+)/?$"
)
# Anchor text is "Title N - Name" (with various spacings).
_TITLE_TEXT_RE = re.compile(r"^Title\s+(\d+)\s*[-–—]\s*(.+)$")


@dataclass(frozen=True)
class JustiaTitleListing:
    title_number: str
    name: str


def parse_justia_root(html: str) -> List[JustiaTitleListing]:
    soup = BeautifulSoup(html, "lxml")
    out: List[JustiaTitleListing] = []
    seen: set = set()
    for a in soup.find_all("a", href=True):
        m = _TITLE_HREF_RE.match(a["href"].strip())
        if not m:
            continue
        href_num = m.group(1)
        if href_num in seen:
            continue
        text = " ".join(a.get_text(strip=True).split())
        tm = _TITLE_TEXT_RE.match(text)
        if tm and tm.group(1) == href_num:
            name = tm.group(2).strip()
        else:
            # Fall back to whatever the anchor text said, sans the "Title N - "
            # prefix. Keeps us resilient to minor wording drift.
            name = text or f"Title {href_num}"
        out.append(JustiaTitleListing(title_number=href_num, name=name))
        seen.add(href_num)
    return out
