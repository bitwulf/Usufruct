"""End-to-end Phase 1 + Phase 3 orchestrator test for the LRS pipeline.

Uses a ``FakeClient`` that serves Justia + legis fixtures locally — no
network access required.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import pytest

from usufruct.fetch.client import FetchResult, sha256_text
from usufruct.lrs.corpus import (
    JUSTIA_ROOT_URL,
    JUSTIA_TITLE_URL_TEMPLATE,
    LEGIS_SECTION_URL_TEMPLATE,
)
from usufruct.lrs.model import RSSection
from usufruct.lrs.pipeline import LRSPaths, run_phase1
from usufruct.lrs.pipeline.orchestrate import (
    run_phase2_with_index,
    run_phase3,
)

FIX = Path(__file__).parent / "fixtures" / "lrs"


def test_normalize_lrs_acts_text_converts_amended_by_period_to_semicolon():
    """legis serves two acts-line templates; LRS normalizes the period-
    separated form so the shared CC parser can split on ``;``."""
    from usufruct.lrs.pipeline.orchestrate import _normalize_lrs_acts_text
    from usufruct.parse import parse_acts_citation_line

    raw = "Acts 1958, No. 498, §1. Amended by Acts 1970, No. 465, §1."
    normalized = _normalize_lrs_acts_text(raw)
    assert normalized == "Acts 1958, No. 498, §1; Acts 1970, No. 465, §1."

    parsed = parse_acts_citation_line(normalized)
    assert len(parsed) == 2
    assert parsed[0].act_year == 1958 and parsed[0].role == "enactment"
    assert parsed[1].act_year == 1970 and parsed[1].role == "amendment"


def test_normalize_lrs_acts_text_passes_through_semicolon_form():
    """The 'modern' R.S. 14:30 form must round-trip unchanged."""
    from usufruct.lrs.pipeline.orchestrate import _normalize_lrs_acts_text

    raw = (
        "Amended by Acts 1973, No. 109, §1; Acts 1975, No. 327, §1; "
        "Acts 2025, No. 343, §1."
    )
    assert _normalize_lrs_acts_text(raw) == raw


def test_normalize_lrs_acts_text_handles_none_and_empty():
    from usufruct.lrs.pipeline.orchestrate import _normalize_lrs_acts_text

    assert _normalize_lrs_acts_text(None) is None
    assert _normalize_lrs_acts_text("") == ""


# A representative section_index for Title 14 — only the section we have
# legis HTML for. Real values for d= are arbitrary in tests; the fixture
# resolver below maps them to the saved HTML.
TEST_SECTION_IDS: Dict[Tuple[str, str], int] = {
    ("14", "30"): 78397,  # First degree murder (real d= value from rs-14-30.html)
}


@dataclass
class _FakeClient:
    """Serve fixtures by URL — Justia root, Justia per-Title, and legis sections.

    A real ``CachedClient`` interface is duck-typed here (only ``get`` is
    required by the orchestrator).
    """

    fixture_root: Path

    def get(self, url: str, force_refetch: bool = False) -> FetchResult:
        text = self._resolve(url)
        return FetchResult(
            url=url, text=text, sha256=sha256_text(text), from_cache=True
        )

    def _resolve(self, url: str) -> str:
        if url == JUSTIA_ROOT_URL:
            return (self.fixture_root / "justia" / "index.html").read_text()
        prefix = JUSTIA_TITLE_URL_TEMPLATE.split("{title}")[0]
        if url.startswith(prefix):
            title = url[len(prefix) :].rstrip("/")
            return (self.fixture_root / "justia" / f"title-{title}.html").read_text()
        if url.startswith("https://legis.la.gov/legis/Law.aspx?d="):
            d_value = int(url.split("d=")[-1])
            # Walk the legis_sections fixture dir and pick the one whose URL
            # ID matches. For now we have rs-14-30.html only.
            for path in (self.fixture_root / "legis_sections").iterdir():
                # Every fixture matches its (title, section) — we trust the
                # caller to pass valid d= values in TEST_SECTION_IDS.
                text = path.read_text()
                if f"d={d_value}" in text or f"HiddenDocId\" value=\"{d_value}\"" in text:
                    return text
            # Fall back: return rs-14-30.html for d=78397 specifically.
            if d_value == 78397:
                return (
                    self.fixture_root / "legis_sections" / "rs-14-30.html"
                ).read_text()
            raise FileNotFoundError(f"No fixture available for legis d={d_value}")
        raise FileNotFoundError(f"FakeClient has no fixture mapped for {url}")


@pytest.fixture
def lrs_setup(tmp_path):
    client = _FakeClient(fixture_root=FIX)
    paths = LRSPaths(root=tmp_path / "data")
    # Pilot mode: just Title 14 — we only have rs-14-30 as a section fixture.
    containers, justia_sections = run_phase1(client, paths, titles=["14"])
    return client, paths, containers, justia_sections


def test_phase1_writes_hierarchy_json(lrs_setup):
    client, paths, containers, justia_sections = lrs_setup
    assert paths.hierarchy.exists()
    raw = json.loads(paths.hierarchy.read_text())
    assert isinstance(raw, list)
    assert len(raw) == len(containers)
    assert any(c["level"] == "title" and c["number"] == "14" for c in raw)


def test_phase1_writes_justia_section_index(lrs_setup):
    client, paths, containers, justia_sections = lrs_setup
    assert paths.justia_section_index.exists()
    raw = json.loads(paths.justia_section_index.read_text())
    # Title 14 has 711 sections in the current fixture
    assert len(raw) == len(justia_sections)
    sects = {s["section_number"] for s in raw}
    assert "30" in sects and "47" in sects


def test_phase1_assigns_section_ranges(lrs_setup):
    client, paths, containers, justia_sections = lrs_setup
    # At least one container (Chapter 1) should have a known range; check
    # the Title 14 container directly.
    title14 = next(c for c in containers if c.level == "title" and c.number == "14")
    assert title14.section_range_start is not None
    assert title14.section_range_end is not None


def test_phase3_emits_rs_14_30_record(lrs_setup):
    client, paths, containers, justia_sections = lrs_setup
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
    urn = "urn:us-la:rs:14:30"
    assert urn in sections
    rec: RSSection = sections[urn]
    assert rec.status == "active"
    assert rec.title_number == "14"
    assert rec.section_number == "30"
    assert rec.citation == "R.S. 14:30"
    assert rec.heading == "First degree murder"
    assert rec.text and rec.text.startswith("A.")
    assert rec.acts_citations, "expected at least one parsed acts citation"
    assert rec.acts_citations[0].role == "enactment"
    assert rec.source_url and rec.source_url.endswith("d=78397")
    assert rec.source_html_hash and rec.source_html_hash.startswith("sha256:")
    # Hierarchy must reach at least the Title
    assert any(node.level == "title" and node.number == "14" for node in rec.hierarchy_path)


def test_phase3_writes_jsonl_and_per_section_files(lrs_setup):
    client, paths, containers, justia_sections = lrs_setup
    section_index = run_phase2_with_index(
        paths,
        justia_sections=justia_sections,
        legis_section_ids=TEST_SECTION_IDS,
    )
    run_phase3(
        client,
        paths,
        containers=containers,
        justia_sections=justia_sections,
        section_index=section_index,
    )
    assert paths.sections_jsonl.exists()
    # Should contain one record per emitted section (including synthesized
    # blanks for the 710 sections we didn't fetch).
    with paths.sections_jsonl.open() as f:
        lines = [line for line in f if line.strip()]
    assert lines
    # Every line must round-trip through the Pydantic schema.
    for line in lines:
        RSSection.model_validate_json(line)


def test_phase3_synthesizes_records_for_unmapped_sections(lrs_setup):
    """Sections in Justia but not in our (small) section_index get a blank/repealed stub."""
    client, paths, containers, justia_sections = lrs_setup
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
    # 14:47 is Justia-flagged repealed
    rec_47 = sections.get("urn:us-la:rs:14:47")
    assert rec_47 is not None
    assert rec_47.status == "repealed"
    assert rec_47.text is None
    # A non-repealed section without legis ID becomes "blank"
    rec_1 = sections.get("urn:us-la:rs:14:1")
    assert rec_1 is not None
    assert rec_1.status == "blank"


def test_validation_report_and_manifest_written(lrs_setup):
    client, paths, containers, justia_sections = lrs_setup
    section_index = run_phase2_with_index(
        paths,
        justia_sections=justia_sections,
        legis_section_ids=TEST_SECTION_IDS,
    )
    run_phase3(
        client,
        paths,
        containers=containers,
        justia_sections=justia_sections,
        section_index=section_index,
    )
    assert paths.manifest.exists()
    assert paths.validation_report.exists()
    manifest = json.loads(paths.manifest.read_text())
    assert manifest["corpus"] == "rs"
    assert "by_status" in manifest["totals"]
