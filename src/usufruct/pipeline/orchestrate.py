"""Run the Usufruct pipeline end-to-end."""
from __future__ import annotations

import datetime as _dt
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .. import SCHEMA_VERSION
from ..fetch import CachedClient
from ..fetch.legis_article import article_url, fetch_legis_article
from ..fetch.legis_toc import fetch_legis_toc
from ..fetch.lsu_toc import fetch_lsu_toc
from ..model import Article, ActsCitation, Container, HierarchyNode, article_number_sort_key
from ..parse import (
    parse_acts_citation_line,
    parse_legis_article,
    parse_legis_toc,
    parse_lsu_toc,
)
from .chunks import write_chunks
from .citations import collect_edges, write_csv as write_edges_csv
from .hierarchy import HierarchyIndex, build_hierarchy_index
from .markdown import write_markdown
from .tree import build_tree, max_depth, write_tree


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _urn(article_number: str) -> str:
    return f"urn:us-la:civcode:art:{article_number}"


def _breadcrumb(path: List[HierarchyNode]) -> str:
    parts = []
    for node in path:
        label_map = {
            "preliminary_title": "Preliminary Title",
            "book": f"Book {node.number}",
            "title": f"Title {node.number}",
            "chapter": f"Chapter {node.number}",
            "section": f"Section {node.number}",
            "subsection": f"Subsection {node.number}",
            "paragraph": f"§{node.number}",
        }
        parts.append(label_map.get(node.level, f"{node.level} {node.number}"))
    return " › ".join(parts)


@dataclass
class PipelinePaths:
    root: Path
    raw: Path = field(init=False)
    hierarchy: Path = field(init=False)
    article_index: Path = field(init=False)
    articles_dir: Path = field(init=False)
    articles_jsonl: Path = field(init=False)
    manifest: Path = field(init=False)
    validation_report: Path = field(init=False)
    snapshots: Path = field(init=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw = self.root / "raw"
        self.hierarchy = self.root / "hierarchy.json"
        self.article_index = self.root / "article_index.json"
        self.articles_dir = self.root / "articles"
        self.articles_jsonl = self.root / "articles.jsonl"
        self.manifest = self.root / "manifest.json"
        self.validation_report = self.root / "validation_report.json"
        # Phase 4 derived artifacts
        self.tree = self.root / "tree.json"
        self.citation_edges = self.root / "citation_edges.csv"
        self.chunks = self.root / "chunks.jsonl"
        self.markdown_dir = self.root / "markdown"
        # snapshots live next to the data dir, not inside it
        self.snapshots = self.root.parent / "snapshots"


def run_phase1(client: CachedClient, paths: PipelinePaths) -> List[Container]:
    result = fetch_lsu_toc(client)
    containers = parse_lsu_toc(result.text)
    paths.hierarchy.write_text(
        json.dumps([c.model_dump() for c in containers], indent=2, ensure_ascii=False)
    )
    return containers


def run_phase2(client: CachedClient, paths: PipelinePaths) -> Dict[str, int]:
    result = fetch_legis_toc(client)
    mapping = parse_legis_toc(result.text)
    paths.article_index.write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False, sort_keys=False)
    )
    return mapping


def _article_from_parsed(
    parsed,
    website_law_id: int,
    source_url: str,
    sha: str,
    hierarchy: HierarchyIndex,
) -> Article:
    path = hierarchy.lookup(parsed.article_number)
    parsed_acts = (
        parse_acts_citation_line(parsed.acts_citations_raw)
        if parsed.acts_citations_raw and parsed.status == "active"
        else []
    )
    return Article(
        urn=_urn(parsed.article_number),
        article_number=parsed.article_number,
        heading=parsed.heading,
        text=parsed.text,
        status=parsed.status,
        hierarchy_path=path,
        breadcrumb=_breadcrumb(path),
        acts_citations=parsed_acts,
        acts_citations_raw=parsed.acts_citations_raw,
        source_url=source_url,
        website_law_id=website_law_id,
        scrape_timestamp=_utc_now_iso(),
        source_html_hash=f"sha256:{sha}",
        schema_version=SCHEMA_VERSION,
    )


def _synthetic_article(
    article_number: str,
    status: str,
    hierarchy: HierarchyIndex,
    acts_raw: Optional[str] = None,
    note: Optional[str] = None,
) -> Article:
    path = hierarchy.lookup(article_number) or hierarchy.nearest(article_number)
    return Article(
        urn=_urn(article_number),
        article_number=article_number,
        heading=None,
        text=None,
        status=status,  # type: ignore[arg-type]
        hierarchy_path=path,
        breadcrumb=_breadcrumb(path),
        acts_citations=[],
        acts_citations_raw=acts_raw,
        source_url=None,
        website_law_id=None,
        scrape_timestamp=_utc_now_iso(),
        source_html_hash=None,
        schema_version=SCHEMA_VERSION,
    )


def run_phase3(
    client: CachedClient,
    paths: PipelinePaths,
    containers: List[Container],
    article_index: Dict[str, int],
    force_refetch: bool = False,
    limit: Optional[int] = None,
) -> Dict[str, Article]:
    """Fetch every article in the index, parse, assign hierarchy, write outputs.

    Returns the full ``{article_number: Article}`` map (including synthesised
    repealed-range fillers and blank records derived from LSU ranges).
    """
    hierarchy = build_hierarchy_index(containers)
    articles: Dict[str, Article] = {}
    repeal_ranges: List[tuple] = []  # (start, end, acts_raw)
    paths.articles_dir.mkdir(parents=True, exist_ok=True)

    items = sorted(article_index.items(), key=lambda kv: article_number_sort_key(kv[0]))
    if limit is not None:
        items = items[:limit]

    for article_number, website_law_id in items:
        url = article_url(website_law_id)
        fetched = client.get(url, force_refetch=force_refetch)
        parsed = parse_legis_article(fetched.text)
        # The label-derived article number is sometimes more reliable than the
        # Art-line text (e.g., when the Art line is the repeal notice), but
        # both should agree for normal articles.
        if not parsed.article_number:
            parsed.article_number = article_number
        article = _article_from_parsed(
            parsed,
            website_law_id=website_law_id,
            source_url=url,
            sha=fetched.sha256,
            hierarchy=hierarchy,
        )
        articles[article.article_number] = article

        # Track repealed ranges for back-fill
        if parsed.status == "repealed" and parsed.repealed_range_end:
            repeal_ranges.append(
                (
                    parsed.article_number,
                    parsed.repealed_range_end,
                    parsed.acts_citations_raw,
                )
            )

    # Backfill missing numbers inside any repealed range as status=repealed.
    for start, end, acts_raw in repeal_ranges:
        start_key = article_number_sort_key(start)
        end_key = article_number_sort_key(end)
        for n in range(start_key[0] + 1, end_key[0] + 1):
            num = str(n)
            if num in articles:
                continue
            articles[num] = _synthetic_article(
                num, status="repealed", hierarchy=hierarchy, acts_raw=acts_raw
            )

    # Backfill any LSU-covered numbers still missing as status=blank.
    expected = hierarchy.all_article_numbers()
    for num in expected:
        if num in articles:
            continue
        articles[num] = _synthetic_article(num, status="blank", hierarchy=hierarchy)

    # Write per-article JSON and JSONL
    sorted_numbers = sorted(articles.keys(), key=article_number_sort_key)
    paths.articles_dir.mkdir(parents=True, exist_ok=True)
    # Clear stale per-article files
    for old in paths.articles_dir.glob("*.json"):
        old.unlink()
    with paths.articles_jsonl.open("w") as jl:
        for num in sorted_numbers:
            art = articles[num]
            (paths.articles_dir / f"{num}.json").write_text(
                json.dumps(art.model_dump(), indent=2, ensure_ascii=False)
            )
            jl.write(json.dumps(art.model_dump(), ensure_ascii=False) + "\n")

    _write_manifest(paths, articles, article_index, containers)
    _write_validation_report(paths, articles, article_index, hierarchy)
    return articles


def run_phase4(
    paths: PipelinePaths,
    containers: List[Container],
    articles: Dict[str, Article],
) -> Dict[str, int]:
    """Build derived artifacts: tree.json, citation_edges.csv, chunks.jsonl, markdown/.

    Returns a stats dict consumed by the augmented manifest writer.
    """
    article_list = [articles[k] for k in sorted(articles.keys(), key=article_number_sort_key)]

    tree = build_tree(containers, article_list)
    paths.tree.write_text(json.dumps(tree, indent=2, ensure_ascii=False))

    edges = collect_edges(article_list)
    write_edges_csv(paths.citation_edges, edges)

    chunk_count = write_chunks(paths.chunks, article_list)
    md_count = write_markdown(paths.markdown_dir, article_list)

    stats = {
        "tree_max_depth": max_depth(tree),
        "citation_edges": len(edges),
        "rag_chunks": chunk_count,
        "markdown_files": md_count,
    }
    _augment_manifest(paths, containers, articles, stats)
    return stats


def _augment_manifest(
    paths: PipelinePaths,
    containers: List[Container],
    articles: Dict[str, Article],
    derived_stats: Dict[str, int],
) -> None:
    """Replace manifest.json with the Phase 3 totals plus Phase 4 derived stats."""
    from collections import Counter, defaultdict

    if paths.manifest.exists():
        manifest = json.loads(paths.manifest.read_text())
    else:
        manifest = {}

    # Per-Book breakdown — bucket by the first hierarchy node (book or preliminary_title)
    buckets: Dict[str, Counter] = defaultdict(Counter)
    bucket_order: List[str] = []
    for art in articles.values():
        root = art.hierarchy_path[0] if art.hierarchy_path else None
        if root is None:
            label = "(out of range)"
        elif root.level == "preliminary_title":
            label = "Preliminary Title"
        else:
            label = f"{root.level.title()} {root.number}"
        if label not in buckets:
            bucket_order.append(label)
        buckets[label][art.status] += 1

    by_book = []
    for label in bucket_order:
        counts = buckets[label]
        by_book.append(
            {
                "bucket": label,
                "active": counts.get("active", 0),
                "repealed": counts.get("repealed", 0),
                "reserved": counts.get("reserved", 0),
                "blank": counts.get("blank", 0),
                "total": sum(counts.values()),
            }
        )

    completeness = {
        "by_book": by_book,
        "tree_max_depth": derived_stats["tree_max_depth"],
        "total_citation_edges": derived_stats["citation_edges"],
        "rag_chunks": derived_stats["rag_chunks"],
        "markdown_files": derived_stats["markdown_files"],
    }
    manifest["completeness"] = completeness
    manifest["generated_at"] = _utc_now_iso()
    paths.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def _write_manifest(
    paths: PipelinePaths,
    articles: Dict[str, Article],
    article_index: Dict[str, int],
    containers: List[Container],
) -> None:
    from collections import Counter

    by_status = Counter(a.status for a in articles.values())
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now_iso(),
        "totals": {
            "containers": len(containers),
            "articles_in_index": len(article_index),
            "articles_emitted": len(articles),
            "by_status": dict(by_status),
        },
        "sources": {
            "hierarchy": "https://lcco.law.lsu.edu/?uid=1&ver=en",
            "legis_toc": "https://legis.la.gov/legis/Laws_Toc.aspx?folder=67&level=Parent",
            "legis_article_template": "https://legis.la.gov/legis/Law.aspx?d={d}",
        },
    }
    paths.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def _write_validation_report(
    paths: PipelinePaths,
    articles: Dict[str, Article],
    article_index: Dict[str, int],
    hierarchy: HierarchyIndex,
) -> None:
    out_of_range: List[str] = []
    for num, art in articles.items():
        if not art.hierarchy_path:
            out_of_range.append(num)
    in_index_but_unparsed = [num for num in article_index if num not in articles]
    synthetic_blank = [
        n for n, a in articles.items() if a.status == "blank" and a.website_law_id is None
    ]
    synthetic_repeal = [
        n for n, a in articles.items() if a.status == "repealed" and a.website_law_id is None
    ]
    report = {
        "out_of_lsu_range": sorted(out_of_range, key=article_number_sort_key),
        "in_legis_index_but_not_emitted": sorted(
            in_index_but_unparsed, key=article_number_sort_key
        ),
        "synthetic_blank_articles": sorted(synthetic_blank, key=article_number_sort_key),
        "synthetic_repealed_articles": sorted(synthetic_repeal, key=article_number_sort_key),
    }
    paths.validation_report.write_text(json.dumps(report, indent=2, ensure_ascii=False))


def snapshot(paths: PipelinePaths, date: Optional[str] = None) -> Path:
    date = date or _dt.date.today().isoformat()
    target = paths.snapshots / date
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for fname in (
        paths.hierarchy,
        paths.article_index,
        paths.articles_jsonl,
        paths.manifest,
        paths.validation_report,
        paths.tree,
        paths.citation_edges,
        paths.chunks,
    ):
        if fname.exists():
            shutil.copy2(fname, target / fname.name)
    if paths.articles_dir.exists():
        shutil.copytree(paths.articles_dir, target / "articles")
    if paths.markdown_dir.exists():
        shutil.copytree(paths.markdown_dir, target / "markdown")
    return target


def run_all(client: CachedClient, paths: PipelinePaths, take_snapshot: bool = True) -> None:
    containers = run_phase1(client, paths)
    article_index = run_phase2(client, paths)
    articles = run_phase3(client, paths, containers, article_index)
    run_phase4(paths, containers, articles)
    if take_snapshot:
        snapshot(paths)
