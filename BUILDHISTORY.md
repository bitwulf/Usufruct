# Usufruct Build History

A running log of development notes, decisions, and changes.

## 2026-05-20 — Initial implementation

### Source reconnaissance

- LSU TOC (`https://lcco.law.lsu.edu/?uid=1&ver=en`) is a single page (~60 KB) with one outermost `<ul>` containing the full nested hierarchy. Each `<li>` has an `<a>` whose `<b>` holds the container label ("Preliminary Title", "Book I", "Title V", "Chapter 1", "Section 1", "Subsection A", "§1") and trailing text gives description + article range "(Art. X to Y)".
- The LSU page also renders the text of `uid=1` (Preliminary Title), which appends a free-floating `<p>Arts. 15-23. [Blank]</p>` outside the TOC `<ul>`. We do NOT depend on this stray marker — we derive blank/repealed status from the legis.la.gov pass instead, which is robust to other LSU `uid` ranges we don't fetch.
- Special container markers: `[Repealed]`, `[Reserved]` appear as suffixes on the container description; `Title XX-A`, `Title XXII-A` show letter-suffixed Roman numerals; `§1`…`§6` are paragraph-level containers (with NBSP between `§N` and the name).
- legis.la.gov TOC is a flat `<table>` of `<a href="Law.aspx?d=NNNNNN">` links with each article appearing twice: once as "CC NNN" (article number) and once as the heading. 2,512 unique `d=` IDs (lower than the ~3,500 estimated in the prompt; the gap is mostly repealed/blank ranges that legis.la.gov collapses into single placeholder articles like `CC 60 = "Arts. 60 to 85 repealed..."`).
- Article pages have a `<span id="ctl00_PageBody_LabelDocument">` containing paragraphs. The CSS classes `A0001`/`A0002`/`A0003` are NOT reliable across articles — same logical role gets different classes. Parse by content pattern instead:
  - First `<p>` matching `^Art\. <num>\.` is the article line (may carry the heading after the period).
  - Top-of-document paragraphs before that line are container breadcrumb (ignore — we get hierarchy from LSU).
  - Final paragraph(s) starting with `Acts ` or `Amended by Acts ` are the acts-citation block.
  - Everything between = body text, paragraphs joined with `\n\n`.
- Repealed-range articles (CC 60, CC 162, CC 3550, ...) put the whole repeal notice inside the Art line itself: `Art. 60.  Arts. 60 to 85 repealed by Acts 1990, No. 989, §1, eff. January 1, 1991.` — detect by `^Arts?\.\s+\d.*\b[Rr]epealed by Acts\b`.

### Architecture decisions

- **HTTP client is one module (`fetch/client.py`)** used by all three fetch modules. Rate limit (1 req/sec to legis.la.gov), exponential backoff retry on 5xx/timeout, custom User-Agent with project name + contact email.
- **Cache layer keys by SHA256 of response body**, stored in `data/raw/`. The cache also writes a sidecar `data/raw/index.json` mapping URL → hash so reruns can find existing fetches without re-hashing. Re-running the pipeline never re-fetches unless the URL is new or cache files were deleted.
- **Blank/repealed range filling is post-processing**, not part of per-article parsing. After Phase 3 finishes parsing all fetched articles, the orchestrator (a) detects "Arts. X to Y [Rr]epealed by ..." patterns and back-fills records for X+1 through Y with status=repealed, (b) walks LSU hierarchy ranges and fills any still-missing article numbers as status=blank.
- **Hierarchy lookup** uses a list of (range_start_key, range_end_key, container_chain) intervals, sorted by `(range_start_key, -range_end_key)` so the deepest matching interval wins for a given article number. Article number keys are `(int_part, decimal_part)` tuples so `"2315.1"` sorts between `"2315"` and `"2316"`.

### Schema notes

- Pydantic v2 for schema validation. `article_number` is `constr(min_length=1)` (string, never int).
- `acts_citations_raw` preserves the original string verbatim including any "Amended by " prefix; parsed `acts_citations` strip the prefix. First parsed entry gets `role: "enactment"`; the rest `role: "amendment"`. This is the literal rule in the prompt; we accept it can be a misnomer for very old articles whose true enactment predates the cited acts list.
- For status `repealed`/`reserved`/`blank`, `text` is null and `acts_citations` is empty; `acts_citations_raw` holds the repeal note where one exists.

### Testing approach

- Cached HTML fixtures live in `tests/fixtures/articles/` and `tests/fixtures/lsu_toc.html`. Bootstrap HTML was fetched once during development and copied into fixtures.
- Test cases per the prompt: CC 1, 8, 60, 90.1, 103.1, 162, 2315, 2315.1, one Title XX-A article (Pledge; CC 3141), CC 3192 for deep nesting (Book III › Title XXI › Chapter 3 › Section 1 › §1 — five container levels plus the article = six total), CC 185 for subsection-deep nesting, and CC 15 (which is now a real article on legis.la.gov post-2015 — the prompt's "blank" assumption is stale, but the underlying blank-range fill is still verified by CC 16–23 which legis.la.gov does omit).
- Tests run offline against fixtures only — no network access required.
- 47 tests across LSU TOC parsing, legis TOC parsing, article parsing, acts-citation parsing, hierarchy lookup, and end-to-end orchestration with a fixture-backed `FakeClient`.

### Findings during validation

- LSU TOC parser yields **410 containers** (the prompt's "~200" estimate was low — Sections and Subsections are deeper-nested than expected; 173 sections + 160 chapters + 52 titles + 12 paragraph-level §'s + 7 subsections + 4 books + 2 preliminary titles = 410).
- legis.la.gov TOC parser yields **2,512 unique articles** (the prompt's "~3,500" estimate was high — legis collapses every repealed range like `Arts. 60 to 85 repealed by ...` into a single placeholder record, so 87 articles in this case become one).
- End-to-end smoke test (13 seeded articles + LSU range backfill) produces **3,559 total records**: 11 active, 40 repealed (including 38 synthesised by the repeal-range backfill), 3,508 blank (synthesised by LSU range walk). All records round-trip through the Pydantic schema cleanly.
- Article number `90.1` exists on legis.la.gov (`d=1147329`) and `103.1` exists (`d=408225`); these confirmed the URL-pattern hints in the prompt.
- CC 15 ("Use of number") is on legis.la.gov as an active article — added by Acts 2025, No. 488 — so the prompt's "Arts. 15-23. [Blank]" example is partially stale. CC 16–23 still don't exist on legis and are correctly synthesised as `status: blank` records by the orchestrator's LSU-range fill pass.

### Things deliberately left out (Phase 4 — deferred)

- Markdown export per article
- Citation edges CSV
- RAG-ready chunks JSONL
- Hierarchical tree JSON
- Updated manifest with completeness stats beyond what `manifest.json` already records

### Reproducing a full crawl

```sh
.venv/bin/usufruct phase1                       # ~instant (cached LSU TOC)
.venv/bin/usufruct phase2                       # ~instant (cached legis TOC)
.venv/bin/usufruct phase3                       # ~42 minutes @ 1 req/s for 2,512 articles
.venv/bin/usufruct snapshot                     # archive into snapshots/YYYY-MM-DD
```

Reruns are safe — the cache layer means already-fetched articles are not re-requested.

## 2026-05-20 — Phase 4 derived artifacts

After the first full crawl was snapshotted into `snapshots/2026-05-20/`, Phase 4
was built out. All four derivations are pure transforms over `articles.jsonl`
+ `hierarchy.json` — no network access required.

### New modules

- `pipeline/tree.py` — nested tree from the flat container list. Containers are
  joined by their full root-to-leaf chain `(level, number, name)` so sibling
  Titles that reuse Roman numerals across different Books stay distinct.
  Articles attach to their deepest matching container; fallback to the longest
  prefix when an exact match isn't present.
- `pipeline/citations.py` — regex extractor for cross-references in active
  article text. Matches `Article(s)`/`Art(s).` prefix, then expands compound
  lists ("Articles 102 and 103", "Arts. 60 to 85") into one row per referenced
  article. Output: `data/citation_edges.csv` with `src_urn, src_article,
  dst_article, raw_match, char_offset`.
- `pipeline/chunks.py` — RAG-ready JSONL. One chunk per active article with
  breadcrumb, heading, body text, prev/next neighbor numbers, and source URL.
  Skips blank/repealed so the retrieval index doesn't carry empties.
- `pipeline/markdown.py` — per-article `.md` with YAML frontmatter (urn,
  status, breadcrumb, acts_citations) and body. Blank/repealed get stub bodies
  so the directory stays complete.

### Wiring + CLI

- `usufruct phase4` runs all four derivations and rewrites `manifest.json` with
  a `completeness` section: per-Book status breakdown, tree max depth, edge
  count, chunk count, markdown file count.
- `usufruct all` now chains phase1 → phase2 → phase3 → phase4 → snapshot.
- `snapshot` also archives `tree.json`, `citation_edges.csv`, `chunks.jsonl`,
  and `markdown/` alongside the Phase 3 outputs.

### Findings from the first Phase 4 run

- **Tree shape:** 5 roots (Preliminary Title + Book I–IV). Max depth 5: deepest
  chain is Book I › Title VII › Chapter 2 › Section 2 › Subsection A
  (Filiation / Proof of Paternity). The `paragraph` (§) level also reaches
  depth 5 via Book III › Title XXI › Chapter 3 › Section 1 › §1 — the test
  pins CC 3192 there.
- **Citation edges: only 146 across the whole corpus.** Far lower than expected.
  Louisiana civil-law articles tend to state their own rule rather than chain
  off other articles, unlike common-law statutes. The bulk of the 146 are
  amendment-era cross-references (e.g., CC 102/103/103.1 cluster on divorce
  time periods). `Article(s)` is the prefix used 109 times; `Art(s).` only
  twice in body text.
- **RAG chunks: 2,214** — exactly equal to the active count, as expected.
- **Markdown: 3,623** — one per article including blank/repealed stubs.
- **Per-Book breakdown:** Book III alone is 2,700/3,623 records (75% of the
  corpus). Preliminary Title 23, Book I 430, Book II 428, Book III 2,700,
  Book IV 42.

### Phase 4 tests

`tests/test_phase4.py` adds 13 tests on top of the existing 47. The fixture
setup reuses `FakeClient`/`TEST_ARTICLES` from `test_orchestrate.py` via a
`pythonpath = ["tests"]` entry in `pyproject.toml`. Coverage:

- tree roots, container assignment for CC 3192, max-depth agreement with stats
- compound citation extraction for CC 103.1, CSV header + row shape
- chunks-skip-non-active, prev/next neighbors
- markdown frontmatter for active articles, stubs for repealed/blank, quote
  escaping for headings containing `"`
- manifest `completeness.by_book.total` sums equal `totals.articles_emitted`
- pure-function `build_tree` matches disk output

### Reproducing Phase 4 from an existing crawl

```sh
.venv/bin/usufruct phase4
.venv/bin/usufruct snapshot
```

## 2026-05-20 — Open-source release prep

Documentation pass ahead of making the repo public. No code changes; all
additions are docs / project metadata.

### Added

- `LICENSE` — MIT, with an explicit carve-out noting that the statutory text
  itself is public domain and not subject to the MIT terms.
- `CONTRIBUTING.md` — dev setup, the rate-limit etiquette rule, schema /
  style conventions, fixture-addition workflow, PR expectations.
- `CITATION.cff` — Citation File Format manifest for the corpus + code.
  Placeholder `USER` in the repo URL is intentional; fill in once the repo
  is published.

### Changed

- `README.md` — substantial restructure for a public audience. Top section
  is now "What you get" (concrete artifact list with counts), followed by
  "Why this exists" (Louisiana civil-law context, gap in existing
  resources), "Get the data" (points at GitHub Releases — the data is not
  in the repo). Added a full sample record (CC 2315) and a "Limitations &
  non-goals" section enumerating what we explicitly do not include
  (revision comments, jurisprudence links, historical versions, semantic
  enrichment, other codes, non-English text). Added "Acknowledgments" for
  LSU CCLS and the Louisiana State Legislature.
- `.gitignore` — added the Phase 4 derived outputs (`chunks.jsonl`,
  `citation_edges.csv`, `tree.json`) and the AI-assist files (`CLAUDE.md`,
  `prompt.md`) so they stay local but don't ship in the public repo.

### Data-release plan

Decision: ship each completed scrape as a **GitHub Release** with the
zipped snapshot attached. Lowest-overhead path; the snapshot in
`snapshots/2026-05-20/` is the candidate for the first release tag. The
README's "Get the data" section already points at this workflow.

### Things deliberately deferred

- GitHub Actions CI for pytest — not selected this pass; running locally
  is fine for now. Trivial to add later (single workflow file).
- `CHANGELOG.md` / `CODE_OF_CONDUCT.md` / badges — common OSS scaffolding
  but not blocking for a respectable public launch.
- A `docs/` folder with deep-dive design notes split out of README — the
  current README is dense but still navigable.
