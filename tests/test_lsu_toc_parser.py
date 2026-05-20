"""Phase 1 regression tests: LSU TOC parser.

We don't pin the *exact* container count because LSU may add/remove entries
across years. Instead we check structural invariants that would catch a
parser regression: presence of every level, the special Title XX-A and
Title XXII-A entries, the §1 paragraph-level container, and at least one
[Repealed] and one [Reserved] marker.
"""
from __future__ import annotations

from usufruct.parse import parse_lsu_toc


def test_parser_emits_containers(lsu_toc_html):
    containers = parse_lsu_toc(lsu_toc_html)
    assert len(containers) > 100, "LSU TOC should yield well over 100 containers"


def test_all_levels_present(lsu_toc_html):
    levels = {c.level for c in parse_lsu_toc(lsu_toc_html)}
    # Subsection is rare but should exist; preliminary_title appears twice (top + Book III).
    expected = {"preliminary_title", "book", "title", "chapter", "section", "subsection", "paragraph"}
    assert expected <= levels, f"missing levels: {expected - levels}"


def test_books_have_full_civcode_coverage(lsu_toc_html):
    containers = parse_lsu_toc(lsu_toc_html)
    books = {c.number: c for c in containers if c.level == "book"}
    # Civil Code has Books I–IV.
    assert set(books) == {"I", "II", "III", "IV"}, f"books found: {set(books)}"
    # Book I starts at 24 (after Preliminary Title 1-23) and ends at 399.
    assert books["I"].range_start == "24"
    assert books["I"].range_end == "399"


def test_letter_suffixed_titles_parsed(lsu_toc_html):
    titles = [c for c in parse_lsu_toc(lsu_toc_html) if c.level == "title"]
    numbers = {t.number for t in titles}
    assert "XX-A" in numbers, "Title XX-A (Pledge) must be parsed with its letter suffix"
    assert "XXII-A" in numbers, "Title XXII-A (Of Registry) must be parsed with its letter suffix"
    pledge = next(t for t in titles if t.number == "XX-A")
    assert pledge.range_start == "3141" and pledge.range_end == "3181"


def test_paragraph_level_containers_parsed(lsu_toc_html):
    paragraphs = [c for c in parse_lsu_toc(lsu_toc_html) if c.level == "paragraph"]
    assert paragraphs, "Expected at least one § (paragraph) container"
    funeral = next((p for p in paragraphs if "Funeral Charges" in p.name), None)
    assert funeral is not None and funeral.number == "1"
    assert funeral.range_start == "3192" and funeral.range_end == "3194"


def test_repealed_and_reserved_markers_recognised(lsu_toc_html):
    containers = parse_lsu_toc(lsu_toc_html)
    assert any(c.status == "repealed" for c in containers), "[Repealed] containers must be tagged"
    assert any(c.status == "reserved" for c in containers), "[Reserved] containers must be tagged"
    # Specific: Title X (Of Corporations) is repealed with no range.
    title_x = next((c for c in containers if c.level == "title" and c.number == "X"), None)
    assert title_x is not None and title_x.status == "repealed"


def test_ancestors_form_root_to_leaf_chain(lsu_toc_html):
    containers = parse_lsu_toc(lsu_toc_html)
    funeral = next(c for c in containers if c.level == "paragraph" and c.number == "1" and "Funeral" in c.name)
    chain = [a.level for a in funeral.ancestors]
    # Book III > Title XXI > Chapter 3 > Section 1 > §1
    assert chain == ["book", "title", "chapter", "section"], f"unexpected chain: {chain}"
    assert funeral.ancestors[0].number == "III"
    assert funeral.ancestors[1].number == "XXI"
