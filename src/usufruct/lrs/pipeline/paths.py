"""LRS-specific output paths, parallel to ``pipeline.orchestrate.PipelinePaths``.

Outputs live under ``data/rs/`` so the existing CC pipeline outputs at
``data/`` root are untouched. The raw HTML cache at ``data/raw/`` is shared
across corpora (it's keyed by SHA of the response body).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LRSPaths:
    """File-system layout for the LRS pipeline.

    All outputs are written under ``<root>/rs/``. The shared raw HTML cache
    stays at ``<root>/raw/``.
    """

    root: Path
    raw: Path = field(init=False)
    rs_root: Path = field(init=False)
    hierarchy: Path = field(init=False)
    justia_section_index: Path = field(init=False)
    section_index: Path = field(init=False)
    sections_dir: Path = field(init=False)
    sections_jsonl: Path = field(init=False)
    notes: Path = field(init=False)
    manifest: Path = field(init=False)
    validation_report: Path = field(init=False)
    tree: Path = field(init=False)
    citation_edges: Path = field(init=False)
    chunks: Path = field(init=False)
    markdown_dir: Path = field(init=False)
    snapshots: Path = field(init=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw = self.root / "raw"
        self.rs_root = self.root / "rs"
        self.rs_root.mkdir(parents=True, exist_ok=True)
        self.hierarchy = self.rs_root / "hierarchy.json"
        self.justia_section_index = self.rs_root / "justia_section_index.json"
        self.section_index = self.rs_root / "section_index.json"
        self.sections_dir = self.rs_root / "sections"
        self.sections_jsonl = self.rs_root / "sections.jsonl"
        self.notes = self.rs_root / "notes.jsonl"
        self.manifest = self.rs_root / "manifest.json"
        self.validation_report = self.rs_root / "validation_report.json"
        self.tree = self.rs_root / "tree.json"
        self.citation_edges = self.rs_root / "citation_edges.csv"
        self.chunks = self.rs_root / "chunks.jsonl"
        self.markdown_dir = self.rs_root / "markdown"
        # Snapshots live next to data/, peer with CC's snapshots/.
        self.snapshots = self.root.parent / "snapshots"
