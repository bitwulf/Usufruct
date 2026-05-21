"""Phase 4 derived-artifact tests for the LRS pipeline.

Reuses the Phase 1+3 fixture setup from test_lrs_orchestrate and then walks
the artifacts that ``run_phase4`` writes.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Tuple

import pytest

from usufruct.lrs.pipeline import LRSPaths, run_phase1, run_phase4
from usufruct.lrs.pipeline.orchestrate import run_phase2_with_index, run_phase3

# Reuse the FakeClient from the orchestrate test module.
from test_lrs_orchestrate import FIX, TEST_SECTION_IDS, _FakeClient


@pytest.fixture
def phase4_setup(tmp_path):
    client = _FakeClient(fixture_root=FIX)
    paths = LRSPaths(root=tmp_path / "data")
    containers, justia_sections = run_phase1(client, paths, titles=["14"])
    section_index = run_phase2_with_index(
        paths,
        justia_sections=justia_sections,
        legis_section_ids=TEST_SECTION_IDS,
    )
    sections = run_phase3(
        client,
        paths,
        containers=containers,
        justia_sections=justia_sections,
        section_index=section_index,
    )
    stats = run_phase4(paths, containers, sections)
    return paths, containers, sections, stats


def test_tree_json_written_and_validates(phase4_setup):
    paths, _, _, stats = phase4_setup
    assert paths.tree.exists()
    tree = json.loads(paths.tree.read_text())
    assert tree["schema_version"]
    # Title 14 should be a root.
    assert any(
        r["level"] == "title" and r["number"] == "14" for r in tree["roots"]
    )
    # Max depth at least 4 (title → chapter → part → subpart).
    assert stats["tree_max_depth"] >= 4


def test_citation_edges_csv_header_and_at_least_one_rs_edge(phase4_setup):
    paths, _, _, stats = phase4_setup
    assert paths.citation_edges.exists()
    with paths.citation_edges.open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames
    assert fieldnames == [
        "src_urn",
        "src_corpus",
        "src_id",
        "dst_corpus",
        "dst_id",
        "dst_urn",
        "raw_match",
        "char_offset",
    ]
    # R.S. 14:30 cites R.S. 14:107.1(C)(1) → at least one rs→rs edge.
    rs_edges = [r for r in rows if r["dst_corpus"] == "rs"]
    assert rs_edges, "expected at least one intra-LRS citation edge"
    # The 14:30 body also references "Code of Criminal Procedure Article 782"
    crp_edges = [r for r in rows if r["dst_corpus"] == "crp"]
    assert crp_edges, "expected at least one R.S. → CCP/CrP citation edge"


def test_chunks_jsonl_only_active(phase4_setup):
    paths, _, sections, _ = phase4_setup
    with paths.chunks.open() as f:
        chunks = [json.loads(line) for line in f if line.strip()]
    # All chunks should have an active source record.
    active_urns = {urn for urn, rec in sections.items() if rec.status == "active"}
    chunk_urns = {c["urn"] for c in chunks}
    assert chunk_urns.issubset(active_urns)
    # Every chunk has prev/next pointers (set or None).
    for c in chunks:
        assert "prev_urn" in c and "next_urn" in c


def test_markdown_file_per_section(phase4_setup):
    paths, _, sections, _ = phase4_setup
    files = list(paths.markdown_dir.rglob("*.md"))
    assert len(files) == len(sections)


def test_markdown_for_active_section_has_body(phase4_setup):
    paths, _, _, _ = phase4_setup
    md = (paths.markdown_dir / "title-14" / "30.md").read_text()
    assert md.startswith("---")
    assert "urn: urn:us-la:rs:14:30" in md
    assert "status: active" in md
    assert "First degree murder" in md


def test_markdown_for_repealed_section_has_stub(phase4_setup):
    paths, _, sections, _ = phase4_setup
    # 14:47 is Justia-flagged repealed.
    md = (paths.markdown_dir / "title-14" / "47.md").read_text()
    assert "status: repealed" in md
    assert "Repealed" in md


def test_manifest_completeness_block_added(phase4_setup):
    paths, _, sections, _ = phase4_setup
    manifest = json.loads(paths.manifest.read_text())
    assert "completeness" in manifest
    by_title = manifest["completeness"]["by_title"]
    assert any(b["title"] == "14" for b in by_title)
    # Sum of all status buckets should match emitted sections count for Title 14.
    title14 = next(b for b in by_title if b["title"] == "14")
    assert title14["total"] == sum(
        1 for rec in sections.values() if rec.title_number == "14"
    )
