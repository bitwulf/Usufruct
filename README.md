# Usufruct

A scraper and data pipeline that produces a clean, structured corpus of the **Louisiana Civil Code** — the only civil-law (not common-law) code in the United States. The Usufruct name is derived from the civil-law concept of using and enjoying property without owning it.

## What you get

- **`articles.jsonl`** — every Civil Code article as a JSON record: 2,214 active, 572 repealed, 837 blank/reserved (3,623 total). Pydantic-validated, stably keyed by article number.
- **`tree.json`** — the full Book → Title → Chapter → Section → Subsection → § hierarchy with article numbers attached to their owning containers.
- **`chunks.jsonl`** — one RAG-ready record per active article with breadcrumb, body text, and prev/next neighbor pointers.
- **`citation_edges.csv`** — cross-references between articles, extracted from body text.
- **`markdown/`** — one `.md` file per article with YAML frontmatter (urn, status, breadcrumb, acts citations).
- **`manifest.json` + `validation_report.json`** — schema version, totals by status, per-Book breakdown, and any gaps between expected and emitted records.

Every record carries `source_url`, `source_html_hash` (SHA-256 of the page it was parsed from), and `scrape_timestamp` so you can diff between scrapes and verify provenance.

## Why this exists

Louisiana is the only U.S. state whose private law is a codified civil-law code rather than common-law precedent. The Civil Code is the foundational artifact, but the official source ([legis.la.gov](https://legis.la.gov)) ships it as ~2,500 separate ASP.NET pages with no machine-readable export, and the LSU annotated index ([lcco.law.lsu.edu](https://lcco.law.lsu.edu)) has carried stale text since 2015. There is no public, structured, parse-friendly version of the bare statutory text.

Usufruct fixes that for downstream uses — legal research tooling, RAG/LLM applications, jurisprudence study, citation graph analysis, comparative-law work.

## Get the data

The corpus is published as **GitHub Releases** in this repo. Each release is an immutable, dated snapshot of a successful scrape and ships the full set of derived artifacts as a zipped archive.

```sh
# Latest snapshot (replace TAG with the release tag, e.g. 2026-05-20)
gh release download TAG --repo bitwulf/usufruct --pattern '*.zip'
```

You do **not** need to run the scraper to use the data. The scraper is included so the corpus is reproducible end-to-end, but most users should just download the latest release.

## Sample record

```json
{
  "urn": "urn:us-la:civcode:art:2315",
  "article_number": "2315",
  "heading": "Liability for acts causing damages",
  "text": "A. Every act whatever of man that causes damage to another obliges him by whose fault it happened to repair it.\n\nB. Damages may include loss of consortium, service, and society, and shall be recoverable by the same respective categories of persons who would have had a cause of action for wrongful death of an injured person...",
  "status": "active",
  "hierarchy_path": [
    {"level": "book", "number": "III", "name": "Of the Different Modes of Acquiring the Ownership of Things"},
    {"level": "title", "number": "V", "name": "Obligations Arising without Agreement"},
    {"level": "chapter", "number": "3", "name": "Of Offenses and Quasi Offenses"}
  ],
  "breadcrumb": "Book III › Title V › Chapter 3",
  "acts_citations": [
    {"act_year": 1884, "act_number": 71, "section": null, "effective_date": null, "effective_date_raw": null, "role": "enactment"},
    {"act_year": 1986, "act_number": 211, "section": 1, "effective_date": null, "effective_date_raw": null, "role": "amendment"}
  ],
  "acts_citations_raw": "Amended by Acts 1884, No. 71; ...; Acts 2001, No. 478, §1.",
  "source_url": "https://legis.la.gov/legis/Law.aspx?d=109369",
  "website_law_id": 109369,
  "scrape_timestamp": "2026-05-20T14:30:00Z",
  "source_html_hash": "sha256:abc123…",
  "schema_version": "1.0.0"
}
```

Schema rules in brief:

- `article_number` is **always a string** (`"2315"`, `"2315.1"`, `"103.1"`). Sub-numbered articles like `2315.1` are independent articles, not sub-parts of `2315`.
- `status` is one of `active`, `repealed`, `reserved`, `blank`. Blank-by-design ranges (e.g., CC 16–23) and repealed-range members get first-class records — they're never silently skipped.
- For non-active records, `text` is null and `acts_citations` is empty; `acts_citations_raw` carries the repeal note where one exists.
- `hierarchy_path` is variable depth (1–5 container levels above the article).
- `urn` follows `urn:us-la:civcode:art:{article_number}` exactly.

The full schema lives in [`src/usufruct/model/schema.py`](src/usufruct/model/schema.py).

## Sources

| Role | Source | Volume |
| --- | --- | --- |
| Hierarchy | LSU CCLS TOC (`lcco.law.lsu.edu/?uid=1&ver=en`) | One fetch, cached forever |
| Article IDs | legis.la.gov TOC (`Laws_Toc.aspx?folder=67&level=Parent`) | One fetch, cached forever |
| Article text | legis.la.gov per-article pages (`Law.aspx?d=NNNNNN`) | ~2,500 fetches at 1 req/sec |

We do **not** fetch LSU article pages — their text has been stale since 2015-12-09. We use LSU only for the hierarchical table of contents.

## How it works

Four phases, each idempotent and resumable:

1. **Structural skeleton** — parse the LSU TOC into 410 hierarchical container records with article-number ranges. → `data/hierarchy.json`.
2. **Article ID discovery** — parse the legis.la.gov TOC into `{article_number: website_law_id}` (2,512 entries). → `data/article_index.json`.
3. **Article scrape** — for each ID, fetch + SHA-256 cache + parse into the canonical schema, assign hierarchy by deepest-matching-interval lookup, back-fill repealed ranges, and synthesise blank records for any LSU range members missing from legis. → `data/articles/*.json` + `data/articles.jsonl`.
4. **Derived artifacts** — pure transforms over `articles.jsonl` + `hierarchy.json`. → `data/tree.json`, `data/citation_edges.csv`, `data/chunks.jsonl`, `data/markdown/*.md`, augmented `data/manifest.json`.

Re-running is safe: the cache layer ensures already-fetched pages are not re-requested, and per-article files are overwritten only when the underlying HTML hash has changed.

## Reproducing from scratch

Install:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

Run:

```sh
.venv/bin/usufruct phase1                # ~instant (cached LSU TOC)
.venv/bin/usufruct phase2                # ~instant (cached legis TOC)
.venv/bin/usufruct phase3                # ~42 minutes @ 1 req/s for 2,512 articles
.venv/bin/usufruct phase4                # ~2 seconds (pure transform)
.venv/bin/usufruct snapshot              # archives data/ → snapshots/YYYY-MM-DD/

# Or one command:
.venv/bin/usufruct all
```

Outputs land in `data/`:

```
data/
├── raw/                 # cached HTML keyed by SHA256
├── hierarchy.json       # 410 containers, sorted intervals
├── article_index.json   # {article_number: website_law_id}
├── articles/            # one .json per article
├── articles.jsonl       # concatenated source of truth
├── tree.json            # nested container hierarchy with article leaves
├── citation_edges.csv   # cross-references in active article text
├── chunks.jsonl         # RAG-ready chunks (one per active article)
├── markdown/            # per-article markdown with YAML frontmatter
├── manifest.json        # totals + schema version + completeness stats
└── validation_report.json
```

## Tests

```sh
.venv/bin/pytest
```

60 tests run offline against cached HTML fixtures in `tests/fixtures/`. Required test-case articles (CC 1, 8, 60, 90.1, 103.1, 162, 2315, 2315.1, 3141, 3192, 185, 14, 15) are pinned as regression tests so parser drift surfaces in <20 seconds, long before any full crawl.

## Limitations & non-goals

This project is deliberately narrow. The following are **out of scope** and will not be accepted as feature requests:

- **No revision comments or editor's notes.** Those exist only in the copyrighted LSLI-annotated edition. Statutory text is public-domain; annotations are not.
- **No cross-reference links to other codes or jurisprudence.** We extract internal Civil Code cross-references via regex (`citation_edges.csv`), but we do not link out to the Revised Statutes, the Code of Civil Procedure, or case law.
- **No historical versions.** Each scrape captures the code as it appeared on that day. We do not maintain a time-series of amendments — though `acts_citations` does enumerate every amending act, so the amendment history is recoverable from the record.
- **No semantic enrichment.** No topic tagging, no embeddings, no AI-generated summaries. `chunks.jsonl` is designed to feed those downstream systems; we don't ship them ourselves.
- **Other codes not yet shipped.** The current release is Civil Code only. The Louisiana Revised Statutes are planned next, followed by the Children's Code, Code of Civil Procedure, Code of Criminal Procedure, and Code of Evidence. See [ROADMAP.md](ROADMAP.md). Until those land, they remain out of scope for feature requests against the current corpus.
- **English text only.** The Civil Code is published in English; the historic French text of the 1825/1870 codes is not included.

## Operational policy

- **Rate limit:** 1 request/second to legis.la.gov. Do not raise this; it is a state government site, not a CDN.
- **User-Agent:** identifies the project and includes a contact email so a state-IT admin can reach a human if something goes wrong.
- **Cache:** every fetched URL is hashed and saved under `data/raw/`; reruns reuse the cache.
- **Snapshots:** each completed scrape is copied to `snapshots/YYYY-MM-DD/` as an immutable archive and published as a GitHub Release.
- **Provenance:** every record carries `scrape_timestamp` + `source_html_hash` for cheap diffs between scrapes.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, testing conventions, and how to add new article fixtures.

## Citation

If you use Usufruct or the corpus it produces in a publication, please cite per [CITATION.cff](CITATION.cff).

## Acknowledgments

- The **Louisiana State Legislature** (legis.la.gov) for publishing the authoritative text of the Civil Code online.
- The **LSU Paul M. Hebert Law Center, Center for Civil Law Studies** for the structural index at lcco.law.lsu.edu, which makes the code's hierarchy machine-readable in one fetch.

Neither organization is affiliated with this project, and any errors are ours, not theirs.

## License

Code: **MIT** — see [LICENSE](LICENSE).

Statutory text: **public domain**. The text of the Louisiana Civil Code is not copyrightable. The MIT license applies only to the scraper code and to the structural / provenance metadata produced by the pipeline.
