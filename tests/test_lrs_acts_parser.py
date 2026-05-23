"""Unit tests for the LRS-side acts-citation parser.

Covers the parsing families documented in BUILDHISTORY.md's reparse
section. Each test pins a representative real-corpus citation form to
the expected ``ActsCitation`` records the LRS-tolerant wrapper produces.
"""
from __future__ import annotations

from usufruct.lrs.parse.lrs_acts_parser import (
    parse_lrs_acts_citation_line,
    _normalize_acts_text,
)


# ---- Transparent delegation (CC parser path) ----------------------------


def test_cc_standard_single_act_passes_through():
    parsed = parse_lrs_acts_citation_line("Acts 1987, No. 124, §1, eff. Jan. 1, 1988.")
    assert len(parsed) == 1
    p = parsed[0]
    assert p.act_year == 1987 and p.act_number == 124 and p.section == 1
    assert p.effective_date == "1988-01-01" and p.role == "enactment"


def test_cc_semicolon_chain_passes_through():
    parsed = parse_lrs_acts_citation_line(
        "Acts 1986, No. 211, §2; Acts 1987, No. 675, §1."
    )
    assert [(p.act_year, p.role) for p in parsed] == [
        (1986, "enactment"),
        (1987, "amendment"),
    ]


def test_cc_amended_by_leading_passes_through():
    """The legacy 'Amended by Acts ...' header form (CC's _LEADING_NOISE)."""
    parsed = parse_lrs_acts_citation_line(
        "Amended by Acts 1973, No. 109, §1; Acts 1975, No. 327, §1."
    )
    assert len(parsed) == 2
    assert parsed[0].role == "enactment"
    assert parsed[1].role == "amendment"


def test_empty_and_none_return_empty():
    assert parse_lrs_acts_citation_line(None) == []
    assert parse_lrs_acts_citation_line("") == []


# ---- LRS-specific patterns ----------------------------------------------


def test_no_ordinal_ex_sess_no_space():
    """Top-leverage family: 'Acts 1956, Ex.Sess., No. 7, §1.'"""
    parsed = parse_lrs_acts_citation_line("Acts 1956, Ex.Sess., No. 7, §1.")
    assert len(parsed) == 1
    assert parsed[0].act_year == 1956 and parsed[0].act_number == 7
    assert parsed[0].section == 1


def test_no_ordinal_ex_sess_with_space():
    parsed = parse_lrs_acts_citation_line("Added by Acts 1950, Ex. Sess., No. 9, §1.")
    assert len(parsed) == 1
    assert parsed[0].act_year == 1950 and parsed[0].act_number == 9


def test_ordinal_ex_sess_no_space():
    parsed = parse_lrs_acts_citation_line(
        "Acts 1960, 3rd Ex.Sess., No. 4, §1, emerg. eff. Jan. 12, 1961."
    )
    assert len(parsed) == 1
    assert parsed[0].act_year == 1960
    assert parsed[0].effective_date == "1961-01-12"


def test_ex_sess_no_comma_after_sess():
    """R.S. 17:3122-style: 'Acts 1974, Ex.Sess. No. 7, §1, eff. Jan. 1, 1975'"""
    parsed = parse_lrs_acts_citation_line(
        "Acts 1974, Ex.Sess. No. 7, §1, eff. Jan. 1, 1975"
    )
    assert len(parsed) == 1
    assert parsed[0].act_year == 1974 and parsed[0].act_number == 7


def test_renumbered_from_rs1950_extracts_second_piece():
    """R.S. 12:481-style: enactment + Renumbered extracts trailing Acts
    as its own amendment piece."""
    raw = (
        "Acts 1954, No. 226, §1. "
        "Renumbered from R.S.1950, §12:391 by Acts 1968, No. 105, §3, "
        "eff. Jan. 1, 1969."
    )
    parsed = parse_lrs_acts_citation_line(raw)
    assert len(parsed) == 2
    assert parsed[0].act_year == 1954 and parsed[0].role == "enactment"
    assert parsed[1].act_year == 1968 and parsed[1].role == "amendment"
    assert parsed[1].effective_date == "1969-01-01"


def test_redesignated_pursuant_drops_reference_clause():
    """R.S. 11:901.1-style: 'Redesignated from R.S. X:Y pursuant to R.S.
    24:253' is a reference, not an act citation — drop it cleanly."""
    parsed = parse_lrs_acts_citation_line(
        "Added by Acts 1952, No. 568, §1. "
        "Redesignated from R.S. 17:711 pursuant to R.S. 24:253."
    )
    assert len(parsed) == 1
    assert parsed[0].act_year == 1952


def test_redesignated_by_acts_extracts_second_piece():
    """R.S. 11:3292.1-style: 'Redesignated [from R.S. X:Y] by Acts ...'
    extracts the trailing Acts citation as an amendment."""
    parsed = parse_lrs_acts_citation_line(
        "Acts 1991, No. 218, §1. "
        "Redesignated from R.S. 33:2140.1 by Acts 1991, No. 74, §3, "
        "eff. June 25, 1991."
    )
    assert len(parsed) == 2
    assert parsed[1].act_number == 74


def test_redesignated_no_from_clause():
    """R.S. 38:2212.3-style: 'Redesignated by Acts ...' (no 'from R.S.')."""
    parsed = parse_lrs_acts_citation_line(
        "Acts 1985, No. 922, §1. Redesignated by Acts 1999, No. 768, §3."
    )
    assert len(parsed) == 2
    assert parsed[1].act_year == 1999 and parsed[1].act_number == 768


def test_emerg_eff_in_continuation_piece():
    """R.S. 13:621.6-style: emerg. eff. as a tolerant synonym for eff."""
    parsed = parse_lrs_acts_citation_line(
        "Acts 1956, Ex.Sess., No. 7, §1. "
        "Amended by Acts 1974, No. 515, §1, emerg. eff. July 12, 1974."
    )
    assert len(parsed) == 2
    assert parsed[1].effective_date == "1974-07-12"


def test_operative_as_eff_synonym():
    """R.S. 13:312.2-style: 'operative DATE' instead of 'eff. DATE'."""
    parsed = parse_lrs_acts_citation_line(
        "Added by Acts 1975, No. 114, §1, operative Aug. 1, 1975."
    )
    assert len(parsed) == 1
    assert parsed[0].effective_date == "1975-08-01"


def test_act_no_typo():
    """R.S. 13:2087-style: 'Act No.' (extra 'Act ' word) instead of 'No.'."""
    parsed = parse_lrs_acts_citation_line(
        "Acts 1983, Act No. 442, §1, eff. July 2, 1983."
    )
    assert len(parsed) == 1
    assert parsed[0].act_number == 442


def test_no_comma_typo():
    """R.S. 13:2562.3-style: 'No,' (comma where period belongs)."""
    parsed = parse_lrs_acts_citation_line(
        "Acts 1966, No, 5, §3, eff. June 9, 1966."
    )
    assert len(parsed) == 1
    assert parsed[0].act_number == 5


def test_eff_see_act():
    """R.S. 13:2623-style: trailing 'eff. See Act.'"""
    parsed = parse_lrs_acts_citation_line("Acts 2025, No. 155, §1, eff. See Act.")
    assert len(parsed) == 1
    assert parsed[0].act_year == 2025


def test_us_code_footnote_stripped():
    """R.S. 30:912-style: trailing '1 N U.S.C.A. ...' footnote."""
    parsed = parse_lrs_acts_citation_line(
        "Acts 1978, No. 406, §1. 1 30 U.S.C.A. §201(b)."
    )
    assert len(parsed) == 1
    assert parsed[0].act_year == 1978


def test_inline_asterisk_footnote_stripped():
    """R.S. 26:361-style: trailing '*AS APPEARS IN ENROLLED BILL.'"""
    parsed = parse_lrs_acts_citation_line(
        "Acts 1987, No. 696, §1. *AS APPEARS IN ENROLLED BILL."
    )
    assert len(parsed) == 1
    assert parsed[0].act_year == 1987


def test_applicable_to_taxable_years_stripped():
    """R.S. 47:120.191-style: trailing ', applicable to taxable years ...'."""
    parsed = parse_lrs_acts_citation_line(
        "Acts 2013, No. 194, §1, applicable to taxable years on or after Jan. 1, 2013."
    )
    assert len(parsed) == 1
    assert parsed[0].act_year == 2013


def test_hcr_tail_stripped():
    """R.S. 47:305.9-style: trailing '. H.C.R. No. ...'"""
    parsed = parse_lrs_acts_citation_line(
        "Added by Acts 1964, No. 27, §1. H.C.R. No. 55, 1986 R.S."
    )
    assert len(parsed) == 1
    assert parsed[0].act_year == 1964


def test_time_of_day_after_emerg_eff():
    """R.S. 13:2488.2-style: ', at 10:05 A.M.' trailing time-of-day."""
    parsed = parse_lrs_acts_citation_line(
        "Added by Acts 1968, No. 16, §2, emerg. eff. July 4, 1968, at 10:05 A.M."
    )
    assert len(parsed) == 1
    assert parsed[0].effective_date == "1968-07-04"


def test_multi_section_with_alphanumeric():
    """R.S. 12:1851-style: §§1, 3A — alphanumeric section ids in spec.
    Integer prefixes are extracted; letter suffixes preserved in raw text."""
    parsed = parse_lrs_acts_citation_line(
        "Acts 2023, No. 259, §§1, 3A, eff. June 12, 2023."
    )
    sections = sorted(p.section for p in parsed if p.section is not None)
    assert sections == [1, 3]


def test_singular_act_in_continuation():
    """R.S. 17:162-style: 'Amended by Act 1966, No. 109, §1.' (singular 'Act')."""
    parsed = parse_lrs_acts_citation_line(
        "Added by Acts 1950, No. 505, §2. Amended by Act 1966, No. 109, §1."
    )
    assert len(parsed) == 2
    assert parsed[1].act_year == 1966 and parsed[1].act_number == 109


def test_trailing_comma_tolerated():
    """R.S. 13:5727-style: trailing comma after §N."""
    parsed = parse_lrs_acts_citation_line("Acts 2022, No. 403, §1,")
    assert len(parsed) == 1


def test_normalize_idempotent_on_clean_input():
    """Standard CC-style input passes through normalization unchanged."""
    clean = "Acts 1987, No. 124, §1, eff. Jan. 1, 1988."
    assert _normalize_acts_text(clean) == clean
