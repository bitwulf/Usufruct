"""Per-article Markdown export — Phase 4 derived artifact.

Each article becomes ``data/markdown/{article_number}.md`` with YAML
frontmatter capturing the canonical metadata and a body containing the
article text. Blank/repealed records still get a stub file so downstream
consumers can rely on every article number being present.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from ..model import Article, article_number_sort_key


def _yaml_str(s: str) -> str:
    # Quote and escape for double-quoted YAML scalars.
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _frontmatter(article: Article) -> str:
    lines: List[str] = ["---"]
    lines.append(f"urn: {_yaml_str(article.urn)}")
    lines.append(f"article_number: {_yaml_str(article.article_number)}")
    lines.append(f"status: {article.status}")
    if article.heading:
        lines.append(f"heading: {_yaml_str(article.heading)}")
    if article.breadcrumb:
        lines.append(f"breadcrumb: {_yaml_str(article.breadcrumb)}")
    if article.source_url:
        lines.append(f"source_url: {_yaml_str(article.source_url)}")
    if article.acts_citations_raw:
        lines.append(
            f"acts_citations_raw: {_yaml_str(article.acts_citations_raw)}"
        )
    if article.acts_citations:
        lines.append("acts_citations:")
        for cit in article.acts_citations:
            lines.append(
                f"  - act_year: {cit.act_year}"
            )
            lines.append(f"    act_number: {cit.act_number}")
            if cit.section is not None:
                lines.append(f"    section: {cit.section}")
            if cit.effective_date:
                lines.append(f"    effective_date: {cit.effective_date}")
            lines.append(f"    role: {cit.role}")
    lines.append(f"schema_version: {_yaml_str(article.schema_version)}")
    lines.append("---")
    return "\n".join(lines)


def render_article(article: Article) -> str:
    fm = _frontmatter(article)
    heading_part = (
        f"# Art. {article.article_number}. {article.heading}"
        if article.heading
        else f"# Art. {article.article_number}"
    )
    if article.status == "active" and article.text:
        body = article.text
    elif article.status == "repealed":
        note = article.acts_citations_raw or "Repealed."
        body = f"*Repealed.* {note}"
    elif article.status == "reserved":
        body = "*Reserved.*"
    else:
        body = "*Blank — no article exists at this number in the current code.*"
    return f"{fm}\n\n{heading_part}\n\n{body}\n"


def write_markdown(out_dir: Path, articles: Iterable[Article]) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.md"):
        old.unlink()
    arts = sorted(articles, key=lambda a: article_number_sort_key(a.article_number))
    for art in arts:
        (out_dir / f"{art.article_number}.md").write_text(render_article(art))
    return len(arts)
