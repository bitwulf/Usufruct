# Usufruct

A scraper and data pipeline that produces clean, structured corpora of
**Louisiana's enacted law**: the **Louisiana Civil Code** (the only civil-law
— not common-law — code in the United States) and the **Louisiana Revised
Statutes** (Titles 1–56). The Usufruct name is derived from the civil-law
concept of using and enjoying property without owning it.

A public reading surface for both corpora lives at
[**theusufruct.com**](https://theusufruct.com).

## What you get

A single release zip ships both corpora side by side: Civil Code at the
archive root, Revised Statutes under `rs/`.

### Civil Code (root of the archive)

- **`articles.jsonl`** — every Civil Code article as a JSON record: 2,214
  active, 572 repealed, 837 blank/reserved (3,623 total). Pydantic-validated,
  stably keyed by article number.
- **`tree.json`** — the full Book → Title → Chapter → Section → Subsection →
  § hierarchy with article numbers attached to their owning containers.
- **`chunks.jsonl`** — one RAG-ready record per active article with
  breadcrumb, body text, and prev/next neighbor pointers.
- **`citation_edges.csv`** — cross-references between articles, extracted
  from body text.
- **`markdown/`** — one `.md` file per article with YAML frontmatter (urn,
  status, breadcrumb, acts citations).
- **`articles/`** — same records as `articles.jsonl`, one JSON file per
  article — easier random access.
- **`hierarchy.json`** — flat list of all 410 hierarchy containers with
  parent chains.
- **`manifest.json` + `validation_report.json`** — schema version, totals by
  status, per-Book breakdown, and any gaps between expected and emitted
  records.

### Revised Statutes (`rs/` subdirectory)

- **`rs/sections.jsonl`** — every LRS section as a JSON record: 39,129
  active, 6,280 repealed, 365 blank (45,774 total). Same shape as CC
  `articles.jsonl`, but identity is `(title_number, section_number)` since
  section numbers are not globally unique across titles.
- **`rs/tree.json`** — full Title → Chapter → Part → Subpart hierarchy
  across 5,529 containers, with section numbers at each leaf.
- **`rs/citation_edges.csv`** — cross-references; columns include source and
  destination corpus, so RS↔CC edges are addressable too.
- **`rs/markdown/title-{N}/{section}.md`** — per-section markdown,
  partitioned by Title. Decimal points in section numbers are encoded as
  underscores (`30.1` → `30_1.md`).
- **`rs/sections/rs_{title}_{section}.json`** — same records as
  `sections.jsonl`, one JSON file per section, same underscore encoding.
- **`rs/hierarchy.json`** — flat list of 5,529 containers with parent
  chains.
- **`rs/manifest.json` + `rs/validation_report.json`** — schema version,
  totals by status, per-Title completeness, and gaps.

Every record in both corpora carries `source_url`, `source_html_hash`
(SHA-256 of the page it was parsed from), and `scrape_timestamp` so you can
diff between scrapes and verify provenance.

## Why this exists

Louisiana is the only U.S. state whose private law is a codified civil-law
code rather than common-law precedent. The Civil Code is the foundational
artifact, and the Revised Statutes carry the rest of the legislature's
enacted law — criminal, banking, insurance, education, public records,
gaming, and more. The official source ([legis.la.gov](https://legis.la.gov))
ships both as tens of thousands of ASP.NET pages with no machine-readable
export, and the LSU annotated index ([lcco.law.lsu.edu](https://lcco.law.lsu.edu))
has carried stale text since 2015. There is no public, structured,
parse-friendly version of the bare statutory text.

Usufruct fixes that for downstream uses — legal research tooling, RAG/LLM
applications, jurisprudence study, citation graph analysis, comparative-law
work.

## Get the data

The corpus is published as **GitHub Releases** in this repo. Each release is
an immutable, dated snapshot containing both corpora as a single zipped
archive.

```sh
# Latest snapshot (replace TAG with the release tag, e.g. 2026-05-22)
gh release download TAG --repo bitwulf/usufruct --pattern '*.zip'
```

You do **not** need to run the scraper to use the data. The scraper is
included so the corpus is reproducible end-to-end, but most users should
just download the latest release.

### Verify a release

Every release ships with a `.sha256` sidecar. To confirm a release is
publicly reachable, downloads cleanly, matches its published checksum, and
contains the expected artifacts:

```sh
scripts/verify_release.sh             # verifies the latest release
scripts/verify_release.sh 2026-05-22  # verifies a specific tag
```

The script depends only on `bash`, `curl`, `python3`, `shasum`, `unzip`, and
`find` — no `gh` required.

## Sample records

### Civil Code article

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

### Revised Statutes section

```json
{
  "urn": "urn:us-la:rs:14:30",
  "title_number": "14",
  "section_number": "30",
  "citation": "R.S. 14:30",
  "heading": "First degree murder",
  "text": "A. First degree murder is the killing of a human being:\n\n(1) When the offender has specific intent to kill or to inflict great bodily harm and is engaged in the perpetration or attempted perpetration of aggravated kidnapping...",
  "status": "active",
  "hierarchy_path": [
    {"level": "title", "number": "14", "name": "CRIMINAL LAW"},
    {"level": "chapter", "number": "1", "name": "CRIMINAL CODE"},
    {"level": "part", "number": "II", "name": "OFFENSES AGAINST THE PERSON"},
    {"level": "subpart", "number": "A", "name": "HOMICIDE"}
  ],
  "breadcrumb": "Title 14 › Chapter 1 › Part II › Subpart A",
  "acts_citations": [
    {"act_year": 1973, "act_number": 109, "section": 1, "effective_date": null, "effective_date_raw": null, "role": "enactment"}
  ],
  "acts_citations_raw": "Acts 1973, No. 109, §1; …",
  "source_url": "https://legis.la.gov/legis/Law.aspx?d=78397",
  "website_law_id": 78397,
  "scrape_timestamp": "2026-05-22T21:48:17Z",
  "source_html_hash": "sha256:50930745…",
  "schema_version": "1.0.0"
}
```

Schema rules in brief:

- **CC** keys on `article_number`; **LRS** keys on
  `(title_number, section_number)` and adds an explicit `citation` field
  (`"R.S. 14:30"`). Both are **always strings** (`"2315"`, `"2315.1"`,
  `"30.1"`). Sub-numbered records like `2315.1` and `30.1` are independent,
  not sub-parts of `2315` or `30`.
- `status` is one of `active`, `repealed`, `reserved`, `blank`.
  Blank-by-design ranges and repealed-range members get first-class records
  — they're never silently skipped.
- For non-active records, `text` is null and `acts_citations` is empty;
  `acts_citations_raw` carries the repeal note where one exists.
- `hierarchy_path` is variable depth: 1–5 levels for CC, up to 7 for LRS.
  LRS uses different level vocabulary (`title`, `chapter`, `part`,
  `subpart`) than CC (`book`, `title`, `chapter`, `section`, `subsection`,
  `paragraph`, `preliminary_title`).
- `urn` follows `urn:us-la:civcode:art:{article_number}` for CC and
  `urn:us-la:rs:{title}:{section}` for LRS.

The full schema lives in
[`src/usufruct/model/schema.py`](src/usufruct/model/schema.py).

## Sources

### Civil Code

| Role | Source | Volume |
| --- | --- | --- |
| Hierarchy | LSU CCLS TOC (`lcco.law.lsu.edu/?uid=1&ver=en`) | One fetch, cached forever |
| Article IDs | legis.la.gov TOC (`Laws_Toc.aspx?folder=67&level=Parent`) | One fetch, cached forever |
| Article text | legis.la.gov per-article pages (`Law.aspx?d=NNNNNN`) | ~2,500 fetches |

We do **not** fetch LSU article pages — their text has been stale since
2015-12-09. We use LSU only for the hierarchical table of contents.

### Revised Statutes

| Role | Source | Volume |
| --- | --- | --- |
| Hierarchy | Justia LA Code (`law.justia.com/codes/louisiana/revised-statutes/title-{N}/`) | 56+ per-Title fetches, cached |
| Section IDs | legis.la.gov per-Title TOCs | 56+ fetches, cached |
| Section text | legis.la.gov per-section pages (`Law.aspx?d=NNNNNN`) | ~45,000 fetches |

There is no LSU equivalent for the Revised Statutes structure. Justia
mirrors the LRS with a clean nested TOC; we use it for hierarchy only and
go to legis.la.gov for the authoritative section text.

## How it works

Both pipelines are phase-based, idempotent, and resumable. The cache layer
ensures already-fetched pages are not re-requested, and per-record files
are overwritten only when the underlying HTML hash has changed.

### Civil Code

1. **Structural skeleton** — parse the LSU TOC into 410 hierarchical
   container records with article-number ranges. → `data/hierarchy.json`.
2. **Article ID discovery** — parse the legis.la.gov TOC into
   `{article_number: website_law_id}` (2,512 entries). →
   `data/article_index.json`.
3. **Article scrape** — for each ID, fetch + SHA-256 cache + parse into the
   canonical schema, assign hierarchy by deepest-matching-interval lookup,
   back-fill repealed ranges, and synthesise blank records for any LSU
   range members missing from legis. → `data/articles/*.json` +
   `data/articles.jsonl`.
4. **Derived artifacts** — pure transforms over `articles.jsonl` +
   `hierarchy.json`. → `data/tree.json`, `data/citation_edges.csv`,
   `data/chunks.jsonl`, `data/markdown/*.md`, augmented `data/manifest.json`.

### Revised Statutes (`usufruct rs ...`)

1. **Structural skeleton + section index** — fetch Justia's per-Title TOC
   pages and parse them into the Title → Chapter → Part → Subpart
   hierarchy plus a section-number listing. → `data/rs/hierarchy.json` +
   `data/rs/justia_section_index.json`.
2. **Section ID resolution** — fetch legis.la.gov per-Title TOC pages and
   map every section number to a `website_law_id`. →
   `data/rs/section_index.json`.
3. **Section scrape** — for each ID, the same fetch + cache + parse loop
   as CC, scaled to ~45K sections. Repealed and reserved sections get
   first-class records. → `data/rs/sections/*.json` +
   `data/rs/sections.jsonl`.
4. **Derived artifacts** — `data/rs/tree.json`,
   `data/rs/citation_edges.csv`, `data/rs/chunks.jsonl`,
   `data/rs/markdown/title-{N}/{section}.md`, augmented
   `data/rs/manifest.json`. Citation extraction handles `R.S. T:S[.sub]`
   plus CC ↔ LRS cross-corpus references.

## Reproducing from scratch

Install:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

Run the Civil Code pipeline:

```sh
.venv/bin/usufruct phase1                # ~instant (cached LSU TOC)
.venv/bin/usufruct phase2                # ~instant (cached legis TOC)
.venv/bin/usufruct phase3                # ~20 minutes @ 2 req/s for 2,512 articles
.venv/bin/usufruct phase4                # ~2 seconds (pure transform)
.venv/bin/usufruct snapshot              # archives data/ → snapshots/YYYY-MM-DD/

# Or one command:
.venv/bin/usufruct all
```

Run the Revised Statutes pipeline (note: phase 3 is ~6 hours @ 2 req/s for
~45K sections):

```sh
.venv/bin/usufruct rs phase1             # ~minutes (Justia TOCs, cached)
.venv/bin/usufruct rs phase2             # ~minutes (legis per-Title TOCs, cached)
.venv/bin/usufruct rs phase3             # ~6 hours full crawl; resumable
.venv/bin/usufruct rs phase4             # ~tens of seconds
.venv/bin/usufruct rs snapshot           # archives data/rs/ → snapshots/lrs-YYYY-MM-DD/

# Or one command (optionally limit to specific Titles for testing):
.venv/bin/usufruct rs all --titles 14,22
```

Outputs land in `data/`:

```
data/
├── raw/                       # cached HTML keyed by SHA256 (shared by both corpora)
├── hierarchy.json             # CC: 410 containers, sorted intervals
├── article_index.json         # CC: {article_number: website_law_id}
├── articles/                  # CC: one .json per article
├── articles.jsonl             # CC: concatenated source of truth
├── tree.json                  # CC: nested hierarchy with article leaves
├── citation_edges.csv         # CC: cross-references in active article text
├── chunks.jsonl               # CC: RAG-ready chunks (one per active article)
├── markdown/                  # CC: per-article markdown with YAML frontmatter
├── manifest.json              # CC: totals + schema version + completeness
├── validation_report.json
└── rs/                        # Revised Statutes — same shapes, distinct identity
    ├── hierarchy.json
    ├── section_index.json
    ├── justia_section_index.json
    ├── sections/              # one .json per section
    ├── sections.jsonl
    ├── tree.json
    ├── citation_edges.csv
    ├── chunks.jsonl
    ├── markdown/title-{N}/    # per-Title subfolders
    ├── manifest.json
    └── validation_report.json
```

## Tests

```sh
.venv/bin/pytest
```

Tests run offline against cached HTML fixtures in `tests/fixtures/`.
Required test-case articles (CC 1, 8, 60, 90.1, 103.1, 162, 2315, 2315.1,
3141, 3192, 185, 14, 15) and a representative set of LRS sections are
pinned as regression tests, so parser drift surfaces in under a minute —
long before any full crawl.

## Limitations & non-goals

This project is deliberately narrow. The following are **out of scope**
and will not be accepted as feature requests:

- **No revision comments or editor's notes.** Those exist only in the
  copyrighted LSLI-annotated edition. Statutory text is public-domain;
  annotations are not.
- **No jurisprudence links.** We extract internal cross-references via
  regex (`citation_edges.csv`, including CC ↔ LRS cross-corpus edges),
  but we do not link out to case law.
- **No historical versions.** Each scrape captures the corpora as they
  appeared on that day. We do not maintain a time-series of amendments —
  though `acts_citations` enumerates every amending act, so amendment
  history is recoverable from the record.
- **No semantic enrichment.** No topic tagging, no embeddings, no
  AI-generated summaries. `chunks.jsonl` is designed to feed those
  downstream systems; we don't ship them ourselves.
- **Remaining codes not yet shipped.** The current release covers the
  Civil Code and Revised Statutes. The Children's Code, Code of Civil
  Procedure, Code of Criminal Procedure, and Code of Evidence are planned
  next. See [ROADMAP.md](ROADMAP.md). Until those land, they remain out
  of scope for feature requests against the current corpus.
- **English text only.** The Civil Code is published in English; the
  historic French text of the 1825/1870 codes is not included.

## Operational policy

- **Rate limit:** default 2 requests/second to legis.la.gov, configurable
  via `--rate-limit`. Do not raise this aggressively; it is a state
  government site, not a CDN.
- **User-Agent:** identifies the project and includes a contact email so a
  state-IT admin can reach a human if something goes wrong.
- **Cache:** every fetched URL is hashed and saved under `data/raw/`;
  reruns reuse the cache and the same cache backs both pipelines.
- **Snapshots:** each completed scrape is copied to `snapshots/YYYY-MM-DD/`
  (CC) or `snapshots/lrs-YYYY-MM-DD/` (LRS) as an immutable archive and
  published as a GitHub Release.
- **Provenance:** every record carries `scrape_timestamp` +
  `source_html_hash` for cheap diffs between scrapes.

## The website

The corpus has a public reading surface at
[**theusufruct.com**](https://theusufruct.com): every CC article at
`/cc/{number}` and every LRS section at `/rs/title-{N}/section-{X}`, with
hierarchical browse, full-text search across both corpora, Bluebook /
permalink / BibTeX citation forms, and per-record JSON / Markdown
downloads. The site is a downstream consumer of the same release zip
documented above — running `scripts/fetch-corpus.sh` reproduces the same
inputs the site builds from. Site source:
[`bitwulf/theusufruct-site`](https://github.com/bitwulf/theusufruct-site).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, testing conventions,
and how to add new article fixtures.

## Citation

If you use Usufruct or the corpus it produces in a publication, please cite
per [CITATION.cff](CITATION.cff).

## Acknowledgments

- The **Louisiana State Legislature** (legis.la.gov) for publishing the
  authoritative text of the Civil Code and Revised Statutes online.
- The **LSU Paul M. Hebert Law Center, Center for Civil Law Studies** for
  the structural index at lcco.law.lsu.edu, which makes the Civil Code's
  hierarchy machine-readable in one fetch.
- **Justia** (law.justia.com) for the Revised Statutes structural mirror,
  which makes the Title → Chapter → Part hierarchy machine-readable
  without parsing legis.la.gov's nested ASP.NET TOC pages.

None of these organizations are affiliated with this project, and any
errors are ours, not theirs.

## License

Code: **MIT** — see [LICENSE](LICENSE).

Statutory text: **public domain**. The text of the Louisiana Civil Code
and Revised Statutes is not copyrightable. The MIT license applies only
to the scraper code and to the structural / provenance metadata produced
by the pipeline.
