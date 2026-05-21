"""Tests for the legis.la.gov LRS section page parser."""
from __future__ import annotations

from pathlib import Path

import pytest

from usufruct.lrs.parse import parse_legis_section
from usufruct.parse import parse_acts_citation_line

FIX = Path(__file__).parent / "fixtures" / "lrs" / "legis_sections"


def _read(name: str) -> str:
    return (FIX / f"{name}.html").read_text()


@pytest.fixture(scope="module")
def rs_14_30():
    return parse_legis_section(_read("rs-14-30"))


def test_label_name_yields_title_and_section(rs_14_30):
    assert rs_14_30.title_number == "14"
    assert rs_14_30.section_number == "30"


def test_citation_is_canonical_form(rs_14_30):
    assert rs_14_30.citation == "R.S. 14:30"


def test_heading_is_first_degree_murder(rs_14_30):
    assert rs_14_30.heading == "First degree murder"


def test_status_active(rs_14_30):
    assert rs_14_30.status == "active"


def test_body_text_starts_with_paragraph_a(rs_14_30):
    assert rs_14_30.text is not None
    assert rs_14_30.text.startswith("A.")
    assert "First degree murder is the killing of a human being" in rs_14_30.text


def test_paragraphs_separated_by_double_newlines(rs_14_30):
    # The body must contain multiple paragraphs joined by "\n\n".
    assert "\n\n" in rs_14_30.text


def test_acts_citations_raw_contains_recent_amendments(rs_14_30):
    raw = rs_14_30.acts_citations_raw
    assert raw is not None
    assert raw.lower().startswith("amended by acts")
    assert "1973" in raw
    assert "2025" in raw


def test_acts_citations_parse_through_shared_cc_parser(rs_14_30):
    # Reuse the CC acts parser — citation grammar is shared. NOTE: the shared
    # parser does not (yet) handle special-session forms like
    # "Acts 2002, 1st Ex. Sess., No. 128, §2", so one act gets skipped.
    # Raw string holds ~29 acts; parser yields 28. Tracked as a corpus-level
    # gap to revisit when generalizing the acts parser.
    parsed = parse_acts_citation_line(rs_14_30.acts_citations_raw)
    assert len(parsed) >= 28, f"expected ≥28 act entries, got {len(parsed)}"
    years = {a.act_year for a in parsed}
    assert 1973 in years
    assert 2025 in years
    # First entry is the enactment role per shared rule.
    assert parsed[0].role == "enactment"
    assert all(a.role == "amendment" for a in parsed[1:])


def test_html_entities_decoded(rs_14_30):
    # &sect; in raw HTML must become §; &#160; must become a space.
    # If decoding failed we'd see literal "&sect;" or "&#160;" inside text.
    assert "&sect;" not in (rs_14_30.text or "")
    assert "&#160;" not in (rs_14_30.text or "")
