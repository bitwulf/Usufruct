"""LRS-side acts-citation parser.

Wraps the shared CC parser (``usufruct.parse.acts_parser``) and adds
LRS-specific handling for the citation forms surfaced during the
end-to-end bulk run that the CC parser does not natively accept.

Strategy:

1. LRS-aggressive text normalization — rewrites period-separators to
   semicolons (extracting trailing ``Renumbered from R.S.... by Acts``
   clauses into their own piece) and strips trailing junk
   (``at HH:MM A.M.`` times, ``H.C.R.`` references,
   ``*AS APPEARS IN ENROLLED BILL`` footnotes, ``N U.S.C.A.`` footnotes,
   ``applicable to taxable years`` clauses, etc.).
2. Split on semicolons.
3. Per piece, try the shared CC parser first (preserves CC behavior).
4. If CC returns nothing for the piece, fall back to an LRS-tolerant
   regex that accepts no-ordinal ``Ex.Sess.``, ``operative`` in place of
   ``eff.``, ``emerg. eff.``, ``Act No.`` / ``No,`` typos, comma-after-
   ordinal, and trailing-comma forms.

CC parser source code is not touched. Sections where CC succeeds keep
identical output.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ...model import ActsCitation
from ...parse import parse_acts_citation_line
from ...parse.acts_parser import parse_effective_date


def _lrs_expand_section_spec(spec: str) -> List[int]:
    """LRS-tolerant section-spec expander.

    Like CC's ``_expand_section_spec`` but accepts alphanumeric section
    identifiers ("3A", "4B") by taking the integer prefix and discarding
    the letter suffix. ``§§1, 3A`` becomes ``[1, 3]``. The letter info is
    preserved in ``acts_citations_raw``.
    """
    s = spec.strip().rstrip(",").strip()
    s = re.sub(r"\s*,?\s+and\s+", ", ", s, flags=re.IGNORECASE)
    m = re.match(r"^(\d+)\s+to\s+(\d+)$", s, re.IGNORECASE)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return list(range(lo, hi + 1)) if lo <= hi else []
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return list(range(lo, hi + 1)) if lo <= hi else []
    nums: List[int] = []
    for part in s.split(","):
        part = part.strip()
        m = re.match(r"^(\d+)", part)
        if m:
            nums.append(int(m.group(1)))
    return nums


# ---------- Normalization ----------

# Period-separated continuation. Accepts:
#   ". Amended by Acts YYYY..." (standard form)
#   ". Amended by Act YYYY..."  (singular "Act" — observed in T17)
#   ". Amended Acts YYYY..."    (no "by" connector)
#   ". Added by Acts YYYY..."   (continuation pieces post-codification)
#   ". Acts YYYY..."            (bare continuation)
# All rewrite to "; Acts? YYYY..." so the splitter produces clean pieces.
_NORM_PERIOD_NEXT_ACTS_RE = re.compile(
    r"\.\s+(?:Amended\s+(?:by\s+)?|Added\s+by\s+)?(Acts?\s+\d{4})",
    re.IGNORECASE,
)
# Missing-period continuation: "§N Acts YYYY..." (T3 section 1392).
_NORM_NOSEP_NEXT_ACTS_RE = re.compile(
    r"(§\d+\w*)\s+(Acts?\s+\d{4})", re.IGNORECASE
)

# Extract trailing ". Renumbered from R.S.[1950, ]§X:Y by (Acts YYYY...)"
# into its own piece so the trailing Acts citation parses on its own.
_NORM_RENUMBERED_BY_ACTS_RE = re.compile(
    r"\.\s+Renumbered\s+from\s+R\.?S\.?\s*1?9?5?0?\s*,?\s*§[^.]*?\s+by\s+(Acts\s+\d{4})",
    re.IGNORECASE,
)
# Same idea for "Redesignated [from R.S. X:Y] by Acts YYYY ...". The
# from-R.S.-X:Y clause is optional (R.S. 38:2212.3-style: "Redesignated
# by Acts 1999, No. 768, §3.").
_NORM_REDESIGNATED_BY_ACTS_RE = re.compile(
    r"\.\s+Redesignated\s+(?:from\s+R\.?S\.?\s+[\d:\.\-A-Za-z]+\s+)?by\s+(Acts\s+\d{4})",
    re.IGNORECASE,
)

# Strip trailing ". Redesignated from R.S. X[:Y]" — only at end of line
# so it does not consume the from-R.S. clause when "by Acts ..." follows
# (the latter is handled by _NORM_REDESIGNATED_BY_ACTS_RE above, which
# must therefore run BEFORE this rule).
_NORM_REDESIGNATED_FROM_BARE_TAIL_RE = re.compile(
    r"\.\s+Redesignated\s+from\s+R\.?S\.?\s+[\d:\.\-A-Za-z]+\.?\s*$",
    re.IGNORECASE,
)

# Rewrite ". Redesignated from R.S. X[:Y]. See (Acts YYYY...)" → "; Acts
# YYYY..." (R.S. 40:1666.7-style: the original Redesignated reference is
# discarded and the See-Acts citation becomes its own piece).
_NORM_REDESIGNATED_FROM_SEE_ACTS_RE = re.compile(
    r"\.\s+Redesignated\s+from\s+R\.?S\.?\s+[\d:\.\-A-Za-z]+\.?\s+See\s+(Acts\s+\d{4})",
    re.IGNORECASE,
)

# Strip trailing ". S.C.R. No. N, YYYY ..." (Senate Concurrent Resolution
# references; analogous to H.C.R.).
_NORM_SCR_TAIL_RE = re.compile(
    r"\.\s+S\.C\.R\.\s+No\.\s*\d+.*$", re.IGNORECASE
)

# Strip trailing ". N See ..." / ". N R.S. ..." / ". N LSA-Const ..." /
# ". N Acts YYYY...amended R.S...." digit-prefix editorial footnotes.
# These cover the long tail of "1 See, now, LSA-Const.", "1 R.S. 47:1501
# et seq.", "1 Acts 1973, Ex.Sess. No. 5 amended R.S....".
_NORM_DIGIT_EDITORIAL_TAIL_RE = re.compile(
    r"\s*\.\s*\d+\s+(?:See\b|R\.?S\.?\b|LSA-Const|Acts\s+\d{4}.*?(?:amend|enact|repeal)\w*).*$",
    re.IGNORECASE,
)

# Leading "Added by " (LRS-specific; CC's parser only strips "Amended by"
# and "Acquired from"). Applied to the WHOLE line first; per-piece variant
# below handles mid-line continuation pieces.
_NORM_LEADING_ADDED_BY_RE = re.compile(
    r"^\s*Added\s+by\s+(?=Acts\b)", re.IGNORECASE
)

# Strip trailing ". Redesignated from R.S. X:Y pursuant to R.S. ..." (no
# Acts to extract; the whole clause is reference-only).
_NORM_REDESIGNATED_PURSUANT_RE = re.compile(
    r"\.\s+Redesignated\s+from\s+R\.?S\.?\s+[\d:\.\-A-Za-z]+\s+pursuant\s+to\s+R\.?S\.?\s+[\d:\.\-A-Za-z]+\s*\.?\s*$",
    re.IGNORECASE,
)

# Strip trailing ". H.C.R. No. N, YYYY R.S." (House Concurrent Resolution
# references; not part of the act citation).
_NORM_HCR_TAIL_RE = re.compile(
    r"\.\s+H\.C\.R\.\s+No\.\s*\d+.*$", re.IGNORECASE
)

# Strip trailing "1 N U.S.C.A. ..." footnotes for any U.S.C. title number
# (CC's parser only matches "26 U.S.C.A.").
_NORM_USCA_FOOTNOTE_RE = re.compile(
    r"\s+\d+\s+\d+\s+U\.S\.C\.A?\.\s*.*$", re.IGNORECASE
)
# Bare "1 U.S.C.A. ..." (no number-before-U.S.C.A.) — observed in R.S. 52:1.
_NORM_BARE_USCA_FOOTNOTE_RE = re.compile(
    r"\s+\d+\s+U\.S\.C\.A?\.\s*.*$", re.IGNORECASE
)

# Strip trailing "1 So in enrolled bill ..." / "1 In subsec. ..." /
# "1 Present R.S. ..." / "1 Former ..." editorial footnotes (T12-heavy).
# The "In sub" alternative consumes the rest of the word ("In subsec",
# "In subsection") via \w* — otherwise a \b after "sub" would fail
# against the next character "s".
_NORM_SO_IN_FOOTNOTE_RE = re.compile(
    r"\s*\.\s*\d+\s+(?:So\s+in|In\s+sub\w*|Present\s+R\.?S\.?|Former)\b.*$",
    re.IGNORECASE,
)

# Strip trailing ", pars. N, M" / ", par. N" subsection-paragraph spec
# appended after §N (R.S. 33:4053-style).
_NORM_PARS_TAIL_RE = re.compile(
    r"\s*,\s*pars?\.\s+\d+(?:\s*,\s*\d+)*(?=\s*(?:\.|;|$))",
    re.IGNORECASE,
)

# Strip trailing "*AS APPEARS IN ENROLLED BILL.", "*As appears in...",
# "*So in enrolled bill", "*R.S. 40:592.1 et seq.", "*In (A)(1)(c)...",
# "*Reference is to..." style asterisk annotations (CC's
# _TRAILING_STAR_SEE_RE only catches "* See ...").
_NORM_STAR_FOOTNOTE_RE = re.compile(
    r"\s*\.\s*\*\s*"
    r"(?:As\s+(?:it\s+)?appears|AS\s+APPEARS|So\s+in|Reference|R\.S\.|In\s+\(|\d)"
    r".*$",
    re.IGNORECASE,
)

# Strip trailing "\d+ As (it )?appears..." digit-prefixed footnote
# (CC's _FOOTNOTE_TAIL_RE only matches "As appears", not "As it appears").
_NORM_DIGIT_AS_APPEARS_RE = re.compile(
    r"\s+\d+\s+As\s+(?:it\s+)?appears\b.*$", re.IGNORECASE
)

# Strip trailing ", applicable to taxable years on or after ...".
_NORM_APPLICABLE_TAXABLE_RE = re.compile(
    r"\s*,\s*applicable\s+to\s+taxable\s+years\b.*$", re.IGNORECASE
)

# Strip trailing ", see Act for effective date" — also drops a trailing
# ". NOTE: ..." block if one immediately follows.
_NORM_SEE_ACT_FOR_EFFDATE_RE = re.compile(
    r"\s*,\s*see\s+Act\s+for\s+effective\s+date\.?(?:\s+NOTE:.*)?\s*$",
    re.IGNORECASE,
)
# Trailing ", see Act." (short form — no "for effective date").
_NORM_SEE_ACT_SHORT_RE = re.compile(
    r"\s*,\s*see\s+Act\.?\s*$", re.IGNORECASE
)

# Strip trailing "at HH:MM A.M./P.M." time-of-day appended to dates
# (common in emerg. eff. citations from the 1960s-70s).
_NORM_TIME_OF_DAY_RE = re.compile(
    r"\s*,?\s*at\s+\d+:\d+\s*[AP]\.?M\.?\s*\.?\s*$", re.IGNORECASE
)
# "at 12:00 Noon" / "at 12:00 Midnight" variants.
_NORM_NOON_MIDNIGHT_RE = re.compile(
    r"\s*,?\s*at\s+\d+:\d+\s+(?:Noon|Midnight)\.?\s*$", re.IGNORECASE
)


def _normalize_acts_text(raw: str) -> str:
    """Apply LRS-side normalization to an acts-citation line.

    Order matters: trailing junk first (so semicolon-extraction regexes
    don't accidentally absorb them), then period→semicolon rewrites,
    then leading-prefix strip.
    """
    out = raw
    # Trailing junk strip — must run before period→semicolon so that
    # "...Acts YYYY... 1 15 U.S.C.A. ..." doesn't get split mid-footnote.
    out = _NORM_NOON_MIDNIGHT_RE.sub("", out)
    out = _NORM_TIME_OF_DAY_RE.sub("", out)
    out = _NORM_STAR_FOOTNOTE_RE.sub("", out)
    out = _NORM_DIGIT_AS_APPEARS_RE.sub("", out)
    out = _NORM_SO_IN_FOOTNOTE_RE.sub("", out)
    out = _NORM_USCA_FOOTNOTE_RE.sub("", out)
    out = _NORM_BARE_USCA_FOOTNOTE_RE.sub("", out)
    out = _NORM_APPLICABLE_TAXABLE_RE.sub("", out)
    out = _NORM_SEE_ACT_FOR_EFFDATE_RE.sub("", out)
    out = _NORM_HCR_TAIL_RE.sub("", out)
    out = _NORM_REDESIGNATED_PURSUANT_RE.sub("", out)
    out = _NORM_SEE_ACT_SHORT_RE.sub("", out)
    out = _NORM_SCR_TAIL_RE.sub("", out)
    out = _NORM_DIGIT_EDITORIAL_TAIL_RE.sub("", out)
    out = _NORM_PARS_TAIL_RE.sub("", out)
    # Period→semicolon rewrites — extract trailing Acts continuations into
    # their own pieces. Order matters: REDESIGNATED_BY_ACTS must precede
    # the bare REDESIGNATED_FROM strip so the embedded "by Acts" form is
    # rescued before its from-R.S. clause is dropped.
    out = _NORM_RENUMBERED_BY_ACTS_RE.sub(r"; \1", out)
    out = _NORM_REDESIGNATED_FROM_SEE_ACTS_RE.sub(r"; \1", out)
    out = _NORM_REDESIGNATED_BY_ACTS_RE.sub(r"; \1", out)
    out = _NORM_REDESIGNATED_FROM_BARE_TAIL_RE.sub("", out)
    out = _NORM_PERIOD_NEXT_ACTS_RE.sub(r"; \1", out)
    out = _NORM_NOSEP_NEXT_ACTS_RE.sub(r"\1; \2", out)
    # Leading-prefix strip (only on the first piece).
    out = _NORM_LEADING_ADDED_BY_RE.sub("", out)
    return out


# ---------- LRS-tolerant fallback ----------

# Per-piece leading-prefix strip (Added/Amended/Acquired). CC strips
# Amended and Acquired only at line-start; we strip all three per piece
# so continuation pieces with "Added by Acts..." parse cleanly.
_PIECE_LEADING_PREFIX_RE = re.compile(
    r"^\s*(?:Added|Amended|Acquired)\s+(?:by\s+|from\s+)?(?=Acts?\b)",
    re.IGNORECASE,
)

# Per-piece "Renumbered from R.S.[1950, ]§X:Y by " / "Redesignated from
# R.S. X:Y by " prefix strip. Fires when a continuation piece (post-
# semicolon split) opens with one of these clauses; the trailing
# "by Acts..." is the actual citation we want to parse.
_PIECE_RENUMBERED_BY_RE = re.compile(
    r"^\s*Renumbered\s+from\s+R\.?S\.?\s*1?9?5?0?\s*,?\s*§[^.]*?\s+by\s+(?=Acts\b)",
    re.IGNORECASE,
)
_PIECE_REDESIGNATED_BY_RE = re.compile(
    r"^\s*Redesignated\s+(?:from\s+R\.?S\.?\s+[\d:\.\-A-Za-z]+\s+)?by\s+(?=Acts\b)",
    re.IGNORECASE,
)

# Per-piece tail cleanup that CC's parser does not handle:
# - Trailing "1 15 U.S.C.A. ..." style footnote (per-piece variant).
# - Trailing comma after §N (e.g., "...§1,").
_PIECE_USCA_TAIL_RE = re.compile(
    r"\s+\d+\s+\d+\s+U\.S\.C\.A?\.\s*.*$", re.IGNORECASE
)
_PIECE_TRAILING_COMMA_RE = re.compile(r",\s*$")

# LRS-tolerant single-section regex.
#
# Differences from CC's _ACT_RE:
# - Session marker accepts: no-ordinal ("Ex.Sess.,", "Ex. Sess.,") and
#   comma-after-ordinal ("1st, Ex.Sess.,").
# - "Act No." accepted in addition to "No.".
# - "No," typo (comma where period belongs) accepted.
# - "operative DATE" accepted in addition to "eff. DATE".
# - "emerg. eff. DATE" accepted in addition to "eff. DATE".
# - Trailing comma after the eff/operative clause tolerated.
_LRS_ACT_RE = re.compile(
    r"Acts?\s+(\d{4})\s*,"
    r"(?:\s*(?:\d+\s*(?:st|nd|rd|th|d)\.?\s*,?\s*)?(?:Ex\.?\s*Sess\.|E\.\s*S\.)\s*,?)?"
    r"\s*(?:Act\s+)?No\s*[.,]?\s*(\d+)"
    r"(?:\s*,?\s*§\s*(\d+)[A-Z]?(?:\([A-Z]\))?)?"
    r"(?:\s*,?\s+(?:emerg\.\s+)?(?:eff\.|operative)\s*([^;]+?))?"
    r"\s*,?\s*$",
    re.IGNORECASE,
)

# Plural-section variant (matches CC's _ACT_MULTI_RE shape but with the
# same tolerances as _LRS_ACT_RE above plus alphanumeric section ids).
_LRS_ACT_MULTI_RE = re.compile(
    r"Acts?\s+(\d{4})\s*,"
    r"(?:\s*(?:\d+\s*(?:st|nd|rd|th|d)\.?\s*,?\s*)?(?:Ex\.?\s*Sess\.|E\.\s*S\.)\s*,?)?"
    r"\s*(?:Act\s+)?No\s*[.,]?\s*(\d+)"
    r"\s*,\s*§§\s*([\dA-Z\s,\-]+?(?:\s+to\s+\d+)?)"
    r"(?:\s*,?\s+(?:emerg\.\s+)?(?:eff\.|operative)\s*([^;]+?))?"
    r"\s*,?\s*$",
    re.IGNORECASE,
)


def _parse_lrs_piece(piece: str, role: str) -> List[ActsCitation]:
    """Run the LRS-tolerant regex on a single semicolon-split piece."""
    s = piece.strip().rstrip(".").strip()
    s = _PIECE_LEADING_PREFIX_RE.sub("", s)
    s = _PIECE_RENUMBERED_BY_RE.sub("", s)
    s = _PIECE_REDESIGNATED_BY_RE.sub("", s)
    s = _PIECE_USCA_TAIL_RE.sub("", s).strip()
    s = _PIECE_TRAILING_COMMA_RE.sub("", s).strip()
    # Accept both "Acts " and singular "Act " (T17-era continuation pieces).
    candidate = s if re.match(r"^Acts?\b", s, re.IGNORECASE) else f"Acts {s}"

    m = _LRS_ACT_RE.match(candidate)
    if m:
        year = int(m.group(1))
        number = int(m.group(2))
        section = int(m.group(3)) if m.group(3) else None
        eff_raw = m.group(4).strip() if m.group(4) else None
        eff = parse_effective_date(eff_raw) if eff_raw else None
        return [
            ActsCitation(
                act_year=year,
                act_number=number,
                section=section,
                effective_date=eff,
                effective_date_raw=eff_raw,
                role=role,
            )
        ]

    m = _LRS_ACT_MULTI_RE.match(candidate)
    if m:
        year = int(m.group(1))
        number = int(m.group(2))
        spec = m.group(3)
        eff_raw = m.group(4).strip() if m.group(4) else None
        eff = parse_effective_date(eff_raw) if eff_raw else None
        sections = _lrs_expand_section_spec(spec)
        return [
            ActsCitation(
                act_year=year,
                act_number=number,
                section=sec,
                effective_date=eff,
                effective_date_raw=eff_raw,
                role=role,
            )
            for sec in sections
        ]

    return []


def parse_lrs_acts_citation_line(raw: Optional[str]) -> List[ActsCitation]:
    """Parse an LRS acts-citation line into ``ActsCitation`` records.

    Per-piece: try the shared CC parser first; fall back to the LRS-
    tolerant regex when CC returns nothing.
    """
    if not raw:
        return []

    normalized = _normalize_acts_text(raw)
    pieces = [p.strip().rstrip(".").strip() for p in normalized.split(";")]
    pieces = [p for p in pieces if p]

    out: List[ActsCitation] = []
    for i, piece in enumerate(pieces):
        role = "enactment" if i == 0 else "amendment"

        # CC first. CC will treat the single piece as a 1-piece line and
        # always assign role="enactment"; we re-stamp role here.
        cc_results = parse_acts_citation_line(piece)
        if cc_results:
            for ac in cc_results:
                if ac.role != role:
                    out.append(ac.model_copy(update={"role": role}))
                else:
                    out.append(ac)
            continue

        # LRS fallback.
        out.extend(_parse_lrs_piece(piece, role))

    return out
