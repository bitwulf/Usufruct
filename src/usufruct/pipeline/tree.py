"""Hierarchical tree JSON — Phase 4 derived artifact.

Reconstructs the nested LSU container hierarchy with each leaf container
carrying the list of article numbers that resolved into it. Containers are
matched by their full root-to-leaf chain (level, number, name) so that
sibling titles with reused Roman numerals across different books remain
distinct.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from .. import SCHEMA_VERSION
from ..model import Article, Container, article_number_sort_key

ContainerKey = Tuple[Tuple[str, str, str], ...]


def _container_key(c: Container) -> ContainerKey:
    return tuple((node.level, node.number, node.name) for node in c.chain())


def _article_owner_key(article: Article) -> ContainerKey:
    return tuple((n.level, n.number, n.name) for n in article.hierarchy_path)


def build_tree(
    containers: Sequence[Container], articles: Iterable[Article]
) -> Dict:
    nodes: Dict[ContainerKey, Dict] = {}
    roots: List[Dict] = []

    for c in containers:
        key = _container_key(c)
        node = {
            "level": c.level,
            "number": c.number,
            "name": c.name,
            "range_start": c.range_start or None,
            "range_end": c.range_end or None,
            "status": c.status,
            "children": [],
            "articles": [],
        }
        nodes[key] = node
        if len(key) == 1:
            roots.append(node)
        else:
            parent_key = key[:-1]
            parent = nodes.get(parent_key)
            if parent is None:
                # Orphaned container — surface at top level so it isn't dropped silently
                roots.append(node)
            else:
                parent["children"].append(node)

    for article in articles:
        owner_key = _article_owner_key(article)
        owner = nodes.get(owner_key)
        if owner is None:
            # Fall back to the longest matching prefix of the owner chain
            for length in range(len(owner_key) - 1, 0, -1):
                owner = nodes.get(owner_key[:length])
                if owner is not None:
                    break
        if owner is not None:
            owner["articles"].append(article.article_number)

    # Sort article lists by article-number key for stable output
    for node in nodes.values():
        node["articles"].sort(key=article_number_sort_key)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "roots": roots,
    }


def write_tree(
    out_path: Path,
    containers: Sequence[Container],
    articles: Iterable[Article],
) -> Dict:
    tree = build_tree(containers, list(articles))
    out_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False))
    return tree


def max_depth(tree: Dict) -> int:
    def walk(node: Dict, depth: int) -> int:
        children = node.get("children") or []
        if not children:
            return depth
        return max(walk(c, depth + 1) for c in children)

    return max((walk(root, 1) for root in tree.get("roots", [])), default=0)
