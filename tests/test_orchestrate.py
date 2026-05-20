"""End-to-end orchestrator test using a mock client backed by fixture HTML.

Verifies the pipeline's record assembly + blank/repealed fill paths without
touching the network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import pytest

from usufruct.fetch.client import FetchResult, sha256_text
from usufruct.fetch.legis_article import article_url
from usufruct.parse import parse_legis_toc, parse_lsu_toc
from usufruct.pipeline.orchestrate import PipelinePaths, _article_from_parsed  # type: ignore[attr-defined]
from usufruct.pipeline.orchestrate import run_phase3
from usufruct.pipeline.hierarchy import build_hierarchy_index
from usufruct.parse import parse_legis_article
from usufruct.model import Article

FIX = Path(__file__).parent / "fixtures"


# Map article number -> fixture filename stem
TEST_ARTICLES = {
    "1":      "cc1",
    "8":      "cc8",
    "14":     "cc14",
    "15":     "cc15",
    "60":     "cc60",
    "90.1":   "cc90.1",
    "103.1":  "cc103.1",
    "162":    "cc162",
    "185":    "cc185",
    "2315":   "cc2315",
    "2315.1": "cc2315.1",
    "3141":   "cc3141",
    "3192":   "cc3192",
}


class FakeClient:
    """Stand-in for ``CachedClient`` that serves fixtures by URL."""

    def __init__(self, article_index: Dict[str, int]):
        # invert: website_law_id -> article_number, so we can resolve d= back to fixture
        self._by_d = {d: n for n, d in article_index.items() if n in TEST_ARTICLES}
        self._fixture_for_d = {d: TEST_ARTICLES[n] for d, n in self._by_d.items()}

    def get(self, url: str, force_refetch: bool = False) -> FetchResult:
        # parse d=
        d = int(url.rsplit("d=", 1)[1])
        fixture_name = self._fixture_for_d[d]
        text = (FIX / "articles" / f"{fixture_name}.html").read_text()
        return FetchResult(url=url, text=text, sha256=sha256_text(text), from_cache=True)


@pytest.fixture
def orchestrate_setup(tmp_path, lsu_toc_html, legis_toc_html):
    containers = parse_lsu_toc(lsu_toc_html)
    article_index = parse_legis_toc(legis_toc_html)
    # narrow article_index to our test set
    article_index = {k: article_index[k] for k in TEST_ARTICLES if k in article_index}
    paths = PipelinePaths(root=tmp_path / "data")
    client = FakeClient(article_index)
    return client, paths, containers, article_index


def test_orchestrator_emits_records_for_every_test_article(orchestrate_setup):
    client, paths, containers, article_index = orchestrate_setup
    articles = run_phase3(client, paths, containers, article_index)
    for n in TEST_ARTICLES:
        assert n in articles, f"article {n} missing from output"
    assert (paths.articles_jsonl).exists()
    assert (paths.manifest).exists()
    assert (paths.validation_report).exists()


def test_record_schema_is_complete_for_cc1(orchestrate_setup):
    client, paths, containers, article_index = orchestrate_setup
    articles = run_phase3(client, paths, containers, article_index)
    a = articles["1"]
    assert a.urn == "urn:us-la:civcode:art:1"
    assert a.status == "active"
    assert a.heading == "Sources of law"
    assert a.hierarchy_path[0].level == "preliminary_title"
    assert a.breadcrumb.startswith("Preliminary Title")
    assert a.source_url.endswith("d=108528")
    assert a.source_html_hash.startswith("sha256:")
    assert len(a.acts_citations) == 1
    assert a.acts_citations[0].role == "enactment"
    assert a.acts_citations[0].effective_date == "1988-01-01"


def test_repealed_range_backfill_synthesises_missing_articles(orchestrate_setup):
    """CC 60 carries 'Arts. 60 to 85 repealed by ...' — 61-85 should be synthesised."""
    client, paths, containers, article_index = orchestrate_setup
    articles = run_phase3(client, paths, containers, article_index)
    assert articles["60"].status == "repealed"
    # 61..85 should also be present and repealed.
    for n in range(61, 86):
        assert str(n) in articles, f"backfill missed {n}"
        assert articles[str(n)].status == "repealed"
        assert articles[str(n)].text is None


def test_blank_articles_synthesised_from_lsu_ranges(orchestrate_setup):
    """LSU Chapter 3 covers 14-23; legis has 14 and 15 in our fixture set. 16-23 should be blank."""
    client, paths, containers, article_index = orchestrate_setup
    articles = run_phase3(client, paths, containers, article_index)
    for n in range(16, 24):
        assert str(n) in articles, f"blank fill missed CC {n}"
        assert articles[str(n)].status == "blank"
        assert articles[str(n)].text is None
        # blank fills carry a hierarchy from the enclosing chapter.
        assert articles[str(n)].hierarchy_path, f"CC {n} blank record has no hierarchy"


def test_breadcrumb_renders_legibly(orchestrate_setup):
    client, paths, containers, article_index = orchestrate_setup
    articles = run_phase3(client, paths, containers, article_index)
    bc = articles["3192"].breadcrumb
    assert "Book III" in bc and "Title XXI" in bc and "Chapter 3" in bc and "Section 1" in bc and "§1" in bc
    # spec uses U+203A right-pointing angle quotation as separator
    assert "›" in bc


def test_jsonl_validates_against_schema(orchestrate_setup):
    client, paths, containers, article_index = orchestrate_setup
    run_phase3(client, paths, containers, article_index)
    with paths.articles_jsonl.open() as f:
        for line in f:
            data = json.loads(line)
            Article.model_validate(data)  # round-trip


def test_article_number_in_urn_is_string(orchestrate_setup):
    client, paths, containers, article_index = orchestrate_setup
    articles = run_phase3(client, paths, containers, article_index)
    for n, a in articles.items():
        assert isinstance(a.article_number, str)
        assert a.urn == f"urn:us-la:civcode:art:{n}"
