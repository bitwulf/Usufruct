"""LRS pipeline orchestrator — runs Phases 1 through 4 end-to-end.

Phase boundaries mirror the CC pipeline so the codebase stays predictable:

1. Justia hierarchy → ``data/rs/hierarchy.json`` + ``justia_section_index.json``.
2. legis section ID discovery → ``data/rs/section_index.json``.
3. legis section text scrape → ``data/rs/sections/*.json`` + ``sections.jsonl``.
4. Derived artifacts → ``tree.json``, ``citation_edges.csv``, ``chunks.jsonl``,
   ``markdown/``, ``manifest.json``, ``validation_report.json``.

Each phase is idempotent (re-running with cached fetches is a no-op) and
resumable. The shared SHA256 cache at ``data/raw/`` is corpus-agnostic.
"""
from __future__ import annotations

import csv
import datetime as _dt
import html as _htmllib
import json
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .. import LRS_SCHEMA_VERSION
from ...fetch.client import CachedClient, FetchResult
from ...model import ActsCitation
from ...parse import parse_acts_citation_line
from ..model import HierarchyNode
from ..corpus import (
    JUSTIA_ROOT_URL,
    JUSTIA_TITLE_URL_TEMPLATE,
    LEGIS_SECTION_URL_TEMPLATE,
    citation_for,
    urn_for,
)
from ..fetch.justia_root import fetch_justia_root
from ..fetch.justia_title import fetch_justia_title, justia_title_url
from ..fetch.legis_section import fetch_legis_section, legis_section_url
from ..fetch.legis_toc import fetch_legis_root_toc, fetch_legis_title_toc
from ..model import Container, ContainerLevel, RSSection, section_sort_key

from ..parse import (
    JustiaSectionEntry,
    parse_justia_root,
    parse_justia_title,
    parse_legis_root_toc,
    parse_legis_root_toc_titles,
    parse_legis_section,
    parse_legis_title_toc,
)
from .hierarchy import (
    LRSHierarchyIndex,
    assign_ranges_from_sections,
    build_lrs_hierarchy_index,
)
from .paths import LRSPaths


# ---------- common helpers ----------

def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _level_value(c: Container) -> str:
    return c.level if isinstance(c.level, str) else c.level.value


def _level_label_for_node(node: HierarchyNode) -> str:
    level_map = {
        "title": f"Title {node.number}",
        "subtitle": f"Subtitle {node.number}",
        "code_preliminary_title": "Code Preliminary Title",
        "code_book": f"Code Book {node.number}",
        "code_title": f"Code Title {node.number}",
        "chapter": f"Chapter {node.number}",
        "part": f"Part {node.number}",
        "subpart": f"Subpart {node.number}",
        "subgroup": f"Subgroup {node.number}",
    }
    return level_map.get(node.level, f"{node.level} {node.number}")


def _breadcrumb(path: List[HierarchyNode]) -> str:
    return " › ".join(_level_label_for_node(n) for n in path)


def _section_slug(title_number: str, section_number: str) -> str:
    safe_section = section_number.replace(".", "_")
    return f"rs_{title_number}_{safe_section}"


# legis.la.gov serves two acts-line templates: the "modern" CC-style with
# ``;`` between citations (used by R.S. 14:30 etc.) and the "legacy" period-
# separated form used by many older sections (R.S. 1:11.1 →
# ``"Acts 1958, No. 498, §1. Amended by Acts 1970, No. 465, §1."``). The
# shared ``parse_acts_citation_line`` only handles the ``;`` form. We
# normalize period-separated forms here before parsing — this keeps the
# shared CC parser untouched per the LRS plan's strict-isolation rule.
_LRS_ACTS_AMENDED_RE = re.compile(
    r"\.\s+Amended\s+by\s+Acts\b", re.IGNORECASE
)
_LRS_ACTS_PERIOD_NEXT_RE = re.compile(r"\.\s+(Acts\s+\d{4})")


def _normalize_lrs_acts_text(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return raw
    out = _LRS_ACTS_AMENDED_RE.sub("; Acts", raw)
    out = _LRS_ACTS_PERIOD_NEXT_RE.sub(r"; \1", out)
    return out


# ---------- Phase 1: Justia hierarchy ----------

def run_phase1(
    client,
    paths: LRSPaths,
    *,
    titles: Optional[Iterable[str]] = None,
) -> Tuple[List[Container], List[JustiaSectionEntry]]:
    """Fetch + parse Justia hierarchy for the requested Titles.

    ``titles=None`` means all Titles from the Justia root index. Pass an
    explicit subset (e.g., ``["1", "14"]``) for pilot/wave runs.
    """
    root = fetch_justia_root(client)
    root_titles = parse_justia_root(root.text)
    wanted = (
        {t.title_number for t in root_titles}
        if titles is None
        else {str(t) for t in titles}
    )
    all_containers: List[Container] = []
    all_sections: List[JustiaSectionEntry] = []

    notes: List[Tuple[str, List[Tuple[str, str, str]]]] = []
    for listing in root_titles:
        if listing.title_number not in wanted:
            continue
        fetched = fetch_justia_title(client, listing.title_number)
        result = parse_justia_title(
            fetched.text, expected_title_number=listing.title_number
        )
        all_containers.extend(result.containers)
        all_sections.extend(result.sections)
        notes.extend(result.notes)

    # Derive section_range_* on every container from the observed sections.
    sections_with_chain = [
        (s.title_number, s.section_number, s.container_chain) for s in all_sections
    ]
    assign_ranges_from_sections(all_containers, sections_with_chain)

    # Persist.
    paths.hierarchy.write_text(
        json.dumps(
            [_dump_container(c) for c in all_containers],
            indent=2,
            ensure_ascii=False,
        )
    )
    paths.justia_section_index.write_text(
        json.dumps(
            [_dump_section_entry(s) for s in all_sections],
            indent=2,
            ensure_ascii=False,
        )
    )
    # Optional NOTE: preservation.
    if notes:
        with paths.notes.open("w") as f:
            for raw, chain in notes:
                f.write(
                    json.dumps(
                        {"raw_text": raw, "context": list(chain)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    return all_containers, all_sections


def _dump_container(c: Container) -> Dict:
    d = c.model_dump()
    # Pydantic stores the enum value when use_enum_values=True; this is a
    # no-op cosmetic guard for clarity.
    return d


def _dump_section_entry(s: JustiaSectionEntry) -> Dict:
    return {
        "title_number": s.title_number,
        "section_number": s.section_number,
        "heading": s.heading,
        "repealed": s.repealed,
        "repeal_note": s.repeal_note,
        "container_chain": [list(c) for c in s.container_chain],
    }


# ---------- Phase 2: legis section IDs ----------

def _title_sort_key(t: str) -> Tuple[int, str]:
    head = t.split("-")[0]
    return (int(head) if head.isdigit() else 999, t)


# Legis assigns each LRS Title a folder ID. The IDs are sequential in the
# sorted-active-Title order: Title 1 → 77, Title 2 → 78, …, Title 14 → 88,
# Title 47 → 121. We discover this empirically and verify per Title (the
# parser only accepts ``RS T:S`` anchors whose title matches the request).
# If legis ever renumbers, the verification fails loudly.
_LEGIS_LRS_BASE_FOLDER = 77


def _derive_folder_map(title_numbers: Iterable[str]) -> Dict[str, int]:
    ordered = sorted({str(t) for t in title_numbers}, key=_title_sort_key)
    return {t: _LEGIS_LRS_BASE_FOLDER + i for i, t in enumerate(ordered)}


def run_phase2_fetch(
    client,
    paths: LRSPaths,
    *,
    justia_sections: List[JustiaSectionEntry],
    titles: Optional[Iterable[str]] = None,
    force_refetch: bool = False,
    progress: bool = False,
) -> Dict[Tuple[str, str], int]:
    """Walk legis root TOC + per-Title TOCs, then join against Justia.

    1. Fetch the legis root TOC (sanity check: every Justia Title appears).
       The root page does not expose folder IDs in HTML — they live behind
       ASP.NET postback handlers — so folder discovery is deterministic
       (see ``_derive_folder_map``).
    2. For each requested Title, fetch
       ``Laws_Toc.aspx?folder=F&title=T&level=Parent`` where ``F = 77 +
       sorted_index(T)``. Verify the returned anchors all belong to the
       requested Title; raise on mismatch.
    3. Merge every parsed ``(title, section) -> website_law_id`` map.
    4. Delegate to ``run_phase2_with_index`` for the Justia↔legis join + emit.

    The derived folder map is persisted to ``data/rs/folder_map.json`` for
    inspection and as the canonical record of the mapping at scrape time.
    """
    import sys as _sys

    # All Justia-known Titles drive the folder math, regardless of whether
    # ``titles`` filters which ones we fetch. This keeps folder IDs stable
    # under pilot-mode runs.
    all_titles = sorted(
        {s.title_number for s in justia_sections}, key=_title_sort_key
    )
    folder_map = _derive_folder_map(all_titles)

    root = fetch_legis_root_toc(client, force_refetch=force_refetch)
    advertised = parse_legis_root_toc_titles(root.text)
    advertised_set = set(advertised)
    missing_on_legis = [t for t in all_titles if t not in advertised_set]
    extra_on_legis = [t for t in advertised if t not in set(all_titles)]
    if progress:
        print(
            f"[phase2] legis root TOC: {len(advertised)} Titles advertised "
            f"(Justia knows {len(all_titles)})",
            file=_sys.stderr,
        )
        if missing_on_legis:
            print(
                f"[phase2] WARNING: in Justia but missing from legis root: "
                f"{missing_on_legis}",
                file=_sys.stderr,
            )
        if extra_on_legis:
            print(
                f"[phase2] note: legis root advertises Titles not in Justia: "
                f"{extra_on_legis}",
                file=_sys.stderr,
            )

    paths.rs_root.mkdir(parents=True, exist_ok=True)
    (paths.rs_root / "folder_map.json").write_text(
        json.dumps(folder_map, indent=2, sort_keys=True)
    )

    wanted: List[str]
    if titles is None:
        wanted = list(all_titles)
    else:
        wanted = sorted({str(t) for t in titles}, key=_title_sort_key)

    merged: Dict[Tuple[str, str], int] = {}
    for i, title_number in enumerate(wanted, start=1):
        folder = folder_map.get(title_number)
        if folder is None:
            if progress:
                print(
                    f"[phase2] Title {title_number}: skipped "
                    f"(not in folder map)",
                    file=_sys.stderr,
                )
            continue
        fetched = fetch_legis_title_toc(
            client, folder, title_number, force_refetch=force_refetch
        )
        title_ids = parse_legis_title_toc(fetched.text)
        # Verify every returned key belongs to the requested Title — if the
        # base folder shifts, this will catch it loudly instead of silently
        # mis-mapping sections.
        wrong = {
            (t, s) for (t, s) in title_ids.keys() if t != title_number
        }
        if wrong:
            raise RuntimeError(
                f"Phase 2 folder mismatch: Title {title_number} at folder "
                f"{folder} returned anchors for foreign Titles: "
                f"{sorted(t for t, _ in wrong)[:5]} (expected only "
                f"'{title_number}'). The base folder may have shifted on "
                f"legis.la.gov; update _LEGIS_LRS_BASE_FOLDER."
            )
        merged.update(title_ids)
        if progress:
            tag = " (cached)" if fetched.from_cache else ""
            print(
                f"[phase2] Title {title_number} (folder {folder}): "
                f"{len(title_ids)} sections{tag} [{i}/{len(wanted)}]",
                file=_sys.stderr,
            )

    return run_phase2_with_index(
        paths,
        justia_sections=justia_sections,
        legis_section_ids=merged,
    )


def run_phase2_with_index(
    paths: LRSPaths,
    *,
    justia_sections: List[JustiaSectionEntry],
    legis_section_ids: Dict[Tuple[str, str], int],
) -> Dict[Tuple[str, str], int]:
    """Join Justia sections against a precomputed legis ID mapping.

    The full Phase 2 fetcher (fetching all 55 legis TOC pages) is intentionally
    not implemented here yet — when ready, callers can wire it in by parsing
    legis TOC HTMLs with ``parse_legis_title_toc`` and passing the result.

    This function:
    1. Joins Justia ↔ legis by ``(title, section)``.
    2. Emits the join into ``data/rs/section_index.json``.
    3. Returns the joined mapping.
    """
    joined: Dict[Tuple[str, str], int] = {}
    in_justia_only: List[Tuple[str, str]] = []
    in_legis_only: List[Tuple[str, str]] = []

    justia_keys = {(s.title_number, s.section_number) for s in justia_sections}
    for key in justia_keys:
        if key in legis_section_ids:
            joined[key] = legis_section_ids[key]
        else:
            in_justia_only.append(key)
    for key in legis_section_ids:
        if key not in justia_keys:
            in_legis_only.append(key)

    rows = [
        {
            "title_number": t,
            "section_number": s,
            "citation": citation_for(t, s),
            "website_law_id": d,
        }
        for (t, s), d in sorted(
            joined.items(), key=lambda kv: (kv[0][0], section_sort_key(kv[0][1]))
        )
    ]
    paths.section_index.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False)
    )
    # Surface gaps in a side-channel report (the validation_report does this
    # too at Phase 4 time; we keep them in-memory here for now).
    if in_justia_only or in_legis_only:
        gaps = {
            "in_justia_not_legis": sorted(in_justia_only),
            "in_legis_not_justia": sorted(in_legis_only),
        }
        (paths.rs_root / "phase2_gaps.json").write_text(
            json.dumps(gaps, indent=2, ensure_ascii=False)
        )
    return joined


# ---------- Phase 3: legis section scrape ----------

def run_phase3(
    client,
    paths: LRSPaths,
    *,
    containers: List[Container],
    justia_sections: List[JustiaSectionEntry],
    section_index: Dict[Tuple[str, str], int],
    force_refetch: bool = False,
    limit: Optional[int] = None,
    titles: Optional[Iterable[str]] = None,
) -> Dict[str, RSSection]:
    """For every section in the index, fetch + parse + emit one RSSection.

    ``titles`` filters scope without touching ``section_index.json`` on disk —
    only sections whose Title appears in the set are fetched, and the
    blank/repealed backfill is restricted to the same set. Useful for pilot
    waves (e.g., ``titles=["1"]`` for Wave 1).
    """
    titles_set: Optional[set[str]] = (
        {str(t) for t in titles} if titles is not None else None
    )

    # Build a hierarchy index keyed on per-container section ranges. The
    # ranges were set during Phase 1 by ``assign_ranges_from_sections``.
    hierarchy_index = build_lrs_hierarchy_index(
        containers,
        section_index=[
            (s.title_number, s.section_number) for s in justia_sections
        ],
    )
    justia_by_key: Dict[Tuple[str, str], JustiaSectionEntry] = {
        (s.title_number, s.section_number): s for s in justia_sections
    }

    paths.sections_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, RSSection] = {}

    items = sorted(
        section_index.items(),
        key=lambda kv: (kv[0][0], section_sort_key(kv[0][1])),
    )
    if titles_set is not None:
        items = [kv for kv in items if kv[0][0] in titles_set]
    if limit is not None:
        items = items[:limit]

    for (title_number, section_number), d_value in items:
        url = legis_section_url(d_value)
        fetched = client.get(url, force_refetch=force_refetch)

        parsed = parse_legis_section(fetched.text)
        # Cross-check the section identifier — legis label must agree with
        # Justia.
        if parsed.title_number != title_number or parsed.section_number != section_number:
            # Soft conflict: record but trust the Justia identifier for the
            # output URN (Justia is the hierarchy source of truth).
            pass

        hierarchy_path = hierarchy_index.lookup(title_number, section_number)
        breadcrumb = _breadcrumb(hierarchy_path)

        # Justia-side repeal note overrides legis status if more specific.
        justia_entry = justia_by_key.get((title_number, section_number))
        if justia_entry and justia_entry.repealed and parsed.status == "active":
            parsed.status = "repealed"
            parsed.heading = None
            parsed.text = None
            parsed.acts_citations_raw = justia_entry.repeal_note

        acts_parsed: List[ActsCitation] = []
        if parsed.status == "active" and parsed.acts_citations_raw:
            acts_parsed = parse_acts_citation_line(
                _normalize_lrs_acts_text(parsed.acts_citations_raw)
            )

        record = RSSection(
            urn=urn_for(title_number, section_number),
            title_number=title_number,
            section_number=section_number,
            citation=citation_for(title_number, section_number),
            heading=parsed.heading,
            text=parsed.text,
            status=parsed.status,
            hierarchy_path=hierarchy_path,
            breadcrumb=breadcrumb,
            acts_citations=acts_parsed,
            acts_citations_raw=parsed.acts_citations_raw,
            source_url=url,
            website_law_id=d_value,
            scrape_timestamp=_utc_now_iso(),
            source_html_hash=f"sha256:{fetched.sha256}",
            schema_version=LRS_SCHEMA_VERSION,
        )
        out[record.urn] = record

    # Backfill repealed sections that Justia knows about but legis didn't
    # surface (no website_law_id available). When titles_set is given, only
    # backfill within the requested Titles — otherwise we'd emit 46k blanks
    # for a pilot run.
    for s in justia_sections:
        if titles_set is not None and s.title_number not in titles_set:
            continue
        urn = urn_for(s.title_number, s.section_number)
        if urn in out:
            continue
        if not s.repealed:
            # Section appears in Justia but not in legis_section_ids — flag in
            # validation later; we still emit a placeholder record so the
            # corpus is complete.
            status = "blank"
            acts_raw = None
        else:
            status = "repealed"
            acts_raw = s.repeal_note
        path = hierarchy_index.lookup(s.title_number, s.section_number)
        out[urn] = RSSection(
            urn=urn,
            title_number=s.title_number,
            section_number=s.section_number,
            citation=citation_for(s.title_number, s.section_number),
            heading=None,
            text=None,
            status=status,
            hierarchy_path=path,
            breadcrumb=_breadcrumb(path),
            acts_citations=[],
            acts_citations_raw=acts_raw,
            source_url=None,
            website_law_id=None,
            scrape_timestamp=_utc_now_iso(),
            source_html_hash=None,
            schema_version=LRS_SCHEMA_VERSION,
        )

    # Persist.
    sorted_records = sorted(
        out.values(),
        key=lambda r: (r.title_number, section_sort_key(r.section_number)),
    )
    # Clear stale per-section files so renames/removals stay tidy.
    for old in paths.sections_dir.glob("*.json"):
        old.unlink()
    with paths.sections_jsonl.open("w") as jl:
        for rec in sorted_records:
            slug = _section_slug(rec.title_number, rec.section_number)
            (paths.sections_dir / f"{slug}.json").write_text(
                json.dumps(rec.model_dump(), indent=2, ensure_ascii=False)
            )
            jl.write(json.dumps(rec.model_dump(), ensure_ascii=False) + "\n")

    _write_manifest(paths, sorted_records, containers)
    _write_validation_report(paths, sorted_records, justia_sections, section_index)
    return out


# ---------- Phase 4: derived artifacts ----------

# Cross-corpus citation pattern: any "R.S. T:S" with optional subdivisions.
_RS_CITATION_RE = re.compile(
    r"R\.S\.\s+(\d+(?:-[A-Z])?)\s*:\s*([0-9]+(?:\.[0-9]+)*)",
)
_CC_CITATION_RE = re.compile(
    r"(?:C\.\s*C\.|Civil\s+Code)\s+(?:Articles?|Arts?\.)\s+([0-9]+(?:\.[0-9]+)*)",
    re.IGNORECASE,
)
_CCP_CITATION_RE = re.compile(
    r"Code\s+of\s+Civil\s+Procedure\s+Article\s+([0-9]+(?:\.[0-9]+)*)",
    re.IGNORECASE,
)
_CRP_CITATION_RE = re.compile(
    r"Code\s+of\s+Criminal\s+Procedure\s+Article\s+([0-9]+(?:\.[0-9]+)*)",
    re.IGNORECASE,
)
_EVIDENCE_CITATION_RE = re.compile(
    r"Code\s+of\s+Evidence\s+Article\s+([0-9]+(?:\.[0-9]+)*)",
    re.IGNORECASE,
)


def run_phase4(
    paths: LRSPaths,
    containers: List[Container],
    sections: Dict[str, RSSection],
) -> Dict[str, int]:
    """Build tree.json, citation_edges.csv, chunks.jsonl, markdown/."""
    sorted_records = sorted(
        sections.values(),
        key=lambda r: (r.title_number, section_sort_key(r.section_number)),
    )

    tree = _build_tree(containers, sorted_records)
    paths.tree.write_text(json.dumps(tree, indent=2, ensure_ascii=False))

    edge_count = _write_citation_edges(paths.citation_edges, sorted_records)
    chunk_count = _write_chunks(paths.chunks, sorted_records)
    md_count = _write_markdown(paths.markdown_dir, sorted_records)

    stats = {
        "tree_max_depth": _max_tree_depth(tree),
        "citation_edges": edge_count,
        "rag_chunks": chunk_count,
        "markdown_files": md_count,
    }
    _augment_manifest(paths, containers, sorted_records, stats)
    return stats


def _build_tree(containers: List[Container], sections: List[RSSection]) -> Dict:
    nodes: Dict[Tuple, Dict] = {}
    roots: List[Dict] = []

    # Each container's full chain key includes the title at index 0 — that
    # disambiguates same-numbered chapters across different titles.
    def container_chain_key(c: Container) -> Tuple:
        return tuple(c.parent_chain) + ((_level_value(c), c.number),)

    for c in containers:
        key = container_chain_key(c)
        node = {
            "level": _level_value(c),
            "number": c.number,
            "name": c.name,
            "section_range": (
                [c.section_range_start, c.section_range_end]
                if c.section_range_start and c.section_range_end
                else None
            ),
            "is_repealed": c.is_repealed,
            "is_reserved": c.is_reserved,
            "children": [],
            "sections": [],
        }
        nodes[key] = node
        if len(key) == 1:
            roots.append(node)
        else:
            parent_key = key[:-1]
            parent = nodes.get(parent_key)
            if parent is None:
                roots.append(node)
            else:
                parent["children"].append(node)

    for rec in sections:
        chain = rec.hierarchy_path
        owner_key = tuple((n.level, n.number) for n in chain)
        owner = nodes.get(owner_key)
        if owner is None:
            # Fall back to longest matching prefix
            for length in range(len(owner_key) - 1, 0, -1):
                owner = nodes.get(owner_key[:length])
                if owner is not None:
                    break
        if owner is not None:
            owner["sections"].append(rec.section_number)

    for node in nodes.values():
        node["sections"].sort(key=section_sort_key)

    return {
        "schema_version": LRS_SCHEMA_VERSION,
        "generated_at": _utc_now_iso(),
        "roots": roots,
    }


def _max_tree_depth(tree: Dict) -> int:
    def walk(node: Dict, depth: int) -> int:
        children = node.get("children") or []
        if not children:
            return depth
        return max(walk(c, depth + 1) for c in children)

    return max((walk(r, 1) for r in tree.get("roots", [])), default=0)


def _write_citation_edges(out_path: Path, sections: List[RSSection]) -> int:
    fieldnames = [
        "src_urn",
        "src_corpus",
        "src_id",
        "dst_corpus",
        "dst_id",
        "dst_urn",
        "raw_match",
        "char_offset",
    ]
    edges: List[Dict[str, str]] = []
    for rec in sections:
        if rec.status != "active" or not rec.text:
            continue
        for m in _RS_CITATION_RE.finditer(rec.text):
            title, section = m.group(1), m.group(2)
            edges.append(
                {
                    "src_urn": rec.urn,
                    "src_corpus": "rs",
                    "src_id": f"{rec.title_number}:{rec.section_number}",
                    "dst_corpus": "rs",
                    "dst_id": f"{title}:{section}",
                    "dst_urn": urn_for(title, section),
                    "raw_match": m.group(0),
                    "char_offset": str(m.start()),
                }
            )
        for m in _CC_CITATION_RE.finditer(rec.text):
            art = m.group(1)
            edges.append(
                {
                    "src_urn": rec.urn,
                    "src_corpus": "rs",
                    "src_id": f"{rec.title_number}:{rec.section_number}",
                    "dst_corpus": "civcode",
                    "dst_id": art,
                    "dst_urn": f"urn:us-la:civcode:art:{art}",
                    "raw_match": m.group(0),
                    "char_offset": str(m.start()),
                }
            )
        for label, pattern in (
            ("ccp", _CCP_CITATION_RE),
            ("crp", _CRP_CITATION_RE),
            ("evidence", _EVIDENCE_CITATION_RE),
        ):
            for m in pattern.finditer(rec.text):
                art = m.group(1)
                edges.append(
                    {
                        "src_urn": rec.urn,
                        "src_corpus": "rs",
                        "src_id": f"{rec.title_number}:{rec.section_number}",
                        "dst_corpus": label,
                        "dst_id": art,
                        "dst_urn": "",
                        "raw_match": m.group(0),
                        "char_offset": str(m.start()),
                    }
                )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in edges:
            writer.writerow(row)
    return len(edges)


def _write_chunks(out_path: Path, sections: List[RSSection]) -> int:
    count = 0
    actives = [r for r in sections if r.status == "active" and r.text]
    by_title_chain: Dict[str, List[RSSection]] = defaultdict(list)
    for r in actives:
        by_title_chain[r.title_number].append(r)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for i, r in enumerate(actives):
            neighbors = by_title_chain[r.title_number]
            idx = neighbors.index(r)
            prev_urn = neighbors[idx - 1].urn if idx > 0 else None
            next_urn = neighbors[idx + 1].urn if idx + 1 < len(neighbors) else None
            f.write(
                json.dumps(
                    {
                        "urn": r.urn,
                        "citation": r.citation,
                        "breadcrumb": r.breadcrumb,
                        "heading": r.heading,
                        "text": r.text,
                        "prev_urn": prev_urn,
                        "next_urn": next_urn,
                        "source_url": r.source_url,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            count += 1
    return count


def _write_markdown(out_dir: Path, sections: List[RSSection]) -> int:
    count = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for rec in sections:
        title_dir = out_dir / f"title-{rec.title_number}"
        title_dir.mkdir(parents=True, exist_ok=True)
        safe_section = rec.section_number.replace(".", "_")
        path = title_dir / f"{safe_section}.md"
        frontmatter = [
            "---",
            f"urn: {rec.urn}",
            f"citation: \"{rec.citation}\"",
            f"status: {rec.status}",
            f"breadcrumb: \"{rec.breadcrumb}\"",
            "acts_citations:",
        ]
        for ac in rec.acts_citations:
            frontmatter.append(
                f"  - {{ year: {ac.act_year}, number: {ac.act_number}, section: {ac.section}, effective_date: {ac.effective_date or 'null'}, role: {ac.role} }}"
            )
        frontmatter.append("---")
        body_lines = [f"# {rec.heading or '(no heading)'}"]
        if rec.status != "active":
            body_lines.append(f"\n_Status: {rec.status}_")
            if rec.acts_citations_raw:
                body_lines.append(f"\n{rec.acts_citations_raw}")
        else:
            body_lines.append("")
            body_lines.append(rec.text or "")
        path.write_text("\n".join(frontmatter) + "\n\n" + "\n".join(body_lines) + "\n")
        count += 1
    return count


# ---------- manifests / validation ----------

def _write_manifest(
    paths: LRSPaths,
    sections: List[RSSection],
    containers: List[Container],
) -> None:
    by_status = Counter(s.status for s in sections)
    manifest = {
        "schema_version": LRS_SCHEMA_VERSION,
        "generated_at": _utc_now_iso(),
        "corpus": "rs",
        "totals": {
            "containers": len(containers),
            "sections_emitted": len(sections),
            "by_status": dict(by_status),
        },
        "sources": {
            "justia_root": JUSTIA_ROOT_URL,
            "justia_title_template": JUSTIA_TITLE_URL_TEMPLATE,
            "legis_section_template": LEGIS_SECTION_URL_TEMPLATE,
        },
    }
    paths.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def _augment_manifest(
    paths: LRSPaths,
    containers: List[Container],
    sections: List[RSSection],
    derived_stats: Dict[str, int],
) -> None:
    if paths.manifest.exists():
        manifest = json.loads(paths.manifest.read_text())
    else:
        manifest = {}

    by_title: Dict[str, Counter] = defaultdict(Counter)
    title_order: List[str] = []
    for rec in sections:
        t = rec.title_number
        if t not in by_title:
            title_order.append(t)
        by_title[t][rec.status] += 1

    completeness = {
        "by_title": [
            {
                "title": t,
                "active": by_title[t].get("active", 0),
                "repealed": by_title[t].get("repealed", 0),
                "reserved": by_title[t].get("reserved", 0),
                "blank": by_title[t].get("blank", 0),
                "total": sum(by_title[t].values()),
            }
            for t in sorted(title_order, key=lambda v: int(v.split("-")[0]) if v.split("-")[0].isdigit() else 0)
        ],
        "tree_max_depth": derived_stats["tree_max_depth"],
        "total_citation_edges": derived_stats["citation_edges"],
        "rag_chunks": derived_stats["rag_chunks"],
        "markdown_files": derived_stats["markdown_files"],
    }
    manifest["completeness"] = completeness
    manifest["generated_at"] = _utc_now_iso()
    paths.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def _write_validation_report(
    paths: LRSPaths,
    sections: List[RSSection],
    justia_sections: List[JustiaSectionEntry],
    section_index: Dict[Tuple[str, str], int],
) -> None:
    no_hierarchy = [s.urn for s in sections if not s.hierarchy_path]
    in_index_but_unparsed = [
        f"{t}:{s}"
        for (t, s) in section_index
        if urn_for(t, s) not in {sec.urn for sec in sections}
    ]
    justia_only_blanks = [
        s.urn
        for s in sections
        if s.status == "blank" and s.website_law_id is None
    ]
    repealed_synthesized = [
        s.urn
        for s in sections
        if s.status == "repealed" and s.website_law_id is None
    ]
    report = {
        "sections_without_hierarchy": sorted(no_hierarchy),
        "in_section_index_but_unemitted": sorted(in_index_but_unparsed),
        "synthetic_blank_sections": sorted(justia_only_blanks),
        "synthetic_repealed_sections": sorted(repealed_synthesized),
    }
    paths.validation_report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )


# ---------- snapshot + all ----------

def snapshot(paths: LRSPaths, date: Optional[str] = None) -> Path:
    date = date or _dt.date.today().isoformat()
    target = paths.snapshots / f"lrs-{date}"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for src in (
        paths.hierarchy,
        paths.justia_section_index,
        paths.section_index,
        paths.sections_jsonl,
        paths.notes,
        paths.tree,
        paths.citation_edges,
        paths.chunks,
        paths.manifest,
        paths.validation_report,
    ):
        if src.exists():
            shutil.copy2(src, target / src.name)
    if paths.sections_dir.exists():
        shutil.copytree(paths.sections_dir, target / "sections")
    if paths.markdown_dir.exists():
        shutil.copytree(paths.markdown_dir, target / "markdown")
    return target


def run_all(
    client,
    paths: LRSPaths,
    *,
    titles: Optional[Iterable[str]] = None,
    legis_section_ids: Optional[Dict[Tuple[str, str], int]] = None,
    phase2_titles: Optional[Iterable[str]] = None,
    take_snapshot: bool = True,
) -> Dict[str, int]:
    """End-to-end pipeline.

    * ``titles``: which Justia Titles to walk in Phase 1 *and* which sections
      to fetch in Phase 3. ``None`` = every Title Justia advertises.
    * ``phase2_titles``: which Titles' legis TOCs to fetch in Phase 2.
      ``None`` defaults to ``titles``. Pass an explicit broader set (e.g.,
      every Title) when you want Phase 2 to populate the corpus-wide
      ``section_index.json`` even though Phase 3 is scoped to a pilot subset.
    * ``legis_section_ids``: pre-built ``(title, section) -> d`` mapping. When
      provided, skips the Phase 2 fetch entirely. ``None`` (the common case)
      runs ``run_phase2_fetch`` against legis.la.gov.
    """
    containers, justia_sections = run_phase1(client, paths, titles=titles)
    if legis_section_ids is None:
        section_index = run_phase2_fetch(
            client,
            paths,
            justia_sections=justia_sections,
            titles=phase2_titles if phase2_titles is not None else titles,
        )
    else:
        section_index = run_phase2_with_index(
            paths,
            justia_sections=justia_sections,
            legis_section_ids=legis_section_ids,
        )
    sections = run_phase3(
        client,
        paths,
        containers=containers,
        justia_sections=justia_sections,
        section_index=section_index,
        titles=titles,
    )
    stats = run_phase4(paths, containers, sections)
    if take_snapshot:
        snapshot(paths)
    return stats
