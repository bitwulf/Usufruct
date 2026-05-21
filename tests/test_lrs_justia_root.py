"""Tests for the Justia LRS root index parser."""
from __future__ import annotations

from pathlib import Path

import pytest

from usufruct.lrs.parse import parse_justia_root

FIX = Path(__file__).parent / "fixtures" / "lrs" / "justia"


@pytest.fixture(scope="module")
def justia_root_html() -> str:
    return (FIX / "index.html").read_text()


def test_root_yields_exactly_54_titles(justia_root_html):
    titles = parse_justia_root(justia_root_html)
    assert len(titles) == 54


def test_root_skips_reserved_title_numbers_5_and_7(justia_root_html):
    numbers = {t.title_number for t in parse_justia_root(justia_root_html)}
    assert "5" not in numbers
    assert "7" not in numbers


def test_root_covers_expected_range(justia_root_html):
    numbers = sorted(
        (int(t.title_number) for t in parse_justia_root(justia_root_html))
    )
    # First (1), last (56), and a couple of marquee entries
    assert numbers[0] == 1
    assert numbers[-1] == 56
    assert 14 in numbers
    assert 47 in numbers
    assert 9 in numbers


def test_root_extracts_names(justia_root_html):
    titles = {t.title_number: t.name for t in parse_justia_root(justia_root_html)}
    assert titles["1"] == "General Provisions"
    assert titles["14"] == "Criminal Law"
    assert titles["47"].startswith("Revenue")
    assert titles["9"] == "Civil Code-Ancillaries"
