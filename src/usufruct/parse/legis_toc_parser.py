"""Parse the legis.la.gov flat Civil Code TOC.

Returns ``{article_number: website_law_id}``. Each article appears in the page
as two ``<a>`` links sharing the same ``d=`` parameter: one with text
``"CC NNN"`` (the article number) and one with the article heading. We pair
them by ``d=`` and read the article number off the ``CC NNN`` link.
"""
from __future__ import annotations

import re
from typing import Dict

from bs4 import BeautifulSoup

_D_RE = re.compile(r"d=(\d+)")
_CC_RE = re.compile(r"^CC\s+([\d.]+)$")


def parse_legis_toc(html: str) -> Dict[str, int]:
    soup = BeautifulSoup(html, "lxml")
    mapping: Dict[str, int] = {}
    for a in soup.find_all("a", href=_D_RE):
        text = a.get_text(" ", strip=True)
        m = _CC_RE.match(text)
        if not m:
            continue
        article_number = m.group(1)
        d_match = _D_RE.search(a["href"])
        if not d_match:
            continue
        mapping[article_number] = int(d_match.group(1))
    return mapping
