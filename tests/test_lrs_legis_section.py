"""Tests for the legis.la.gov LRS section page parser."""
from __future__ import annotations

from pathlib import Path

import pytest

from usufruct.lrs.parse import parse_legis_section
from usufruct.lrs.pipeline.orchestrate import _normalize_lrs_acts_text
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
    # Reuse the CC acts parser — citation grammar is shared. After the
    # special-session extension to the CC regex, all 29 amendments parse,
    # including the 2002 1st Extraordinary Session entry.
    parsed = parse_acts_citation_line(rs_14_30.acts_citations_raw)
    assert len(parsed) == 29, f"expected 29 act entries, got {len(parsed)}"
    years = {a.act_year for a in parsed}
    assert 1973 in years
    assert 2002 in years  # the special-session amendment
    assert 2025 in years
    # First entry is the enactment role per shared rule.
    assert parsed[0].role == "enactment"
    assert all(a.role == "amendment" for a in parsed[1:])
    # Pin the special-session entry by year+number so a future regex churn
    # doesn't silently drop it again.
    special = [a for a in parsed if a.act_year == 2002 and a.act_number == 128]
    assert len(special) == 1
    assert special[0].section == 2


def test_html_entities_decoded(rs_14_30):
    # &sect; in raw HTML must become §; &#160; must become a space.
    # If decoding failed we'd see literal "&sect;" or "&#160;" inside text.
    assert "&sect;" not in (rs_14_30.text or "")
    assert "&#160;" not in (rs_14_30.text or "")


# ---------- Title 1 pinned fixtures (Wave 1 regression set) ----------
#
# Two structural facts pinned here that 14:30 does not exercise:
#   * The outer-<span> body fallback path — legis serves a sizable subset of
#     older sections without the inner <div id="WPMainDoc">. RS 1:1 and
#     RS 1:11.1 both use that template.
#   * The legacy period-separated acts line — `"Acts 1958, No. 498, §1.
#     Amended by Acts 1970, No. 465, §1."`. The shared CC parser only splits
#     on `;`; the LRS orchestrator's `_normalize_lrs_acts_text` rewrites the
#     period form before delegating. RS 1:11.1 is the canonical witness.


@pytest.fixture(scope="module")
def rs_1_1():
    return parse_legis_section(_read("rs-1-1"))


@pytest.fixture(scope="module")
def rs_1_11_1():
    return parse_legis_section(_read("rs-1-11_1"))


def test_title_1_fixtures_use_outer_span_fallback():
    # Pin the structural fact that motivated saving these fixtures: both
    # lack the inner WPMainDoc div, so the parser must fall back to the
    # outer Document <span>. If legis ever rewraps these pages, regenerate
    # the fixtures and remove this assertion.
    for name in ("rs-1-1", "rs-1-11_1"):
        html = _read(name)
        assert 'id="WPMainDoc"' not in html, (
            f"{name} unexpectedly has WPMainDoc — refresh fixture"
        )


def test_rs_1_1_citation_and_status(rs_1_1):
    assert rs_1_1.title_number == "1"
    assert rs_1_1.section_number == "1"
    assert rs_1_1.citation == "R.S. 1:1"
    assert rs_1_1.status == "active"
    assert rs_1_1.heading == "Revised Statutes; how cited"


def test_rs_1_1_body_extracted_via_outer_span(rs_1_1):
    assert rs_1_1.text is not None
    assert rs_1_1.text.startswith(
        "This Act shall be known as the Louisiana Revised Statutes of 1950"
    )


def test_rs_1_1_has_no_acts_block(rs_1_1):
    # RS 1:1 — the title-citation rule — predates legis's acts-tracking
    # conventions and serves no acts line.
    assert rs_1_1.acts_citations_raw is None


def test_rs_1_11_1_citation_and_status(rs_1_11_1):
    assert rs_1_11_1.title_number == "1"
    assert rs_1_11_1.section_number == "11.1"
    assert rs_1_11_1.citation == "R.S. 1:11.1"
    assert rs_1_11_1.status == "active"
    assert rs_1_11_1.heading == "Special census"


def test_rs_1_11_1_body_extracted_via_outer_span(rs_1_11_1):
    assert rs_1_11_1.text is not None
    assert rs_1_11_1.text.startswith(
        "All incorporated municipalities in the State of Louisiana"
    )


def test_rs_1_11_1_acts_raw_is_period_separated(rs_1_11_1):
    # The verbatim text on legis is period-separated, not semicolon-separated.
    # This is the legacy form that requires LRS-side normalization before the
    # shared CC parser can split it.
    assert rs_1_11_1.acts_citations_raw == (
        "Acts 1958, No. 498, §1. Amended by Acts 1970, No. 465, §1."
    )


def test_rs_1_11_1_acts_round_trip_through_normalizer(rs_1_11_1):
    # End-to-end: outer-span fixture → parse_legis_section → LRS normalizer
    # → shared CC parser. Must yield two structured acts (enactment + 1970
    # amendment). If the normalizer or shared parser ever drops one, the
    # 1958/1970 pair makes the regression obvious.
    parsed = parse_acts_citation_line(
        _normalize_lrs_acts_text(rs_1_11_1.acts_citations_raw)
    )
    assert len(parsed) == 2
    enactment, amendment = parsed
    assert enactment.act_year == 1958
    assert enactment.act_number == 498
    assert enactment.section == 1
    assert enactment.role == "enactment"
    assert amendment.act_year == 1970
    assert amendment.act_number == 465
    assert amendment.section == 1
    assert amendment.role == "amendment"


# ---------- "Added by Acts ..." enactment form (R.S. 14:30.1 pattern) ----------
#
# The 1950 LRS codification used "Acts YYYY, ..." (bare) and "Amended by
# Acts YYYY, ..." (history). Sections inserted later in the corpus use
# "Added by Acts YYYY, ..." for the enactment. Title 14 has 81 such
# sections (12% of its active sections). Before the fix, the parser
# classified "Added by Acts ..." paragraphs as body text, so the whole
# acts citation line bled into ``text`` and ``acts_citations_raw`` was
# ``None``. Now: the body/acts split recognizes the third prefix, and the
# LRS normalizer strips the leading "Added by " for the shared CC parser.
#
# Tested with synthetic HTML so we don't grow the fixture set during
# Wave 2 — a pinned ``rs-14-30-1.html`` fixture remains deferred per the
# user's "Title 14 only" scope decision.


_RS_14_30_1_SYNTHETIC_HTML = (
    "<html><body>"
    '<span id="ctl00_PageBody_LabelName">RS 14:30.1</span>'
    '<span id="ctl00_PageBody_LabelDocument"><div id="WPMainDoc">'
    "<p>&sect;30.1.  Second degree murder</p>"
    "<p>A. Second degree murder is the killing of a human being.</p>"
    "<p>B. Whoever commits the crime of second degree murder shall be punished.</p>"
    "<p>Added by Acts 1973, No. 111, &sect;1. "
    "Amended by Acts 1975, No. 380, &sect;1; "
    "Acts 1976, No. 657, &sect;2.</p>"
    "</div></span>"
    "</body></html>"
)


def test_added_by_acts_classified_as_acts_not_body():
    parsed = parse_legis_section(_RS_14_30_1_SYNTHETIC_HTML)
    assert parsed.status == "active"
    assert parsed.heading == "Second degree murder"
    # The 'Added by Acts ...' line must land in acts_citations_raw, not text.
    assert parsed.acts_citations_raw is not None
    assert parsed.acts_citations_raw.startswith("Added by Acts 1973")
    assert "Added by Acts" not in (parsed.text or "")
    assert "Amended by Acts" not in (parsed.text or "")
    # Body has only the two §A/§B paragraphs.
    assert parsed.text == (
        "A. Second degree murder is the killing of a human being.\n\n"
        "B. Whoever commits the crime of second degree murder shall be punished."
    )


def test_added_by_acts_round_trip_through_normalizer():
    parsed = parse_legis_section(_RS_14_30_1_SYNTHETIC_HTML)
    acts = parse_acts_citation_line(
        _normalize_lrs_acts_text(parsed.acts_citations_raw)
    )
    assert [a.act_year for a in acts] == [1973, 1975, 1976]
    assert acts[0].role == "enactment"
    assert acts[0].act_number == 111
    assert acts[1].role == "amendment"
    assert acts[2].role == "amendment"
