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
