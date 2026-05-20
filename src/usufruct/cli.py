"""Usufruct command-line entry point.

    usufruct phase1                # parse LSU TOC into data/hierarchy.json
    usufruct phase2                # parse legis.la.gov TOC into data/article_index.json
    usufruct phase3 [--limit N]    # scrape every article and emit data/articles/...
    usufruct phase4                # build derived artifacts (tree, citation edges, chunks, markdown)
    usufruct all                   # run all four phases in order
    usufruct snapshot              # archive data/ into snapshots/YYYY-MM-DD/
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
    return 0


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
