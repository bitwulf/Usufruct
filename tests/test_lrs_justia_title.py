"""Tests for the Justia per-Title page parser.

Each test pins a structural variant from the implementation plan:

* Title 1 — banner skip, duplicate-CHAPTER idempotence, section gap,
  decimal sections.
* Title 14 — letter-hyphen subparts, subgroups, NOTE: filtering, repealed
  sections by anchor text.
* Title 47 — Subtitle level, dense Subpart letters with O→Q gap.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from usufruct.lrs.model import ContainerLevel
from usufruct.lrs.parse import parse_justia_title

FIX = Path(__file__).parent / "fixtures" / "lrs" / "justia"


def _read(name: str) -> str:
    return (FIX / f"{name}.html").read_text()


# ---------- Title 1 ----------

@pytest.fixture(scope="module")
def title1():
    return parse_justia_title(_read("title-1"), expected_title_number="1")


def test_title1_reports_title_number(title1):
    assert title1.title_number == "1"


def test_title1_banner_is_skipped(title1):
    # No container with name "LOUISIANA REVISED STATUTES" exists.
    names = {c.name for c in title1.containers}
    assert "LOUISIANA REVISED STATUTES" not in names


def test_title1_has_single_title_chapter1_and_chapter2(title1):
    titles = [c for c in title1.containers if c.level == ContainerLevel.TITLE.value]
    chapters = [c for c in title1.containers if c.level == ContainerLevel.CHAPTER.value]
    assert len(titles) == 1
    assert titles[0].number == "1"
    chapter_numbers = sorted({c.number for c in chapters})
    assert chapter_numbers == ["1", "2"]


def test_title1_duplicate_chapter2_collapses(title1):
    # The two consecutive "CHAPTER 2. MISCELLANEOUS" headers must collapse
    # into a single Container — not two.
    ch2 = [c for c in title1.containers if c.level == ContainerLevel.CHAPTER.value and c.number == "2"]
    assert len(ch2) == 1
    assert "MISCELLANEOUS" in ch2[0].name.upper()


def test_title1_section_count_and_numbers(title1):
    # Anchor-counted sections in the Title 1 fixture, including 1:11.1, 1:50,
    # 1:55.1, the §1:58.x cluster.
    nums = [s.section_number for s in title1.sections]
    assert "1" in nums and "18" in nums
    assert "11.1" in nums
    assert "50" in nums                   # gap between 1:18 and 1:50
    assert "55.1" in nums
    assert any(n.startswith("58.") for n in nums)
    # All anchors are reported as Title 1
    assert {s.title_number for s in title1.sections} == {"1"}


def test_title1_chapter1_sections_attach_to_chapter1(title1):
    # §1:1 should be under Chapter 1, and §1:50 should be under Chapter 2.
    by_section = {s.section_number: s for s in title1.sections}
    chain_for_1 = by_section["1"].container_chain
    chain_for_50 = by_section["50"].container_chain
    # Chain entries are (level, number, name)
    chain_levels_1 = [c[0] for c in chain_for_1]
    chain_levels_50 = [c[0] for c in chain_for_50]
    assert ContainerLevel.TITLE.value in chain_levels_1
    assert ContainerLevel.CHAPTER.value in chain_levels_1
    chapter_for_1 = next(c for c in chain_for_1 if c[0] == ContainerLevel.CHAPTER.value)
    chapter_for_50 = next(c for c in chain_for_50 if c[0] == ContainerLevel.CHAPTER.value)
    assert chapter_for_1[1] == "1"
    assert chapter_for_50[1] == "2"


# ---------- Title 14 ----------

@pytest.fixture(scope="module")
def title14():
    return parse_justia_title(_read("title-14"), expected_title_number="14")


def test_title14_letter_hyphen_subparts(title14):
    subparts = {c.number for c in title14.containers if c.level == ContainerLevel.SUBPART.value}
    assert "A-1" in subparts
    assert "A-2" in subparts
    # Also expect normal letters
    assert "A" in subparts and "B" in subparts and "C" in subparts


def test_title14_has_subgroup_level(title14):
    subgroups = [c for c in title14.containers if c.level == ContainerLevel.SUBGROUP.value]
    # Title 14 has "1. ARSON AND USE OF EXPLOSIVES" and similar groupings
    assert subgroups, "expected at least one subgroup container in Title 14"
    arson = [c for c in subgroups if "ARSON" in c.name.upper()]
    assert arson, "expected an ARSON subgroup"


def test_title14_note_paragraphs_are_filtered(title14):
    # No container should have a NOTE: prefix in its name.
    for c in title14.containers:
        assert not c.name.upper().startswith("NOTE:"), c.name
    # But the parser should have captured at least a couple of notes aside.
    assert len(title14.notes) >= 2


def test_title14_repealed_section_detected_from_anchor(title14):
    by_section = {s.section_number: s for s in title14.sections}
    assert "47" in by_section, "Title 14 fixture should include §14:47"
    assert by_section["47"].repealed is True
    assert by_section["47"].heading is None
    assert by_section["47"].repeal_note and "Repealed" in by_section["47"].repeal_note


def test_title14_known_active_sections(title14):
    by_section = {s.section_number: s for s in title14.sections}
    # 14:30 First degree murder
    assert "30" in by_section
    assert by_section["30"].repealed is False
    assert by_section["30"].heading is not None
    assert "First degree" in by_section["30"].heading
    # 14:30.1 Second degree murder
    assert "30.1" in by_section
    assert "Second degree" in by_section["30.1"].heading


def test_title14_section_inside_subgroup_has_subgroup_in_chain(title14):
    # Find any section anchor whose container chain includes a subgroup.
    sections_with_subgroup = [
        s
        for s in title14.sections
        if any(c[0] == ContainerLevel.SUBGROUP.value for c in s.container_chain)
    ]
    assert sections_with_subgroup, (
        "expected at least one Title 14 section to live under a subgroup container"
    )


# ---------- Title 47 ----------

@pytest.fixture(scope="module")
def title47():
    return parse_justia_title(_read("title-47"), expected_title_number="47")


def test_title47_has_subtitle_level(title47):
    subtitles = [c for c in title47.containers if c.level == ContainerLevel.SUBTITLE.value]
    assert subtitles, "Title 47 should have at least one Subtitle container"
    # Subtitle numbers are Roman numerals (I, II, III, ...)
    numbers = {c.number for c in subtitles}
    assert "I" in numbers
    assert "II" in numbers


def test_title47_subpart_letters_extend_past_M(title47):
    subparts = sorted({c.number for c in title47.containers if c.level == ContainerLevel.SUBPART.value})
    # The fixture (Title 47 Income Tax) goes at least to SUBPART R and to AA/BB
    assert any(letter > "M" for letter in subparts)
    assert any(letter.startswith("AA") or letter.startswith("BB") for letter in subparts) or "R" in subparts


def test_title47_subpart_letter_gap_O_to_Q(title47):
    subpart_letters = [
        c.number
        for c in title47.containers
        if c.level == ContainerLevel.SUBPART.value
    ]
    # SUBPART O and SUBPART Q both present, SUBPART P absent.
    assert "O" in subpart_letters
    assert "Q" in subpart_letters
    assert "P" not in subpart_letters
