"""Parse an article's acts citation line into structured ``ActsCitation`` entries.

Examples handled:

    "Acts 1987, No. 124, §1, eff. Jan. 1, 1988."
    "Acts 1986, No. 211, §2; Acts 1987, No. 675, §1."
    "Amended by Acts 1884, No. 71; Acts 1908, No. 120, §1; ..."
    "Acts 1990, No. 989, §1, eff. January 1, 1991."
    "Acts 2002, 1st Ex. Sess., No. 128, §2." (extraordinary session)
    "Acts 1999, No. 1315, §§1, 2, eff. Jan. 1, 2000."  (multi-section)
    "Acts 1968, No. 154, §§1-3."                       (multi-section range)
    "Added by Acts 1972, No. 451, §§1 to 3."           (multi-section "to" range)
    "Acts 2021, No 128, §1."                           (missing period after "No")
    "Acts 1986, No. 225, §3. {{NOTE: SEE ACTS ...}}"   (braced NOTE block)
    "Acts 2019, No. 325, §1. NOTE: See Acts ..."       (trailing NOTE commentary)
    "Acts 2024, No. 670, §1, See Act."                 (trailing "See Act" reference)
    "Acts 1999, No. 517, §1. 1 As appears in enrolled bill." (footnote-marker tail)
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

# The optional session marker matches "1st Ex. Sess.,", "2nd Ex. Sess.,",
# "3rd Ex. Sess.," and the 1960-era no-space variant "1st Ex.Sess.,". The
# session designation is *not* stored as a structured field on
# ``ActsCitation`` (the original raw text in ``acts_citations_raw``
# preserves it verbatim). Two same-numbered acts from the same year's
# regular and extraordinary sessions would parse to identical
# ``ActsCitation`` records — a rare collision noted but accepted.
#
# Tolerances picked up in the LRS corpus:
# - ``No\.?`` (period optional) handles R.S. 1:55.1 ("Acts 2021, No 128, §1.").
# - ``\d+\s*(?:st|nd|rd|th)`` (optional space between digit and ordinal
#   suffix) handles R.S. 9:2921 ("Acts 2011, 1 st Ex. Sess., No. 30, §1.").
# - ``,?\s+eff\.`` (comma before ``eff.`` optional) handles R.S. 9:5131
#   ("Added by Acts 1974, No. 546, §1 eff. Jan. 1, 1975.").
_ACT_RE = re.compile(
    r"Acts\s+(\d{4})\s*,"
    r"(?:\s*\d+\s*(?:st|nd|rd|th)\s+Ex\.\s*Sess\.\s*,)?"
    r"\s*No\.?\s*(\d+)"
    r"(?:\s*,\s*§\s*(\d+))?"
    r"(?:\s*,?\s+eff\.\s*([^;]+?))?"
    r"\s*$",
    re.IGNORECASE,
)

# Plural-section form: one Act covering multiple sections. The captured
# spec is expanded by ``_expand_section_spec`` into a list of ints —
# comma-separated ("1, 2"), hyphen range ("1-3"), or "N to M" range
# ("1 to 3"). Each section becomes its own ``ActsCitation`` record sharing
# the act_year + act_number + effective_date + role.
_ACT_MULTI_RE = re.compile(
    r"Acts\s+(\d{4})\s*,"
    r"(?:\s*\d+\s*(?:st|nd|rd|th)\s+Ex\.\s*Sess\.\s*,)?"
    r"\s*No\.?\s*(\d+)"
    r"\s*,\s*§§\s*([\d\s,\-]+?(?:\s+to\s+\d+)?)"
    r"(?:\s*,?\s+eff\.\s*([^;]+?))?"
    r"\s*$",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"([A-Za-z]+)\.?\s+(\d{1,2})\s*,\s*(\d{4})"
)
_LEADING_NOISE = re.compile(r"^\s*(?:Amended\s+by\s+|Acquired\s+from\s+)", re.IGNORECASE)

# Embedded ``{{NOTE: ...}}`` annotations bleed into the acts line on a
# handful of sections (R.S. 9:2802, 9:1253, 14:102.9, 14:117.1). They are
# editorial commentary, not part of the acts citation — strip wherever
# they appear before parsing.
_BRACED_NOTE_RE = re.compile(r"\s*\{\{[^}]*\}\}\s*")

# Trailing unbraced ``NOTE: See Acts ... regarding applicability.`` form
# (R.S. 9:4843 and 6 siblings). The NOTE-cited Acts are cross-references,
# not amendments — they must not be parsed into ``ActsCitation`` records.
# Also matches ``*NOTE:`` (R.S. 9:5644 leads its commentary with an
# asterisk) and ``*Note error in English translation...`` style
# editorial annotations (R.S. 9:2785, no colon after Note). Conservative
# match: only strip when preceded by a period so we don't eat legitimate
# mid-citation text.
_TRAILING_NOTE_RE = re.compile(
    r"\s*\.\s*\*?\s*Note\b.*$", re.IGNORECASE
)

# Trailing unbraced ``* See ...`` cross-reference (R.S. 9:675 form, points
# at related repealed sections). Distinct from the per-piece
# ``, See Act.`` 2024-era form below.
_TRAILING_STAR_SEE_RE = re.compile(
    r"\s*\.\s*\*\s*See\b.*$", re.IGNORECASE
)

# Trailing ``, See Act.`` commentary appended in 2024-era amendments
# (R.S. 14:112.11–13, 14:71.3.2). Strip per-piece after split.
_TRAILING_SEE_ACT_RE = re.compile(r"\s*,?\s*See\s+Act\.?\s*$", re.IGNORECASE)

# Trailing footnote markers — a digit followed by editorial prose appended
# after the final period (R.S. 9:1131.26, 9:2945, 9:2725). Conservative
# match: only known prose anchors (``As appears`` and ``26 U.S.C.A.``) so
# we don't strip legitimate numbered continuations.
_FOOTNOTE_TAIL_RE = re.compile(
    r"\s+\d+\s+(?:As\s+appears\b|26\s+U\.S\.C\.A\.).*$",
    re.IGNORECASE,
)


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


def _expand_section_spec(spec: str) -> List[int]:
    """Expand a plural-section spec into a list of integer sections.

    Forms handled (all observed in real corpus data):

        "1, 2"       -> [1, 2]
        "1, 2, 5"    -> [1, 2, 5]
        "1-3"        -> [1, 2, 3]
        "1 to 3"     -> [1, 2, 3]
        "1, 2, and 5" -> [1, 2, 5]

    Unparseable specs return an empty list (caller will fall through to
    "no acts emitted" — same behavior as a regex miss).
    """
    s = spec.strip().rstrip(",").strip()
    # Normalize "and" connectors to commas before splitting.
    s = re.sub(r"\s*,?\s+and\s+", ", ", s, flags=re.IGNORECASE)
    # Word-form range: "N to M"
    m = re.match(r"^(\d+)\s+to\s+(\d+)$", s, re.IGNORECASE)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return list(range(lo, hi + 1)) if lo <= hi else []
    # Hyphen range: "N-M"
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return list(range(lo, hi + 1)) if lo <= hi else []
    # Comma-separated list.
    nums: List[int] = []
    for part in s.split(","):
        part = part.strip()
        if part.isdigit():
            nums.append(int(part))
    return nums


def parse_acts_citation_line(raw: str) -> List[ActsCitation]:
    """Parse a full acts-citation paragraph into a list of ``ActsCitation``."""
    if not raw:
        return []
    line = raw.strip()
    # Strip embedded ``{{NOTE: ...}}`` editorial blocks anywhere in the line.
    line = _BRACED_NOTE_RE.sub(" ", line)
    # Strip trailing unbraced ``. NOTE: ...`` / ``. *Note ...`` commentary
    # (R.S. 9:4843, 9:5644, 9:2785), and the related trailing
    # ``. * See ...`` cross-reference (R.S. 9:675).
    line = _TRAILING_NOTE_RE.sub("", line).strip()
    line = _TRAILING_STAR_SEE_RE.sub("", line).strip()
    # Drop a trailing period for stable splitting; re-added inside _split_acts.
    line = line.rstrip(".")
    line = _LEADING_NOISE.sub("", line).strip()
    pieces = _split_acts(line)
    out: List[ActsCitation] = []
    for i, piece in enumerate(pieces):
        # Per-piece tail cleanup: ``, See Act.`` (2024-era) and footnote
        # markers (``1 As appears in enrolled act``, ``1 26 U.S.C.A. ...``).
        # rstrip-period after each sub, since the strip can leave the
        # citation's own trailing period exposed (e.g., stripping
        # ``. 1 As appears in enrolled act`` from ``§1. 1 As appears ...``
        # leaves ``§1.``, which the act regexes' trailing ``\s*$`` reject).
        piece = _TRAILING_SEE_ACT_RE.sub("", piece).strip().rstrip(".").strip()
        piece = _FOOTNOTE_TAIL_RE.sub("", piece).strip().rstrip(".").strip()
        # Some pieces don't start with "Acts " (e.g., short continuation). Try
        # prefixing if necessary.
        candidate = piece if piece.lower().startswith("acts") else f"Acts {piece}"
        role = "enactment" if i == 0 else "amendment"
        m = _ACT_RE.match(candidate)
        if m:
            year = int(m.group(1))
            number = int(m.group(2))
            section = int(m.group(3)) if m.group(3) else None
            eff_raw = m.group(4).strip() if m.group(4) else None
            eff = parse_effective_date(eff_raw) if eff_raw else None
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
            continue
        # Plural-section form: one Act covering multiple sections — emit
        # one ``ActsCitation`` per section (per user decision 2026-05-21),
        # all sharing the act_year, act_number, eff date, and role.
        m = _ACT_MULTI_RE.match(candidate)
        if m:
            year = int(m.group(1))
            number = int(m.group(2))
            spec = m.group(3)
            eff_raw = m.group(4).strip() if m.group(4) else None
            eff = parse_effective_date(eff_raw) if eff_raw else None
            sections = _expand_section_spec(spec)
            for sec in sections:
                out.append(
                    ActsCitation(
                        act_year=year,
                        act_number=number,
                        section=sec,
                        effective_date=eff,
                        effective_date_raw=eff_raw,
                        role=role,
                    )
                )
    return out
