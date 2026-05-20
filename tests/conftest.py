"""Shared pytest fixtures.

All fixtures read HTML from ``tests/fixtures/`` — no network access during tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def lsu_toc_html() -> str:
    return (FIXTURES / "lsu_toc.html").read_text()


@pytest.fixture(scope="session")
def legis_toc_html() -> str:
    return (FIXTURES / "legis_toc.html").read_text()


@pytest.fixture
def article_html():
    def _load(name: str) -> str:
        return (FIXTURES / "articles" / f"{name}.html").read_text()
    return _load
