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
