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


# ---------- RS 14:30.1 pinned fixture ("Added by Acts ..." enactment form) ----
#
# The 1950 LRS codification used bare "Acts YYYY, ..." (single enactment)
# and "Amended by Acts YYYY, ..." (accumulated history). Sections inserted
# *after* 1950 use "Added by Acts YYYY, ..." for the enactment marker —
# 81 of Title 14's 655 active sections (12%) follow this pattern. Before
# the fix, the parser classified "Added by Acts ..." paragraphs as body
# text, so the whole acts citation line bled into ``text`` and
# ``acts_citations_raw`` was ``None``.
#
# Fix split into two LRS-side pieces:
#   * ``_ACTS_LINE_RE`` in ``lrs/parse/legis_section_parser.py`` extended
#     to recognize the third prefix, so body→acts classification works.
#   * ``_normalize_lrs_acts_text`` in ``lrs/pipeline/orchestrate.py``
#     strips the leading "Added by " so the shared CC parser doesn't
#     drop the enactment piece.
#
# rs-14-30.1 is the canonical witness — added in 1973, currently 17
# parsed acts spanning 1973 → 2025.


@pytest.fixture(scope="module")
def rs_14_30_1():
    return parse_legis_section(_read("rs-14-30_1"))


def test_rs_14_30_1_citation_and_status(rs_14_30_1):
    assert rs_14_30_1.title_number == "14"
    assert rs_14_30_1.section_number == "30.1"
    assert rs_14_30_1.citation == "R.S. 14:30.1"
    assert rs_14_30_1.status == "active"
    assert rs_14_30_1.heading == "Second degree murder"


def test_rs_14_30_1_acts_classified_not_bled_into_body(rs_14_30_1):
    # The "Added by Acts 1973, ..." paragraph must land in
    # acts_citations_raw, not body text. Before the fix, the entire acts
    # line was concatenated into ``text``.
    assert rs_14_30_1.acts_citations_raw is not None
    assert rs_14_30_1.acts_citations_raw.startswith("Added by Acts 1973, No. 111")
    text = rs_14_30_1.text or ""
    assert "Added by Acts" not in text
    assert "Amended by Acts" not in text
    assert "; Acts " not in text
    # Body must end on the punishment-clause paragraph (the last real body
    # paragraph), not on an acts entry.
    assert text.rstrip().endswith("suspension of sentence.")


def test_rs_14_30_1_acts_round_trip_through_normalizer(rs_14_30_1):
    # End-to-end pin: fixture → parse_legis_section → LRS normalizer →
    # shared CC parser. The 1973 enactment must come through as the first
    # entry (not silently dropped because the shared parser's
    # _LEADING_NOISE doesn't know about "Added by ").
    acts = parse_acts_citation_line(
        _normalize_lrs_acts_text(rs_14_30_1.acts_citations_raw)
    )
    assert len(acts) == 17
    assert acts[0].act_year == 1973
    assert acts[0].act_number == 111
    assert acts[0].section == 1
    assert acts[0].role == "enactment"
    # Spot-check the tail — confirms full chain of amendments through 2025.
    assert acts[-1].act_year == 2025
    assert all(a.role == "amendment" for a in acts[1:])
    # No duplicate parse from the legacy period→semicolon rewriter eating
    # part of an amendment entry.
    years = [a.act_year for a in acts]
    assert years == sorted(years), "acts must come out in citation order"
