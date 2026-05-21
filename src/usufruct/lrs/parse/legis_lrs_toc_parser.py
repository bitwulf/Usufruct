"""Parse legis.la.gov LRS TOC pages.

The site has two TOC layers:

* **Root TOC** (``Laws_Toc.aspx?folder=75&level=Parent``). On the real site
  the Title rows are ASP.NET postback anchors with text like ``TITLE 14``
  but no folder ID in the HTML — folder IDs are server-side state, exposed
  only via ``__doPostBack``. We expose two parsers:

  * :func:`parse_legis_root_toc_titles` — read Title numbers off the row
    labels. This is what runs against the real page.
  * :func:`parse_legis_root_toc` — looks for ``Laws_Toc.aspx?folder=N&title=T``
    anchors and returns a ``{title: folder}`` map. Used by synthetic
    fixtures and as a forward-compat hook if legis later exposes the folder
    in the HTML.

* **Per-Title TOC** (``Laws_Toc.aspx?folder=N&title=T&level=Parent``). Flat
  list of ``<a href="Law.aspx?d=N">RS T:S</a>`` anchors plus one
  Title-level anchor ``RS T`` (no section). :func:`parse_legis_title_toc`
  returns ``{(title, section): website_law_id}``.

All parsers are tolerant of HTML drift — they key off ``href`` patterns and
anchor text, not class names.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from bs4 import BeautifulSoup

_LAW_HREF_RE = re.compile(r"Law\.aspx\?d=(\d+)", re.IGNORECASE)
_TOC_HREF_RE = re.compile(
    r"Laws_Toc\.aspx\?folder=(\d+)(?:&|&amp;)title=(\d+)", re.IGNORECASE
)
_RS_TEXT_RE = re.compile(r"RS\s+(\d+(?:-[A-Z])?)\s*:\s*([0-9.]+)", re.IGNORECASE)
_RS_TITLE_TEXT_RE = re.compile(r"RS\s+(\d+(?:-[A-Z])?)\b", re.IGNORECASE)
_TITLE_LABEL_RE = re.compile(r"^TITLE\s+(\d+(?:-[A-Z])?)\s*$", re.IGNORECASE)


def parse_legis_root_toc(html: str) -> Dict[str, int]:
    """Map each Title number to its legis folder ID (used by synthetic tests).

    On the real legis page, returns ``{}`` because folder IDs live behind
    ASP.NET postback handlers, not in anchor hrefs. Use
    :func:`parse_legis_root_toc_titles` plus a deterministic folder
    derivation for production runs.
    """
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


def parse_legis_root_toc_titles(html: str) -> List[str]:
    """Return Title numbers in the order they appear on the root TOC.

    Reads anchor text matching ``^TITLE \\d+(-[A-Z])?$`` — that's the
    canonical row-label form used in legis's ListViewTOC1 control.
    """
    soup = BeautifulSoup(html, "lxml")
    out: List[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a"):
        text = " ".join(a.get_text(strip=True).split())
        m = _TITLE_LABEL_RE.match(text)
        if not m:
            continue
        number = m.group(1)
        if number in seen:
            continue
        seen.add(number)
        out.append(number)
    return out


def parse_legis_title_toc(html: str) -> Dict[Tuple[str, str], int]:
    """Map ``(title, section) -> website_law_id`` for one per-Title TOC page.

    Filters out Title-level anchors (text ``RS T`` with no colon) — only
    section anchors of the form ``RS T:S`` are returned.
    """
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
