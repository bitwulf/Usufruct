"""Phase 4 derived artifacts — tree, citation edges, RAG chunks, markdown.

Reuses the orchestrate test setup: a small fixture corpus of 13 articles plus
the LSU/legis TOC fixtures is fed through Phase 3, then Phase 4 derives the
four artifacts and we assert their shape, contents, and counts.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict

import pytest

from usufruct.model import Article
from usufruct.parse import parse_legis_toc, parse_lsu_toc
from usufruct.pipeline.chunks import build_chunks
from usufruct.pipeline.citations import extract_edges
from usufruct.pipeline.markdown import render_article
from usufruct.pipeline.orchestrate import PipelinePaths, run_phase3, run_phase4
from usufruct.pipeline.tree import build_tree, max_depth

from test_orchestrate import FakeClient, TEST_ARTICLES


@pytest.fixture
def phase4_outputs(tmp_path, lsu_toc_html, legis_toc_html):
    containers = parse_lsu_toc(lsu_toc_html)
    article_index = parse_legis_toc(legis_toc_html)
    article_index = {k: article_index[k] for k in TEST_ARTICLES if k in article_index}
    paths = PipelinePaths(root=tmp_path / "data")
    client = FakeClient(article_index)
    articles = run_phase3(client, paths, containers, article_index)
    stats = run_phase4(paths, containers, articles)
    return paths, containers, articles, stats


def test_phase4_emits_all_artifacts(phase4_outputs):
    paths, _, _, _ = phase4_outputs
    assert paths.tree.exists()
    assert paths.citation_edges.exists()
    assert paths.chunks.exists()
    assert paths.markdown_dir.exists()


def test_tree_roots_cover_preliminary_title_and_all_books(phase4_outputs):
    paths, _, _, _ = phase4_outputs
    tree = json.loads(paths.tree.read_text())
    labels = {(r["level"], r["number"]) for r in tree["roots"]}
    # All four books plus the preliminary title should be roots
    assert ("preliminary_title", "Preliminary Title") in labels
    assert ("book", "I") in labels
    assert ("book", "II") in labels
    assert ("book", "III") in labels
    assert ("book", "IV") in labels


def test_tree_assigns_articles_to_their_owning_container(phase4_outputs):
    paths, _, _, _ = phase4_outputs
    tree = json.loads(paths.tree.read_text())

    def find(node, target_articles):
        if any(n in node.get("articles", []) for n in target_articles):
            return node
        for child in node.get("children", []):
            hit = find(child, target_articles)
            if hit is not None:
                return hit
        return None

    # CC 3192 is documented in BUILDHISTORY as five-deep: Book III › Title XXI › Chapter 3 › Section 1 › §1
    for root in tree["roots"]:
        owner = find(root, ["3192"])
        if owner is not None:
            assert owner["level"] == "paragraph"
            assert owner["number"] == "1"
            return
    pytest.fail("CC 3192 not placed in any tree container")


def test_tree_max_depth_matches_fixture_corpus(phase4_outputs):
    paths, _, _, stats = phase4_outputs
    tree = json.loads(paths.tree.read_text())
    # Builder helper and the orchestrator stats must agree
    assert max_depth(tree) == stats["tree_max_depth"]
    # Our fixture set includes CC 3192 (§ depth) and CC 185 (subsection depth) —
    # max depth must be at least 5 (book → title → chapter → section → §|subsection).
    assert stats["tree_max_depth"] >= 5


def test_citation_edges_extract_compound_references(phase4_outputs):
    paths, _, articles, _ = phase4_outputs
    # CC 103.1 contains "in accordance with Articles 102 and 103"
    edges = extract_edges(articles["103.1"])
    dests = {e.dst_article for e in edges}
    assert "102" in dests
    assert "103" in dests
    # And every edge should label CC 103.1 as the src
    assert all(e.src_article == "103.1" for e in edges)


def test_citation_edges_csv_has_header_and_rows(phase4_outputs):
    paths, _, _, _ = phase4_outputs
    with paths.citation_edges.open() as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["src_urn", "src_article", "dst_article", "raw_match", "char_offset"]
    assert any(r[1] == "103.1" for r in rows[1:])


def test_chunks_skip_non_active_articles(phase4_outputs):
    paths, _, articles, _ = phase4_outputs
    chunks = [json.loads(line) for line in paths.chunks.open()]
    chunk_numbers = {c["article_number"] for c in chunks}
    # Active fixture articles → present
    assert "2315" in chunk_numbers
    assert "103.1" in chunk_numbers
    # Repealed (CC 60) and synthesised blanks (CC 16-23) → absent
    assert "60" not in chunk_numbers
    assert "16" not in chunk_numbers
    # Counts: chunks == active articles
    expected = sum(1 for a in articles.values() if a.status == "active" and a.text)
    assert len(chunks) == expected


def test_chunks_have_prev_next_neighbours(phase4_outputs):
    paths, _, articles, _ = phase4_outputs
    chunks = build_chunks(articles.values())
    # First chunk has no prev; last chunk has no next
    chunks_sorted = sorted(chunks, key=lambda c: c["article_number"])  # not the real order, but sufficient for spot-checks
    first = next(c for c in chunks if c["prev_article"] is None)
    last = next(c for c in chunks if c["next_article"] is None)
    assert first["article_number"] == "1"
    assert last["article_number"]  # any article — just non-empty


def test_markdown_frontmatter_round_trips_for_active_article(phase4_outputs):
    paths, _, articles, _ = phase4_outputs
    md = (paths.markdown_dir / "2315.md").read_text()
    assert md.startswith("---\n")
    assert 'urn: "urn:us-la:civcode:art:2315"' in md
    assert "status: active" in md
    assert "heading:" in md
    assert "# Art. 2315." in md
    assert "Every act whatever of man" in md  # body text


def test_markdown_stubs_emitted_for_repealed_and_blank(phase4_outputs):
    paths, _, _, _ = phase4_outputs
    repealed = (paths.markdown_dir / "60.md").read_text()
    assert "status: repealed" in repealed
    assert "*Repealed.*" in repealed
    blank = (paths.markdown_dir / "16.md").read_text()
    assert "status: blank" in blank
    assert "*Blank" in blank


def test_render_article_escapes_quotes_in_frontmatter():
    a = Article(
        urn="urn:us-la:civcode:art:9999",
        article_number="9999",
        heading='He said "hi"',
        text="Body.",
        status="active",
    )
    out = render_article(a)
    assert r'heading: "He said \"hi\""' in out


def test_manifest_completeness_section(phase4_outputs):
    paths, _, _, _ = phase4_outputs
    manifest = json.loads(paths.manifest.read_text())
    comp = manifest["completeness"]
    assert "by_book" in comp
    assert comp["rag_chunks"] >= 1
    assert comp["markdown_files"] >= 1
    assert comp["tree_max_depth"] >= 5
    # by_book sums match the totals
    by_book_total = sum(b["total"] for b in comp["by_book"])
    assert by_book_total == manifest["totals"]["articles_emitted"]


def test_build_tree_pure_function_matches_disk_output(phase4_outputs):
    paths, containers, articles, _ = phase4_outputs
    article_list = list(articles.values())
    tree = build_tree(containers, article_list)
    on_disk = json.loads(paths.tree.read_text())
    # generated_at timestamp differs by call; compare structure only
    assert [r["name"] for r in tree["roots"]] == [r["name"] for r in on_disk["roots"]]
