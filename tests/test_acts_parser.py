"""Unit tests for the acts citation parser."""
from __future__ import annotations

import pytest

from usufruct.parse import parse_acts_citation_line, parse_effective_date


def test_single_act_with_effective_date():
    citations = parse_acts_citation_line("Acts 1987, No. 124, §1, eff. Jan. 1, 1988.")
    assert len(citations) == 1
    c = citations[0]
    assert c.act_year == 1987
    assert c.act_number == 124
    assert c.section == 1
    assert c.effective_date == "1988-01-01"
    assert c.effective_date_raw == "Jan. 1, 1988"
    assert c.role == "enactment"


def test_multiple_acts_semicolon_split():
    citations = parse_acts_citation_line(
        "Acts 1986, No. 211, §2; Acts 1987, No. 675, §1; "
        "Acts 1997, No. 1317, §1, eff. July 15, 1997."
    )
    assert [c.role for c in citations] == ["enactment", "amendment", "amendment"]
    assert citations[-1].effective_date == "1997-07-15"


def test_amended_by_prefix_stripped():
    citations = parse_acts_citation_line(
        "Amended by Acts 1884, No. 71; Acts 1908, No. 120, §1."
    )
    assert [c.act_year for c in citations] == [1884, 1908]
    assert citations[0].role == "enactment"
    assert citations[0].section is None
    assert citations[1].section == 1


def test_section_optional():
    citations = parse_acts_citation_line("Acts 2019, No. 401, §1.")
    assert citations[0].section == 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Jan. 1, 1988", "1988-01-01"),
        ("January 1, 1991", "1991-01-01"),
        ("June 25, 2010", "2010-06-25"),
        ("Sept. 9, 2009", "2009-09-09"),
        ("not a date", None),
    ],
)
def test_parse_effective_date(raw, expected):
    assert parse_effective_date(raw) == expected


def test_empty_string():
    assert parse_acts_citation_line("") == []
    assert parse_acts_citation_line(None) == []  # type: ignore[arg-type]


def test_special_session_act():
    # Louisiana extraordinary (special) sessions insert a "Nth Ex. Sess.,"
    # marker between the year and No. Common in LRS Titles 14, 22, 47;
    # absent from the Civil Code, which is why this wasn't covered before.
    citations = parse_acts_citation_line(
        "Acts 2002, 1st Ex. Sess., No. 128, §2."
    )
    assert len(citations) == 1
    c = citations[0]
    assert c.act_year == 2002
    assert c.act_number == 128
    assert c.section == 2
    assert c.role == "enactment"


def test_special_session_mixed_with_regular_acts():
    # The R.S. 14:30 pattern in miniature: regular sessions on either side
    # of one extraordinary-session entry.
    citations = parse_acts_citation_line(
        "Amended by Acts 1973, No. 109, §1; "
        "Acts 2002, 1st Ex. Sess., No. 128, §2; "
        "Acts 2003, No. 1223, §1."
    )
    assert [c.act_year for c in citations] == [1973, 2002, 2003]
    assert citations[1].act_number == 128
    assert citations[1].section == 2


@pytest.mark.parametrize(
    "raw,year,number",
    [
        ("Acts 1989, 2nd Ex. Sess., No. 6, §1.", 1989, 6),
        ("Acts 1994, 3rd Ex. Sess., No. 37, §1.", 1994, 37),
        ("Acts 1960, 1st Ex.Sess., No. 7, §1.", 1960, 7),  # legacy no-space form
        (
            "Acts 2016, 1st Ex. Sess., No. 21, §1, eff. March 14, 2016.",
            2016,
            21,
        ),
    ],
)
def test_special_session_ordinals_and_variants(raw, year, number):
    citations = parse_acts_citation_line(raw)
    assert len(citations) == 1, f"{raw!r} failed to parse"
    assert citations[0].act_year == year
    assert citations[0].act_number == number


# ---------- 2026-05-21 extensions ----------
# Six pattern classes added in the same session to clear the 36 active
# sections in Title 1+9+14 whose acts text the parser couldn't split.
# Sources: R.S. 1:55.1 (Title 1); R.S. 14:102.9, 14:117.1, 14:324, 14:217,
# 14:331, 14:112.11–13, 14:71.3.2 (Title 14); R.S. 9:3578.5, 9:4843,
# 9:302, 9:2725, 9:2802, 9:1253, 9:1131.26, 9:2945 + relatives (Title 9).


def test_no_with_missing_period_after():
    """R.S. 1:55.1 has ``"Acts 2021, No 128, §1."`` — the period after
    ``No`` is missing. The regex makes it optional."""
    citations = parse_acts_citation_line("Acts 2021, No 128, §1.")
    assert len(citations) == 1
    assert citations[0].act_year == 2021
    assert citations[0].act_number == 128
    assert citations[0].section == 1


@pytest.mark.parametrize(
    "raw,expected_sections",
    [
        # R.S. 9:3578.5 — comma list
        ("Acts 1999, No. 1315, §§1, 2, eff. Jan. 1, 2000.", [1, 2]),
        # R.S. 14:324, 14:217 — comma list, no eff date
        ("Acts 1954, No. 75, §§1, 2.", [1, 2]),
        # R.S. 9:2725 — hyphen range
        ("Acts 1968, No. 154, §§1-3.", [1, 2, 3]),
        # R.S. 14:331 — "to" word range
        ("Acts 1972, No. 451, §§1 to 3.", [1, 2, 3]),
        # R.S. 9:302 — non-contiguous comma list (sections 7 and 9, not 8)
        ("Acts 1990, No. 1009, §§7, 9, eff. Jan. 1, 1991.", [7, 9]),
    ],
)
def test_multi_section_emits_one_record_per_section(raw, expected_sections):
    """``§§N, M`` / ``§§N-M`` / ``§§N to M`` — one Act applying to
    multiple sections emits one ``ActsCitation`` per section sharing
    act_year + act_number + role + effective_date.
    """
    citations = parse_acts_citation_line(raw)
    assert [c.section for c in citations] == expected_sections
    # All share the same act metadata.
    assert len({c.act_year for c in citations}) == 1
    assert len({c.act_number for c in citations}) == 1
    assert len({c.role for c in citations}) == 1


def test_multi_section_carries_effective_date_to_every_record():
    """Effective date applies to every section emitted from one Act."""
    citations = parse_acts_citation_line(
        "Acts 1999, No. 1315, §§1, 2, eff. Jan. 1, 2000."
    )
    assert all(c.effective_date == "2000-01-01" for c in citations)
    assert all(c.effective_date_raw == "Jan. 1, 2000" for c in citations)


def test_braced_note_block_is_stripped_before_parse():
    """``{{NOTE: ...}}`` editorial blocks bleed into the acts line on a
    few sections (R.S. 9:2802, 9:1253, 14:102.9, 14:117.1). They must
    not be parsed as Acts text.
    """
    raw = "Acts 1986, No. 225, §3. {{NOTE: SEE ACTS 1986, NO. 225, §5.}}"
    citations = parse_acts_citation_line(raw)
    assert len(citations) == 1, (
        f"Braced NOTE block leaked into parser output: got {len(citations)} "
        f"citations from {raw!r}"
    )
    assert citations[0].act_year == 1986
    assert citations[0].act_number == 225
    assert citations[0].section == 3


def test_braced_note_block_with_leading_added_by_synthetic():
    """R.S. 14:117.1 shape: leading "Added by" + trailing brace-NOTE.
    The CC parser doesn't strip "Added by" (LRS-side normalize does),
    but the brace-strip alone should leave a parse-clean tail.
    """
    raw = (
        "Acts 1983, No. 394, §1. "
        "{{NOTE: SECTION 2 OF ACTS 1983, NO. 394 READS AS FOLLOWS: ...}}"
    )
    citations = parse_acts_citation_line(raw)
    assert len(citations) == 1
    assert citations[0].act_year == 1983
    assert citations[0].act_number == 394


def test_trailing_unbraced_note_commentary_is_stripped():
    """R.S. 9:4843 form: ``Acts X, No. Y, §1. NOTE: See Acts ... regarding
    applicability.``. The trailing NOTE references *cross-reference* Acts
    that aren't amendments; they must not be parsed.
    """
    raw = (
        "Acts 2019, No. 325, §1. "
        "NOTE: See Acts 2019, No. 325, §§6, 7, and 10, regarding applicability."
    )
    citations = parse_acts_citation_line(raw)
    assert len(citations) == 1, (
        f"Trailing NOTE: leaked into parser output: got {len(citations)} "
        f"citations from {raw!r}"
    )
    assert citations[0].act_year == 2019
    assert citations[0].act_number == 325
    assert citations[0].section == 1


@pytest.mark.parametrize(
    "raw",
    [
        # R.S. 14:112.11–13 — single act + trailing "See Act."
        "Acts 2024, No. 670, §1, See Act.",
    ],
)
def test_trailing_see_act_reference_is_stripped(raw):
    """``, See Act.`` is 2024-era statutory drafting style appended to a
    citation to flag that the act's own text should be consulted. It's
    not part of the citation."""
    citations = parse_acts_citation_line(raw)
    assert len(citations) == 1, (
        f"Trailing 'See Act.' leaked into parser output: got "
        f"{len(citations)} citations from {raw!r}"
    )
    assert citations[0].act_year == 2024
    assert citations[0].act_number == 670
    assert citations[0].section == 1


def test_trailing_see_act_combines_with_multi_section():
    """R.S. 14:71.3.2 — both ``§§N, M`` plural-section AND ``, See Act.``
    trailing. The strip happens per-piece before regex match."""
    citations = parse_acts_citation_line("Acts 2024, No. 738, §§2, 5, See Act.")
    assert [c.section for c in citations] == [2, 5]
    assert all(c.act_year == 2024 and c.act_number == 738 for c in citations)


@pytest.mark.parametrize(
    "raw,year,number,section",
    [
        # R.S. 9:2945
        ("Acts 1999, No. 517, §1. 1 As appears in enrolled bill.", 1999, 517, 1),
        # R.S. 9:1131.26 (post-LRS Added-by strip)
        ("Acts 1983, No. 552, §1. 1 As appears in enrolled act", 1983, 552, 1),
    ],
)
def test_footnote_marker_tail_is_stripped(raw, year, number, section):
    """Trailing footnote markers like ``1 As appears in enrolled act``
    appear in a handful of legacy LRS sections after the final period.
    They are editorial annotations from the enrolled-bill rendering."""
    citations = parse_acts_citation_line(raw)
    assert len(citations) == 1, (
        f"Footnote-marker tail leaked: got {len(citations)} citations from {raw!r}"
    )
    assert citations[0].act_year == year
    assert citations[0].act_number == number
    assert citations[0].section == section


def test_footnote_marker_tail_combines_with_multi_section():
    """R.S. 9:2725 — both ``§§1-3`` plural-range AND a trailing
    ``1 26 U.S.C.A. §7425.`` footnote marker."""
    citations = parse_acts_citation_line(
        "Acts 1968, No. 154, §§1-3. 1 26 U.S.C.A. §7425."
    )
    assert [c.section for c in citations] == [1, 2, 3]
    assert all(c.act_year == 1968 and c.act_number == 154 for c in citations)


def test_expand_section_spec_unit():
    """Direct unit test on the spec expander — covers each shape in
    isolation, including the 'and' connector variant
    (``§§6, 7, and 10`` inside NOTE blocks, harmless test for parity).
    """
    from usufruct.parse.acts_parser import _expand_section_spec
    assert _expand_section_spec("1, 2") == [1, 2]
    assert _expand_section_spec("1-3") == [1, 2, 3]
    assert _expand_section_spec("1 to 3") == [1, 2, 3]
    assert _expand_section_spec("7, 9") == [7, 9]
    assert _expand_section_spec("1, 2, and 5") == [1, 2, 5]
    # Inverted range yields empty (rather than infinite-loop / negative range).
    assert _expand_section_spec("5-1") == []
    assert _expand_section_spec("") == []


# ---------- second-pass tolerance tweaks ----------
# These five patterns surfaced as the residual raw-unparsed cases after
# the first extension pass — each is a small editorial variation rather
# than a new structural form, but warrants a pinned test.


def test_split_ordinal_in_extraordinary_session():
    """R.S. 9:2921: ``"Acts 2011, 1 st Ex. Sess., No. 30, §1."`` — note
    the SPACE between ``1`` and ``st``. The ordinal regex now tolerates
    optional whitespace there."""
    citations = parse_acts_citation_line("Acts 2011, 1 st Ex. Sess., No. 30, §1.")
    assert len(citations) == 1
    assert citations[0].act_year == 2011
    assert citations[0].act_number == 30
    assert citations[0].section == 1


def test_effective_date_without_preceding_comma():
    """R.S. 9:5131: ``"Added by Acts 1974, No. 546, §1 eff. Jan. 1, 1975."``
    — no comma between ``§1`` and ``eff.``. The eff regex now treats the
    comma as optional as long as whitespace is present."""
    citations = parse_acts_citation_line("Acts 1974, No. 546, §1 eff. Jan. 1, 1975.")
    assert len(citations) == 1
    assert citations[0].act_year == 1974
    assert citations[0].effective_date == "1975-01-01"


def test_trailing_star_note_with_uppercase_form():
    """R.S. 9:5644 form: ``"Acts 1985, No. 728, §1. *NOTE: AS APPEARS IN
    ENROLLED BILL."`` — leading asterisk before NOTE."""
    citations = parse_acts_citation_line(
        "Acts 1985, No. 728, §1. *NOTE: AS APPEARS IN ENROLLED BILL."
    )
    assert len(citations) == 1
    assert citations[0].act_year == 1985
    assert citations[0].act_number == 728


def test_trailing_star_note_without_colon():
    """R.S. 9:2785 form: ``"Acts 1984, No. 331, §4. *Note error in English
    translation of French text; \"obligor\" should be \"obligee.\""`` —
    ``*Note`` is editorial commentary without a colon; the NOTE-strip regex
    matches on the word boundary."""
    citations = parse_acts_citation_line(
        'Acts 1984, No. 331, §4. *Note error in English translation of '
        'French text; "obligor" should be "obligee."'
    )
    assert len(citations) == 1
    assert citations[0].act_year == 1984
    assert citations[0].act_number == 331
    assert citations[0].section == 4


def test_trailing_star_see_cross_reference():
    """R.S. 9:675 form: ``"Acts 1950, No. 495, §1. * See R.S. 9:603,
    9:671-9:674 [repealed 1960] 9:691-9:693 [repealed 1952]."`` — trailing
    ``* See ...`` points at related (mostly repealed) sections, not Acts."""
    citations = parse_acts_citation_line(
        "Acts 1950, No. 495, §1. * See R.S. 9:603, 9:671-9:674 "
        "[repealed 1960] 9:691-9:693 [repealed 1952]."
    )
    assert len(citations) == 1
    assert citations[0].act_year == 1950
    assert citations[0].act_number == 495
    assert citations[0].section == 1
