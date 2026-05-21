"""Unit tests for ``hierarchy.py`` — LRS hierarchy index + range assignment.

The motivator for these tests is Title 9 (Civil Code Ancillaries), whose
``code_book × code_title`` structure produces many sibling containers that
share ``(level, number)`` — and sometimes even ``(level, number, name)``
— under different parents. Examples on real data:

- ``code_title V`` appears under code_books I, II, III, IV with completely
  different names (NATURAL JURIDICAL PERSONS, OWNERSHIP, QUASI CONTRACTS
  AND OFFENSES, ...).
- ``Part I: IN GENERAL`` exists under 6+ chapters across multiple
  code_titles — same level, number, *and* name.

Before the path-keying fix, both ``LRSHierarchyIndex`` and
``assign_ranges_from_sections`` collapsed these siblings into one entry —
the latest one wins for the index, and section buckets merged into one
union range. The R.S. 9:2800 marquee section ended up under a chimera
chain and the ``Part I: IN GENERAL`` containers shared a single
``[51, 5504]`` range that swallowed essentially the whole Title.

The tests below construct minimal synthetic hierarchies that reproduce
the sibling-collision shapes and pin the post-fix behavior. Real-fixture
end-to-end pins live in ``test_lrs_orchestrate``.
"""
from __future__ import annotations

from usufruct.lrs.model import Container, ContainerLevel
from usufruct.lrs.pipeline.hierarchy import (
    assign_ranges_from_sections,
    build_lrs_hierarchy_index,
)


def _mk(level, number, name, parent_chain=None):
    return Container(
        level=level,
        number=number,
        name=name,
        parent_chain=parent_chain or [],
    )


def test_assign_ranges_disambiguates_same_name_siblings_under_different_parents():
    """Two ``Part I: IN GENERAL`` under different chapters must get
    disjoint section ranges, not the union of both.

    Pre-fix: keyed on ``(title, level, number, name)`` — both Part Is
    hashed to ``("9", "part", "I", "IN GENERAL")``, so Chapter 1's
    Part I §§101–103 and Chapter 2's Part I §§201–203 merged into one
    bucket and both containers got range ``[101, 203]``.

    Post-fix: keyed on the full root-to-container path of (level, number)
    steps. Each Part I gets its own bucket.
    """
    title = _mk(ContainerLevel.TITLE, "9", "CIVIL CODE--ANCILLARIES")
    ch1 = _mk(
        ContainerLevel.CHAPTER, "1", "ALPHA",
        parent_chain=[("title", "9")],
    )
    ch2 = _mk(
        ContainerLevel.CHAPTER, "2", "BETA",
        parent_chain=[("title", "9")],
    )
    part1_under_ch1 = _mk(
        ContainerLevel.PART, "I", "IN GENERAL",
        parent_chain=[("title", "9"), ("chapter", "1")],
    )
    part1_under_ch2 = _mk(
        ContainerLevel.PART, "I", "IN GENERAL",
        parent_chain=[("title", "9"), ("chapter", "2")],
    )
    containers = [title, ch1, ch2, part1_under_ch1, part1_under_ch2]

    sections_with_chain = [
        ("9", "101", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("chapter", "1", "ALPHA"),
            ("part", "I", "IN GENERAL"),
        ]),
        ("9", "103", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("chapter", "1", "ALPHA"),
            ("part", "I", "IN GENERAL"),
        ]),
        ("9", "201", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("chapter", "2", "BETA"),
            ("part", "I", "IN GENERAL"),
        ]),
        ("9", "203", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("chapter", "2", "BETA"),
            ("part", "I", "IN GENERAL"),
        ]),
    ]
    assign_ranges_from_sections(containers, sections_with_chain)

    # Each Part I covers only its own chapter's sections — not [101, 203].
    assert (
        part1_under_ch1.section_range_start,
        part1_under_ch1.section_range_end,
    ) == ("101", "103")
    assert (
        part1_under_ch2.section_range_start,
        part1_under_ch2.section_range_end,
    ) == ("201", "203")
    # Chapters span only their own Part.
    assert (ch1.section_range_start, ch1.section_range_end) == ("101", "103")
    assert (ch2.section_range_start, ch2.section_range_end) == ("201", "203")
    # Title spans the union (its own bucket is the whole chain).
    assert (title.section_range_start, title.section_range_end) == ("101", "203")


def test_index_lookup_returns_coherent_chain_for_same_name_siblings():
    """After ranges are correctly assigned, ``LRSHierarchyIndex.lookup``
    returns the chain rooted under the *correct* parent — not a chimera
    that picked the wrong sibling because they shared (level, number, name).
    """
    title = _mk(ContainerLevel.TITLE, "9", "CIVIL CODE--ANCILLARIES")
    ch1 = _mk(
        ContainerLevel.CHAPTER, "1", "ALPHA",
        parent_chain=[("title", "9")],
    )
    ch2 = _mk(
        ContainerLevel.CHAPTER, "2", "BETA",
        parent_chain=[("title", "9")],
    )
    part1_under_ch1 = _mk(
        ContainerLevel.PART, "I", "IN GENERAL",
        parent_chain=[("title", "9"), ("chapter", "1")],
    )
    part1_under_ch2 = _mk(
        ContainerLevel.PART, "I", "IN GENERAL",
        parent_chain=[("title", "9"), ("chapter", "2")],
    )
    containers = [title, ch1, ch2, part1_under_ch1, part1_under_ch2]
    sections_with_chain = [
        ("9", "101", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("chapter", "1", "ALPHA"),
            ("part", "I", "IN GENERAL"),
        ]),
        ("9", "103", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("chapter", "1", "ALPHA"),
            ("part", "I", "IN GENERAL"),
        ]),
        ("9", "201", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("chapter", "2", "BETA"),
            ("part", "I", "IN GENERAL"),
        ]),
        ("9", "203", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("chapter", "2", "BETA"),
            ("part", "I", "IN GENERAL"),
        ]),
    ]
    assign_ranges_from_sections(containers, sections_with_chain)
    index = build_lrs_hierarchy_index(
        containers,
        section_index=[("9", s) for s in ("101", "103", "201", "203")],
    )

    # §101 lives under Chapter 1 / Part I — chapter name is ALPHA, not BETA.
    chain = index.lookup("9", "101")
    assert [n.number for n in chain] == ["9", "1", "I"]
    assert chain[1].name == "ALPHA"
    assert chain[2].name == "IN GENERAL"

    # §201 lives under Chapter 2 / Part I — chapter name is BETA.
    chain = index.lookup("9", "201")
    assert [n.number for n in chain] == ["9", "2", "I"]
    assert chain[1].name == "BETA"
    assert chain[2].name == "IN GENERAL"


def test_resolve_chain_walks_full_path_not_collapsed_level_number_pairs():
    """Title-9-shaped case: two ``code_title V`` containers under different
    code_books. The deeper-than-name sibling collision (same level + same
    number, different names) is what the prior key collapsed at *every*
    intermediate step of ``resolve_chain``, producing chimera chains like
    ``code_book I › code_title IV: PREDIAL SERVITUDES`` even though
    PREDIAL SERVITUDES actually lives under code_book II.

    Pin: a deeply-nested container's resolved chain matches its
    ``parent_chain`` exactly — never substitutes a sibling at any level.
    """
    title = _mk(ContainerLevel.TITLE, "9", "CIVIL CODE--ANCILLARIES")
    book_ii = _mk(
        ContainerLevel.CODE_BOOK, "II", "THINGS, AND OF THE DIFFERENT MODIFICATIONS OF OWNERSHIP",
        parent_chain=[("title", "9")],
    )
    book_iii = _mk(
        ContainerLevel.CODE_BOOK, "III", "OF THE DIFFERENT MODES OF ACQUIRING THE OWNERSHIP OF THINGS",
        parent_chain=[("title", "9")],
    )
    ct_v_under_book_ii = _mk(
        ContainerLevel.CODE_TITLE, "V", "OF PREDIAL SERVITUDES",
        parent_chain=[("title", "9"), ("code_book", "II")],
    )
    ct_v_under_book_iii = _mk(
        ContainerLevel.CODE_TITLE, "V", "OF QUASI CONTRACTS, AND OF OFFENSES AND QUASI OFFENSES",
        parent_chain=[("title", "9"), ("code_book", "III")],
    )
    chap_under_book_iii_ct_v = _mk(
        ContainerLevel.CHAPTER, "2", "OF OFFENSES AND QUASI OFFENSES",
        parent_chain=[("title", "9"), ("code_book", "III"), ("code_title", "V")],
    )
    chap_under_book_ii_ct_v = _mk(
        ContainerLevel.CHAPTER, "1", "GENERAL PROVISIONS",
        parent_chain=[("title", "9"), ("code_book", "II"), ("code_title", "V")],
    )
    containers = [
        title, book_ii, book_iii,
        ct_v_under_book_ii, ct_v_under_book_iii,
        chap_under_book_ii_ct_v, chap_under_book_iii_ct_v,
    ]
    sections_with_chain = [
        # §2800 — Book III branch (the R.S. 9:2800 marquee).
        ("9", "2800", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("code_book", "III", "..."),
            ("code_title", "V", "QUASI CONTRACTS"),
            ("chapter", "2", "OFFENSES"),
        ]),
        # §700 — Book II branch (PREDIAL SERVITUDES).
        ("9", "700", [
            ("title", "9", "CIVIL CODE--ANCILLARIES"),
            ("code_book", "II", "..."),
            ("code_title", "V", "PREDIAL"),
            ("chapter", "1", "GENERAL"),
        ]),
    ]
    assign_ranges_from_sections(containers, sections_with_chain)
    index = build_lrs_hierarchy_index(
        containers,
        section_index=[("9", "700"), ("9", "2800")],
    )

    # §2800 must resolve under code_book III → code_title V (QUASI...).
    chain = index.lookup("9", "2800")
    levels_and_names = [(n.level, n.number, n.name) for n in chain]
    assert ("code_book", "III", book_iii.name) in levels_and_names
    assert ("code_book", "II", book_ii.name) not in levels_and_names
    assert ("code_title", "V", ct_v_under_book_iii.name) in levels_and_names
    assert ("code_title", "V", ct_v_under_book_ii.name) not in levels_and_names
    # Chapter is the Book III branch's Chapter 2 (OFFENSES), not Book II's
    # Chapter 1 (GENERAL PROVISIONS).
    chapter_nodes = [n for n in chain if n.level == "chapter"]
    assert len(chapter_nodes) == 1
    assert chapter_nodes[0].number == "2"
    assert chapter_nodes[0].name == "OF OFFENSES AND QUASI OFFENSES"

    # And the converse — §700 must resolve under code_book II → code_title V (PREDIAL).
    chain = index.lookup("9", "700")
    levels_and_names = [(n.level, n.number, n.name) for n in chain]
    assert ("code_book", "II", book_ii.name) in levels_and_names
    assert ("code_book", "III", book_iii.name) not in levels_and_names
    assert ("code_title", "V", ct_v_under_book_ii.name) in levels_and_names
    assert ("code_title", "V", ct_v_under_book_iii.name) not in levels_and_names
