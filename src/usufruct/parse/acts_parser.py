"""Parse an article's acts citation line into structured ``ActsCitation`` entries.

Examples handled:

    "Acts 1987, No. 124, §1, eff. Jan. 1, 1988."
    "Acts 1986, No. 211, §2; Acts 1987, No. 675, §1."
    "Amended by Acts 1884, No. 71; Acts 1908, No. 120, §1; ..."
    "Acts 1990, No. 989, §1, eff. January 1, 1991."
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..model import ActsCitation

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_ACT_RE = re.compile(
    r"Acts\s+(\d{4})\s*,\s*No\.\s*(\d+)"
    r"(?:\s*,\s*§\s*(\d+))?"
    r"(?:\s*,\s*eff\.\s*([^;]+?))?"
    r"\s*$",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"([A-Za-z]+)\.?\s+(\d{1,2})\s*,\s*(\d{4})"
)
_LEADING_NOISE = re.compile(r"^\s*(?:Amended\s+by\s+|Acquired\s+from\s+)", re.IGNORECASE)


def parse_effective_date(raw: str) -> Optional[str]:
    """Convert ``"Jan. 1, 1988"`` -> ``"1988-01-01"``. Returns ``None`` if unparseable."""
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if not m:
        return None
    month_word = m.group(1).lower().rstrip(".")
    day = int(m.group(2))
    year = int(m.group(3))
    month = _MONTHS.get(month_word)
    if not month or not (1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _split_acts(line: str) -> List[str]:
    """Split a citation line on top-level semicolons.

    No internal grouping to worry about — months and act numbers don't contain
    ``;``. Trim each piece and drop empties.
    """
    pieces = [p.strip().rstrip(".").strip() for p in line.split(";")]
    return [p for p in pieces if p]


def parse_acts_citation_line(raw: str) -> List[ActsCitation]:
    """Parse a full acts-citation paragraph into a list of ``ActsCitation``."""
    if not raw:
        return []
    line = raw.strip()
    # Drop a trailing period for stable splitting; re-added inside _split_acts
    line = line.rstrip(".")
    line = _LEADING_NOISE.sub("", line).strip()
    pieces = _split_acts(line)
    out: List[ActsCitation] = []
    for i, piece in enumerate(pieces):
        # Some pieces don't start with "Acts " (e.g., short continuation). Try
        # prefixing if necessary.
        candidate = piece if piece.lower().startswith("acts") else f"Acts {piece}"
        m = _ACT_RE.match(candidate)
        if not m:
            continue
        year = int(m.group(1))
        number = int(m.group(2))
        section = int(m.group(3)) if m.group(3) else None
        eff_raw = m.group(4).strip() if m.group(4) else None
        eff = parse_effective_date(eff_raw) if eff_raw else None
        role = "enactment" if i == 0 else "amendment"
        out.append(
            ActsCitation(
                act_year=year,
                act_number=number,
                section=section,
                effective_date=eff,
                effective_date_raw=eff_raw,
                role=role,
            )
        )
    return out
