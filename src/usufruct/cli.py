"""Usufruct command-line entry point.

    usufruct phase1                # parse LSU TOC into data/hierarchy.json
    usufruct phase2                # parse legis.la.gov TOC into data/article_index.json
    usufruct phase3 [--limit N]    # scrape every article and emit data/articles/...
    usufruct phase4                # build derived artifacts (tree, citation edges, chunks, markdown)
    usufruct all                   # run all four phases in order
    usufruct snapshot              # archive data/ into snapshots/YYYY-MM-DD/

    usufruct rs phase1 [--titles N,M,...]   # Justia hierarchy → data/rs/
    usufruct rs phase3 [--limit N]          # legis section scrape → data/rs/sections/
    usufruct rs phase4                      # derived artifacts → data/rs/
    usufruct rs all [--titles N,M,...]      # phase1 → 3 → 4 → snapshot
    usufruct rs snapshot                    # archive data/rs/ → snapshots/lrs-YYYY-MM-DD/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .fetch import CachedClient
from .pipeline.orchestrate import (
    PipelinePaths,
    run_all,
    run_phase1,
    run_phase2,
    run_phase3,
    run_phase4,
    snapshot,
)
from .parse import parse_lsu_toc, parse_legis_toc
from .model import Article, Container
from .lrs.pipeline import LRSPaths as _LRSPaths
from .lrs.pipeline import run_all as _lrs_run_all
from .lrs.pipeline import run_phase1 as _lrs_run_phase1
from .lrs.pipeline import run_phase2_fetch as _lrs_run_phase2_fetch
from .lrs.pipeline import run_phase3 as _lrs_run_phase3
from .lrs.pipeline import run_phase4 as _lrs_run_phase4
from .lrs.pipeline import snapshot as _lrs_snapshot
from .lrs.pipeline.orchestrate import run_phase2_with_index as _lrs_run_phase2_with_index
from .lrs.model import Container as _RSContainer
from .lrs.model import RSSection
from .lrs.parse import JustiaSectionEntry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="usufruct", description=__doc__)
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Where pipeline outputs and the raw HTML cache live (default: data)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help="Requests per second to legis.la.gov (default: 1.0)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("phase1", help="Parse the LSU TOC into hierarchy.json")
    sub.add_parser("phase2", help="Parse the legis.la.gov TOC into article_index.json")

    p3 = sub.add_parser("phase3", help="Scrape every article into data/articles/")
    p3.add_argument("--limit", type=int, default=None, help="Stop after N articles (for smoke tests)")
    p3.add_argument("--force-refetch", action="store_true", help="Bypass cache for article HTML")

    sub.add_parser(
        "phase4",
        help="Derived artifacts: tree.json, citation_edges.csv, chunks.jsonl, markdown/",
    )
    sub.add_parser("all", help="phase1 + phase2 + phase3 + phase4 + snapshot")
    sub.add_parser("snapshot", help="Copy current data/ outputs into snapshots/YYYY-MM-DD")

    rs = sub.add_parser("rs", help="Louisiana Revised Statutes pipeline (parallel to CC)")
    rs_sub = rs.add_subparsers(dest="rs_command", required=True)
    p1 = rs_sub.add_parser("phase1", help="Justia hierarchy + section index")
    p1.add_argument("--titles", default=None, help="Comma-separated Title numbers (e.g. 1,14)")
    p2 = rs_sub.add_parser(
        "phase2", help="Fetch legis root + per-Title TOCs → section_index.json"
    )
    p2.add_argument(
        "--titles",
        default=None,
        help="Comma-separated Title numbers; default walks all 54",
    )
    p2.add_argument("--force-refetch", action="store_true")
    p3 = rs_sub.add_parser("phase3", help="Fetch + parse each legis section")
    p3.add_argument(
        "--titles",
        default=None,
        help="Comma-separated Title numbers; scopes which sections to fetch",
    )
    p3.add_argument("--limit", type=int, default=None, help="Stop after N sections")
    p3.add_argument("--force-refetch", action="store_true")
    rs_sub.add_parser("phase4", help="Tree, citation edges, chunks, markdown")
    pall = rs_sub.add_parser("all", help="phase1 + phase2 + phase3 + phase4 + snapshot")
    pall.add_argument(
        "--titles",
        default=None,
        help="Comma-separated Title numbers; scopes Phase 1 + Phase 3",
    )
    pall.add_argument(
        "--phase2-titles",
        default=None,
        help="Comma-separated Title numbers for Phase 2 TOC walk (default: same as --titles)",
    )
    rs_sub.add_parser("snapshot", help="Archive data/rs/ → snapshots/lrs-YYYY-MM-DD/")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = PipelinePaths(root=Path(args.data_dir))
    client = CachedClient(cache_dir=paths.raw, rate_limit_per_host=args.rate_limit)

    if args.command == "phase1":
        containers = run_phase1(client, paths)
        print(f"Wrote {len(containers)} containers -> {paths.hierarchy}", file=sys.stderr)
    elif args.command == "phase2":
        mapping = run_phase2(client, paths)
        print(f"Wrote {len(mapping)} article IDs -> {paths.article_index}", file=sys.stderr)
    elif args.command == "phase3":
        containers = _load_containers(paths)
        article_index = _load_article_index(paths)
        articles = run_phase3(
            client,
            paths,
            containers=containers,
            article_index=article_index,
            force_refetch=args.force_refetch,
            limit=args.limit,
        )
        print(
            f"Emitted {len(articles)} article records -> {paths.articles_dir} / {paths.articles_jsonl}",
            file=sys.stderr,
        )
    elif args.command == "phase4":
        containers = _load_containers(paths)
        articles = _load_articles(paths)
        stats = run_phase4(paths, containers, articles)
        print(
            "Phase 4 complete: "
            f"tree(max_depth={stats['tree_max_depth']}), "
            f"edges={stats['citation_edges']}, "
            f"chunks={stats['rag_chunks']}, "
            f"markdown={stats['markdown_files']}",
            file=sys.stderr,
        )
    elif args.command == "all":
        run_all(client, paths, take_snapshot=True)
        print(f"Pipeline complete; snapshot in {paths.snapshots}", file=sys.stderr)
    elif args.command == "snapshot":
        target = snapshot(paths)
        print(f"Snapshot written to {target}", file=sys.stderr)
    elif args.command == "rs":
        return _run_rs(args, client)
    return 0


def _parse_titles_arg(spec):
    if spec is None:
        return None
    return [s.strip() for s in spec.split(",") if s.strip()]


def _run_rs(args, client) -> int:
    paths = _LRSPaths(root=Path(args.data_dir))
    if args.rs_command == "phase1":
        titles = _parse_titles_arg(args.titles)
        containers, sections = _lrs_run_phase1(client, paths, titles=titles)
        print(
            f"Phase 1: {len(containers)} containers, {len(sections)} sections "
            f"-> {paths.hierarchy}",
            file=sys.stderr,
        )
    elif args.rs_command == "phase2":
        titles = _parse_titles_arg(args.titles)
        _containers, justia_sections = _load_lrs_phase1(paths)
        section_index = _lrs_run_phase2_fetch(
            client,
            paths,
            justia_sections=justia_sections,
            titles=titles,
            force_refetch=args.force_refetch,
            progress=True,
        )
        print(
            f"Phase 2: emitted {len(section_index)} section IDs -> {paths.section_index}",
            file=sys.stderr,
        )
    elif args.rs_command == "phase3":
        containers, justia_sections = _load_lrs_phase1(paths)
        section_index = _load_lrs_section_index(paths)
        titles = _parse_titles_arg(args.titles)
        records = _lrs_run_phase3(
            client,
            paths,
            containers=containers,
            justia_sections=justia_sections,
            section_index=section_index,
            force_refetch=args.force_refetch,
            limit=args.limit,
            titles=titles,
        )
        print(
            f"Phase 3: emitted {len(records)} sections -> {paths.sections_jsonl}",
            file=sys.stderr,
        )
    elif args.rs_command == "phase4":
        containers, _ = _load_lrs_phase1(paths)
        sections = _load_lrs_sections(paths)
        stats = _lrs_run_phase4(paths, containers, sections)
        print(
            "Phase 4 complete: "
            f"tree(max_depth={stats['tree_max_depth']}), "
            f"edges={stats['citation_edges']}, "
            f"chunks={stats['rag_chunks']}, "
            f"markdown={stats['markdown_files']}",
            file=sys.stderr,
        )
    elif args.rs_command == "all":
        titles = _parse_titles_arg(args.titles)
        phase2_titles = _parse_titles_arg(args.phase2_titles)
        _lrs_run_all(
            client,
            paths,
            titles=titles,
            phase2_titles=phase2_titles,
        )
        print(
            f"LRS pipeline complete; snapshot in {paths.snapshots}",
            file=sys.stderr,
        )
    elif args.rs_command == "snapshot":
        target = _lrs_snapshot(paths)
        print(f"LRS snapshot written to {target}", file=sys.stderr)
    return 0


def _load_lrs_phase1(paths):
    if not paths.hierarchy.exists() or not paths.justia_section_index.exists():
        raise SystemExit(
            "Run `usufruct rs phase1` first: "
            f"{paths.hierarchy} / {paths.justia_section_index} missing"
        )
    containers = [
        _RSContainer.model_validate(d) for d in json.loads(paths.hierarchy.read_text())
    ]
    raw_sections = json.loads(paths.justia_section_index.read_text())
    sections = [
        JustiaSectionEntry(
            title_number=r["title_number"],
            section_number=r["section_number"],
            heading=r.get("heading"),
            repealed=r.get("repealed", False),
            repeal_note=r.get("repeal_note"),
            container_chain=[tuple(c) for c in r.get("container_chain", [])],
        )
        for r in raw_sections
    ]
    return containers, sections


def _load_lrs_section_index(paths):
    if not paths.section_index.exists():
        raise SystemExit(
            f"Run `usufruct rs phase2` (or populate {paths.section_index}) first"
        )
    rows = json.loads(paths.section_index.read_text())
    return {(r["title_number"], r["section_number"]): r["website_law_id"] for r in rows}


def _load_lrs_sections(paths):
    if not paths.sections_jsonl.exists():
        raise SystemExit(f"Run `usufruct rs phase3` first: {paths.sections_jsonl} missing")
    out = {}
    with paths.sections_jsonl.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = RSSection.model_validate_json(line)
            out[rec.urn] = rec
    return out


def _load_containers(paths: PipelinePaths):
    if not paths.hierarchy.exists():
        raise SystemExit(f"Run `usufruct phase1` first: {paths.hierarchy} missing")
    data = json.loads(paths.hierarchy.read_text())
    return [Container.model_validate(d) for d in data]


def _load_article_index(paths: PipelinePaths):
    if not paths.article_index.exists():
        raise SystemExit(f"Run `usufruct phase2` first: {paths.article_index} missing")
    return json.loads(paths.article_index.read_text())


def _load_articles(paths: PipelinePaths):
    if not paths.articles_jsonl.exists():
        raise SystemExit(f"Run `usufruct phase3` first: {paths.articles_jsonl} missing")
    out = {}
    with paths.articles_jsonl.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            art = Article.model_validate_json(line)
            out[art.article_number] = art
    return out


if __name__ == "__main__":
    sys.exit(main())
