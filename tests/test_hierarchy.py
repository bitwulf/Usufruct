"""Hierarchy interval lookup regression tests."""
from __future__ import annotations

from usufruct.parse import parse_lsu_toc
from usufruct.pipeline.hierarchy import build_hierarchy_index


def test_deepest_match_wins(lsu_toc_html):
    idx = build_hierarchy_index(parse_lsu_toc(lsu_toc_html))
    path = idx.lookup("2315")
    assert [n.level for n in path] == ["book", "title", "chapter"]
    assert path[0].number == "III"
    assert path[1].number == "V"
    assert path[2].number == "3"


def test_sub_numbered_article_inherits_chapter(lsu_toc_html):
    idx = build_hierarchy_index(parse_lsu_toc(lsu_toc_html))
    path = idx.lookup("2315.1")
    assert [n.level for n in path] == ["book", "title", "chapter"]
    assert path[1].number == "V" and path[2].number == "3"


def test_preliminary_title_articles(lsu_toc_html):
    idx = build_hierarchy_index(parse_lsu_toc(lsu_toc_html))
    path = idx.lookup("1")
    assert path[0].level == "preliminary_title"
    assert path[-1].number == "1"  # Chapter 1
    # CC 15 sits in Chapter 3 (Conflict of Laws).
    path15 = idx.lookup("15")
    assert path15[-1].number == "3"


def test_letter_suffixed_title_hierarchy(lsu_toc_html):
    """CC 3141 starts Title XX-A (Pledge)."""
    idx = build_hierarchy_index(parse_lsu_toc(lsu_toc_html))
    path = idx.lookup("3141")
    titles = [n.number for n in path if n.level == "title"]
    assert "XX-A" in titles


def test_deeply_nested_paragraph_container(lsu_toc_html):
    """CC 3192 is the first article under §1 Of Funeral Charges (5 levels deep)."""
    idx = build_hierarchy_index(parse_lsu_toc(lsu_toc_html))
    path = idx.lookup("3192")
    levels = [n.level for n in path]
    # Book → Title → Chapter → Section → Paragraph
    assert levels == ["book", "title", "chapter", "section", "paragraph"]
    assert path[-1].number == "1"  # §1


def test_subsection_path_for_cc185(lsu_toc_html):
    """CC 185 lives under Book I > Title VII > Chapter 2 > Section 2 > Subsection A."""
    idx = build_hierarchy_index(parse_lsu_toc(lsu_toc_html))
    path = idx.lookup("185")
    levels = [n.level for n in path]
    assert levels == ["book", "title", "chapter", "section", "subsection"]
    assert path[-1].number == "A"


def test_unknown_article_returns_empty(lsu_toc_html):
    idx = build_hierarchy_index(parse_lsu_toc(lsu_toc_html))
    # Article 99999 is well beyond the Civil Code.
    assert idx.lookup("99999") == []
