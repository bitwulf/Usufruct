"""Phase 2 regression tests: legis.la.gov TOC parser."""
from __future__ import annotations

from usufruct.parse import parse_legis_toc


def test_emits_thousands_of_articles(legis_toc_html):
    mapping = parse_legis_toc(legis_toc_html)
    assert 2000 <= len(mapping) <= 5000, f"unexpected count: {len(mapping)}"


def test_known_article_ids(legis_toc_html):
    mapping = parse_legis_toc(legis_toc_html)
    # Spot-checks from prompt's required test cases.
    assert mapping["1"] == 108528
    assert mapping["90.1"] == 1147329
    assert mapping["103.1"] == 408225


def test_article_numbers_are_strings(legis_toc_html):
    mapping = parse_legis_toc(legis_toc_html)
    assert all(isinstance(k, str) for k in mapping), "article numbers must be strings"
    assert all(isinstance(v, int) for v in mapping.values()), "law IDs must be ints"


def test_decimal_articles_present(legis_toc_html):
    mapping = parse_legis_toc(legis_toc_html)
    decimals = [k for k in mapping if "." in k]
    assert len(decimals) >= 50, f"expected many sub-numbered articles, got {len(decimals)}"
