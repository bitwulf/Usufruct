"""Parse a legis.la.gov LRS Title TOC page into ``(title, section) -> d=NNNNNN``.

The per-Title TOC at ``Laws_Toc.aspx?folder=N&title=T`` is a flat table of
anchors of the form ``<a href="Law.aspx?d=NNNNNN">RS T:S</a>``. We harvest
each unique pair into a mapping. The root TOC at ``folder=75`` carries
``<a href="Laws_Toc.aspx?folder=N&title=T">RS T</a>`` per Title; we extract
``(title -> folder)`` so the per-Title TOC URLs can be generated.

Both parsers are tolerant of HTML drift: they key off the ``href`` rather
than text classes.
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

from bs4 import BeautifulSoup

_LAW_HREF_RE = re.compile(r"Law\.aspx\?d=(\d+)", re.IGNORECASE)
_TOC_HREF_RE = re.compile(
    r"Laws_Toc\.aspx\?folder=(\d+)(?:&|&amp;)title=(\d+)", re.IGNORECASE
)
_RS_TEXT_RE = re.compile(r"RS\s+(\d+(?:-[A-Z])?)\s*:\s*([0-9.]+)", re.IGNORECASE)
_RS_TITLE_TEXT_RE = re.compile(r"RS\s+(\d+(?:-[A-Z])?)\b", re.IGNORECASE)


def parse_legis_root_toc(html: str) -> Dict[str, int]:
    """Map each Title number to its legis folder ID (used in per-Title TOC URLs)."""
    soup = BeautifulSoup(html, "lxml")
    out: Dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        m = _TOC_HREF_RE.search(a["href"])
        if not m:
            continue
        folder, title = m.group(1), m.group(2)
        if title not in out:
            out[title] = int(folder)
    return out


def parse_legis_title_toc(html: str) -> Dict[Tuple[str, str], int]:
    """Map ``(title, section) -> website_law_id`` for one per-Title TOC page."""
    soup = BeautifulSoup(html, "lxml")
    out: Dict[Tuple[str, str], int] = {}
    for a in soup.find_all("a", href=True):
        href_m = _LAW_HREF_RE.search(a["href"])
        if not href_m:
            continue
        d_value = int(href_m.group(1))
        text = " ".join(a.get_text(strip=True).split())
        tm = _RS_TEXT_RE.search(text)
        if not tm:
            continue
        title, section = tm.group(1), tm.group(2)
        out[(title, section)] = d_value
    return out
