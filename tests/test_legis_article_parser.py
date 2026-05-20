"""Regression tests against the article fixtures named in prompt.md.

Each test loads a cached HTML article and asserts the parser produces the
right article_number, status, and key shape (heading, text presence,
repeal-range detection, sub-paragraph preservation).
"""
from __future__ import annotations

from usufruct.parse import parse_legis_article


def test_cc1_simple_article(article_html):
    parsed = parse_legis_article(article_html("cc1"))
    assert parsed.article_number == "1"
    assert parsed.status == "active"
    assert parsed.heading == "Sources of law"
    assert parsed.text == "The sources of law are legislation and custom."
    assert parsed.acts_citations_raw == "Acts 1987, No. 124, §1, eff. Jan. 1, 1988."


def test_cc8_multiparagraph_text_uses_double_newline(article_html):
    parsed = parse_legis_article(article_html("cc8"))
    assert parsed.article_number == "8"
    assert parsed.status == "active"
    assert parsed.heading == "Repeal of laws"
    # Multiple body paragraphs joined by \n\n
    assert "\n\n" in parsed.text
    assert parsed.text.count("\n\n") >= 2  # >=3 body paragraphs


def test_cc14_multiple_amendments_in_one_line(article_html):
    parsed = parse_legis_article(article_html("cc14"))
    assert parsed.article_number == "14"
    assert "Acts 1991" in parsed.acts_citations_raw
    assert "Acts 2025" in parsed.acts_citations_raw
    assert ";" in parsed.acts_citations_raw


def test_cc60_repealed_range_in_art_line(article_html):
    parsed = parse_legis_article(article_html("cc60"))
    assert parsed.article_number == "60"
    assert parsed.status == "repealed"
    assert parsed.text is None
    assert parsed.heading is None
    assert "Arts. 60 to 85 repealed" in parsed.acts_citations_raw
    assert parsed.repealed_range_end == "85"


def test_cc90_1_decimal_article(article_html):
    parsed = parse_legis_article(article_html("cc90.1"))
    assert parsed.article_number == "90.1"
    assert parsed.status == "active"
    assert parsed.heading == "Impediment of age"
    assert parsed.text.startswith("A minor under the age of sixteen")


def test_cc103_1_preserves_numbered_subparagraphs(article_html):
    parsed = parse_legis_article(article_html("cc103.1"))
    assert parsed.article_number == "103.1"
    assert parsed.heading == "Judgment of divorce; time periods"
    assert "(1)" in parsed.text and "(2)" in parsed.text
    # Two acts citations joined by ;
    assert parsed.acts_citations_raw.count(";") >= 2


def test_cc162_repealed_range_uppercase_repealed(article_html):
    parsed = parse_legis_article(article_html("cc162"))
    assert parsed.article_number == "162"
    assert parsed.status == "repealed"
    assert parsed.repealed_range_end == "175"


def test_cc2315_famous_tort_article(article_html):
    parsed = parse_legis_article(article_html("cc2315"))
    assert parsed.article_number == "2315"
    assert parsed.heading == "Liability for acts causing damages"
    assert parsed.text.startswith("A.")  # A. paragraph
    assert "Amended by Acts" in parsed.acts_citations_raw


def test_cc2315_1_sub_numbered_sibling(article_html):
    parsed = parse_legis_article(article_html("cc2315.1"))
    assert parsed.article_number == "2315.1"
    assert parsed.heading == "Survival action"
    # Roman/letter sub-paragraph markers preserved
    for marker in ("A.", "B.", "C.", "D.", "E.", "F."):
        assert marker in parsed.text, f"missing sub-paragraph marker {marker!r}"


def test_cc3192_six_level_nested_article(article_html):
    """CC 3192 lives at Book III > Title XXI > Chapter 3 > Section 1 > §1."""
    parsed = parse_legis_article(article_html("cc3192"))
    assert parsed.article_number == "3192"
    # heading is preserved verbatim including trailing period.
    assert parsed.heading and parsed.heading.startswith("Funeral charges")


def test_cc3141_title_xx_a_pledge(article_html):
    parsed = parse_legis_article(article_html("cc3141"))
    assert parsed.article_number == "3141"
    assert parsed.heading == "Pledge defined"


def test_cc185_subsection_a_article(article_html):
    parsed = parse_legis_article(article_html("cc185"))
    assert parsed.article_number == "185"
    assert parsed.heading == "Presumption of paternity of husband"
