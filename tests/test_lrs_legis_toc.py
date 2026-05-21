"""Unit tests for the legis.la.gov LRS TOC parsers + Phase 2 fetcher.

Legis's root TOC uses ASP.NET postback links for Title rows — the folder
ID is *not* in the HTML. We discover folder IDs deterministically from
the Justia Title list (Title 1 → folder 77, sequential). Tests verify
both pieces: the parsers and the fetcher's folder derivation + per-Title
verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import json
import pytest

from usufruct.fetch.client import FetchResult, sha256_text
from usufruct.lrs.corpus import (
    LEGIS_LRS_ROOT_TOC_URL,
    LEGIS_LRS_TITLE_TOC_URL_TEMPLATE,
)
from usufruct.lrs.parse import (
    parse_legis_root_toc,
    parse_legis_root_toc_titles,
    parse_legis_title_toc,
)
from usufruct.lrs.parse import JustiaSectionEntry
from usufruct.lrs.pipeline import LRSPaths, run_phase2_fetch


# A root TOC modeled on the real page: anchors with text "TITLE N" pointing
# to ASP.NET postback JavaScript URLs (no folder ID in HTML). Synthetic
# Justia list has Titles 1, 9, 14, 47 with the same gaps as the real corpus.
_ROOT_HTML = """
<html><body>
<table>
<tr><td><a id="ctl0" href="javascript:__doPostBack(...)">TITLE 1</a></td>
    <td><a id="ctl0b" href="javascript:__doPostBack(...)">General Provisions</a></td></tr>
<tr><td><a id="ctl1" href="javascript:__doPostBack(...)">TITLE 9</a></td>
    <td><a id="ctl1b" href="javascript:__doPostBack(...)">Civil Code-Ancillaries</a></td></tr>
<tr><td><a id="ctl2" href="javascript:__doPostBack(...)">TITLE 14</a></td>
    <td><a id="ctl2b" href="javascript:__doPostBack(...)">Criminal Law</a></td></tr>
<tr><td><a id="ctl3" href="javascript:__doPostBack(...)">TITLE 47</a></td>
    <td><a id="ctl3b" href="javascript:__doPostBack(...)">Revenue and Taxation</a></td></tr>
</table>
</body></html>
"""

# Per-Title TOC: Law.aspx?d= anchors with text "RS T:S" (sections) and one
# "RS T" anchor at the top for the Title-level overview (must be filtered).
_TITLE_1_HTML = """
<html><body>
<a href="Law.aspx?d=74078">RS 1</a>
<a href="Law.aspx?d=74078">TITLE 1. GENERAL PROVISIONS</a>
<a href="Law.aspx?d=74079">RS 1:1</a>
<a href="Law.aspx?d=74079">Title of Code</a>
<a href="Law.aspx?d=74089">RS 1:2 - Public laws</a>
<a href="Law.aspx?d=74090">RS 1:11.1</a>
</body></html>
"""

_TITLE_14_HTML = """
<html><body>
<a href="Law.aspx?d=78223">RS 14</a>
<a href="Law.aspx?d=78223">TITLE 14. CRIMINAL LAW</a>
<a href="Law.aspx?d=78397">RS 14:30</a>
<a href="Law.aspx?d=78397">First degree murder</a>
<a href="Law.aspx?d=78398">RS 14:30.1 - Second degree murder</a>
</body></html>
"""

_TITLE_9_HTML = """
<html><body>
<a href="Law.aspx?d=72000">RS 9</a>
<a href="Law.aspx?d=72100">RS 9:2800</a>
</body></html>
"""

_TITLE_47_HTML = """
<html><body>
<a href="Law.aspx?d=99000">RS 47</a>
<a href="Law.aspx?d=99001">RS 47:1</a>
</body></html>
"""

# A TOC fixture intentionally containing a foreign-Title anchor — used to
# verify the fetcher's per-Title cross-check fails loudly when present.
_TITLE_1_HTML_WITH_FOREIGN = """
<html><body>
<a href="Law.aspx?d=74079">RS 1:1</a>
<a href="Law.aspx?d=99999">RS 99:1</a>
</body></html>
"""


def test_root_toc_parser_titles_extracts_title_numbers():
    titles = parse_legis_root_toc_titles(_ROOT_HTML)
    assert titles == ["1", "9", "14", "47"]


def test_root_toc_parser_titles_dedups_and_ignores_non_title_anchors():
    html = """
    <html><body>
      <a>TITLE 1</a>
      <a>TITLE 1</a>  <!-- duplicate -->
      <a>Section 1</a>
      <a>TITLE 14-A</a>
      <a>Random text</a>
    </body></html>
    """
    titles = parse_legis_root_toc_titles(html)
    assert titles == ["1", "14-A"]


def test_root_toc_parser_returns_empty_for_real_legis_html_pattern():
    """The original folder-anchor parser returns {} for ASP.NET postback HTML."""
    assert parse_legis_root_toc(_ROOT_HTML) == {}


def test_title_toc_parser_extracts_section_to_d_map_and_skips_title_level():
    ids = parse_legis_title_toc(_TITLE_1_HTML)
    # Title-level "RS 1" anchor (no colon) must NOT appear.
    keys = set(ids.keys())
    assert ("1", "1") in keys
    assert ("1", "2") in keys
    assert ("1", "11.1") in keys
    # Make sure nothing got a numeric-only key from "RS 1"
    assert not any(s == "" for _, s in keys)


def test_title_toc_parser_handles_dash_and_heading_suffixes():
    ids = parse_legis_title_toc(_TITLE_14_HTML)
    assert ids[("14", "30")] == 78397
    assert ids[("14", "30.1")] == 78398


@dataclass
class _TocFakeClient:
    """Serve only legis TOC URLs by URL pattern."""

    def get(self, url: str, force_refetch: bool = False) -> FetchResult:
        if url == LEGIS_LRS_ROOT_TOC_URL:
            return _result(url, _ROOT_HTML)
        # Per-Title TOC URLs. The folder derivation in run_phase2_fetch
        # assigns Title 1 -> 77, Title 9 -> 78, Title 14 -> 79, Title 47 -> 80
        # for THIS synthetic fixture (which only has 4 Titles), because the
        # mapping is by sorted index in the Justia-known set.
        for folder, title, html in (
            (77, "1", _TITLE_1_HTML),
            (78, "9", _TITLE_9_HTML),
            (79, "14", _TITLE_14_HTML),
            (80, "47", _TITLE_47_HTML),
        ):
            if url == LEGIS_LRS_TITLE_TOC_URL_TEMPLATE.format(folder=folder, title=title):
                return _result(url, html)
        raise FileNotFoundError(f"No TOC fixture for {url}")


def _result(url: str, text: str) -> FetchResult:
    return FetchResult(url=url, text=text, sha256=sha256_text(text), from_cache=True)


def _justia_sections() -> list[JustiaSectionEntry]:
    return [
        JustiaSectionEntry(
            title_number="1",
            section_number="1",
            heading="Title of Code",
            repealed=False,
            repeal_note=None,
            container_chain=[("title", "1", "GENERAL PROVISIONS")],
        ),
        JustiaSectionEntry(
            title_number="1",
            section_number="2",
            heading=None,
            repealed=False,
            repeal_note=None,
            container_chain=[("title", "1", "GENERAL PROVISIONS")],
        ),
        JustiaSectionEntry(
            title_number="9",
            section_number="2800",
            heading=None,
            repealed=False,
            repeal_note=None,
            container_chain=[("title", "9", "CIVIL CODE-ANCILLARIES")],
        ),
        JustiaSectionEntry(
            title_number="14",
            section_number="30",
            heading="First degree murder",
            repealed=False,
            repeal_note=None,
            container_chain=[("title", "14", "CRIMINAL LAW")],
        ),
        JustiaSectionEntry(
            title_number="14",
            section_number="999",  # Justia-only — surfaces a gap
            heading="Phantom",
            repealed=False,
            repeal_note=None,
            container_chain=[("title", "14", "CRIMINAL LAW")],
        ),
        JustiaSectionEntry(
            title_number="47",
            section_number="1",
            heading=None,
            repealed=False,
            repeal_note=None,
            container_chain=[("title", "47", "REVENUE AND TAXATION")],
        ),
    ]


def test_run_phase2_fetch_walks_all_titles_by_default(tmp_path):
    client = _TocFakeClient()
    paths = LRSPaths(root=tmp_path / "data")
    justia = _justia_sections()

    section_index = run_phase2_fetch(client, paths, justia_sections=justia)

    # Joined entries must include the Justia-known sections that legis confirms.
    assert section_index[("1", "1")] == 74079
    assert section_index[("14", "30")] == 78397
    assert section_index[("47", "1")] == 99001
    # 14:999 is Justia-only; not in section_index.
    assert ("14", "999") not in section_index

    folder_map = json.loads((paths.rs_root / "folder_map.json").read_text())
    # Derived deterministically: Titles sorted are 1,9,14,47 → 77,78,79,80.
    assert folder_map == {"1": 77, "9": 78, "14": 79, "47": 80}


def test_run_phase2_fetch_titles_filter_scopes_per_title_fetches(tmp_path):
    client = _TocFakeClient()
    paths = LRSPaths(root=tmp_path / "data")
    justia = _justia_sections()

    section_index = run_phase2_fetch(
        client, paths, justia_sections=justia, titles=["1"]
    )

    assert ("1", "1") in section_index
    # Other Titles' TOCs were not fetched, so their sections do not appear.
    assert ("14", "30") not in section_index
    assert ("47", "1") not in section_index
    # folder_map still reflects all known Titles (it's an inventory, not the
    # fetch scope).
    folder_map = json.loads((paths.rs_root / "folder_map.json").read_text())
    assert folder_map == {"1": 77, "9": 78, "14": 79, "47": 80}


def test_run_phase2_fetch_emits_gap_file_when_justia_legis_disagree(tmp_path):
    client = _TocFakeClient()
    paths = LRSPaths(root=tmp_path / "data")
    justia = _justia_sections()

    run_phase2_fetch(client, paths, justia_sections=justia)

    gaps = json.loads((paths.rs_root / "phase2_gaps.json").read_text())
    assert ["14", "999"] in gaps["in_justia_not_legis"]
    # Title 1's legis fixture has 1:11.1 which we deliberately omitted from
    # the synthetic Justia list → it should appear as a Justia-side gap.
    legis_only = {tuple(p) for p in gaps["in_legis_not_justia"]}
    assert ("1", "11.1") in legis_only


def test_run_phase2_fetch_raises_on_foreign_title_in_toc(tmp_path):
    """Cross-Title contamination must fail loudly (folder remap detection)."""

    class _ForeignTitleClient:
        def get(self, url, force_refetch=False):
            if url == LEGIS_LRS_ROOT_TOC_URL:
                return _result(url, _ROOT_HTML)
            # When Title 1's TOC is requested, return a contaminated fixture.
            if "title=1&" in url or url.endswith("title=1&level=Parent"):
                return _result(url, _TITLE_1_HTML_WITH_FOREIGN)
            return _result(url, "")

    paths = LRSPaths(root=tmp_path / "data")
    with pytest.raises(RuntimeError, match="folder mismatch"):
        run_phase2_fetch(
            _ForeignTitleClient(),
            paths,
            justia_sections=_justia_sections(),
            titles=["1"],
        )
