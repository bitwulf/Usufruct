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

## 2026-05-21 — LRS pipeline scaffolded (Phases 1, 3, 4)

Implementation of `lars-test/LRS_IMPLEMENTATION_PLAN.md`. Goal was to bolt
the Louisiana Revised Statutes corpus onto the existing repo as a strictly
additive sub-package — zero behavior change for the CC pipeline.

### Package layout (all new)

```
src/usufruct/lrs/
├── corpus.py                  URN form, URL templates, citation form
├── fetch/                     Thin wrappers around the shared CachedClient
│   ├── justia_root.py
│   ├── justia_title.py
│   └── legis_section.py
├── parse/
│   ├── justia_root_parser.py        index.html → 54 Title listings
│   ├── justia_title_parser.py       title-N.html → (containers, sections)
│   ├── legis_lrs_toc_parser.py      Law.aspx?d= TOC anchors (Phase 2 helper)
│   └── legis_section_parser.py      Law.aspx?d=N → ParsedRSSection
├── pipeline/
│   ├── paths.py                     LRSPaths (writes under data/rs/)
│   ├── hierarchy.py                 LRS-specific interval index by (title, sect)
│   └── orchestrate.py               Phase1/2/3/4 wiring + manifests/snapshot
└── model/
    └── schema.py              RSSection, Container, ContainerLevel,
                               HierarchyNode (LRS-flavored), section_sort_key
```

`cli.py` gained an additive `rs` subparser group. The existing flat CC
subcommands keep their exact behavior.

### CC back-compat: contract honored

Per the plan's strict-isolation rule, **no edits** to anything under
`src/usufruct/{fetch,parse,pipeline,model}/`. The two pre-approved
genericization exceptions (`pipeline/hierarchy.py`, `pipeline/tree.py`) were
**not needed**: the LRS data model is different enough (keyed on
`(title, section)` not just `article_number`, with deeper container chains
including `subtitle`/`part`/`subpart`/`subgroup`) that a parallel
implementation in `src/usufruct/lrs/pipeline/` was cleaner than retrofitting
the CC modules. Convergence can happen later in the planned `Provision`
refactor.

All 60 CC tests still pass with zero modification.

### Schema decisions

- `RSSection.section_number` is a **string** for parity with CC's
  `article_number`. `"30.1"` and `"43.1.1"` are single logical sections.
  `section_sort_key` returns `(int, int, int)` tuples to accommodate the
  rare third decimal (e.g., 14:43.1.1).
- `Container` is corpus-specific: includes `parent_chain: List[(level,
  number)]` because the same chapter/part/subpart numbers repeat across
  Titles. Disambiguation flows through the chain.
- `ContainerLevel` is a string enum (vs. CC's `Literal` alias) so the LRS
  parser can use enum members without fighting the type system. Pydantic
  config `use_enum_values=True` keeps the JSON shape identical.
- `HierarchyNode` is **redefined** in the LRS schema. The CC version's
  `level` field is a `Literal` restricted to CC levels (`preliminary_title`,
  `book`, `title`, `chapter`, `section`, `subsection`, `paragraph`); LRS
  levels like `part`/`subpart`/`subgroup`/`subtitle` would fail validation
  if we tried to reuse it. The shapes are identical; the literal is the
  only thing that differs. `ActsCitation` *is* reused unchanged from CC.

### Parser findings against the four fixtures

| Fixture | Containers | Sections | Repealed (anchor) | NOTE: |
| ---- | ---: | ---: | ---: | ---: |
| `index.html` | 54 (Titles) | — | — | — |
| `title-1.html` | 3 | 41 | 0 | 0 |
| `title-14.html` | 70 | 711 | 56 | 6 |
| `title-47.html` | 235 | 2665 | 408 | 24 |

Title 14's 711 sections and Title 47's 2665 sections match the plan's
volume table exactly. Title 14's repealed count (56) is higher than the
plan's "~25" estimate — that estimate appears low; this is the corpus
truth. Title 47's 408 repealed sections is a large fraction of the Title
(15%) and worth flagging.

### HTML quirks the parser handles

The Justia per-Title pages use a `<strong class="heading-6 font-w-bold">`
block whose internal `<p>` tags have inconsistent closing-tag casing
(`</P>` vs `</p>`) and often end mid-`<p>` (the closing `</strong>` lands
inside an unclosed `<p>`). BeautifulSoup with `lxml` normalizes most of
this. The parser walks `<p>` children inside each strong block and:

1. Drops empty `<p>` and the `LOUISIANA REVISED STATUTES` banner.
2. Pulls out `NOTE:` paragraphs into a side channel (`data/rs/notes.jsonl`).
3. Classifies each remaining `<p>` by regex against TITLE / SUBTITLE /
   CHAPTER / PART / SUBPART / numbered-subgroup. Continuation lines
   (multi-line headings like `SUBPART F. WITHHOLDING INCOME TAX ON WAGES,
   AND / DECLARATION OF TAX BY INDIVIDUALS`) get concatenated into one
   heading.
4. Maintains a per-level context dict. Setting one level resets all deeper
   levels (the "stateful diff" rule).
5. Idempotent on exact duplicates: Title 1's two consecutive
   `CHAPTER 2. MISCELLANEOUS` headers collapse to one container.

The slug-to-section rule (`rs-14-30-1` → `30.1`) is straightforward
join-the-rest-with-`.`.

### Known limitations

- **Phase 2 (legis section ID discovery)** is partially implemented. The
  parser for the legis per-Title TOC HTML exists
  (`parse_legis_title_toc`), but the orchestrator does not yet fetch all
  55 legis TOC pages — instead, `run_phase2_with_index` accepts a
  pre-built mapping and emits the join. For real-world use, the next step
  is wiring `run_phase2_fetch` to walk the legis root TOC for folder IDs
  and then fetch each Title TOC. This is fine for now because no LRS
  release is being cut yet; tests pass with a fixture-backed mapping.
- **`parse_acts_citation_line` skips special-session entries** like
  `Acts 2002, 1st Ex. Sess., No. 128, §2`. The R.S. 14:30 fixture has one
  such entry, and the parser returns 28 of 29 acts. CC tests don't hit
  this pattern so it never surfaced. The fix would extend the regex in
  `parse/acts_parser.py`, but that's a touch on CC code, which the plan
  treats as escalation-required. Flagged for follow-up.
- **Wave 1 / Wave 2 fixtures not yet downloaded.** The plan lists 13
  pinned regression fixtures; this initial scaffolding only used 4 Justia
  pages + 1 legis section. Future waves should fetch the remaining
  fixtures (`rs-1-1.html`, `rs-14-67.html`, `rs-9-2800.html`, etc.) and
  add their tests.
- **Phase 4 citation extractor** uses a minimal regex set: `R.S. T:S`
  (intra-LRS), `Civil Code Article N` (LRS → CC), and `Code of Civil
  Procedure / Code of Criminal Procedure / Code of Evidence Article N`
  (LRS → other-corpus stubs with empty `dst_urn`). It validates fine
  against R.S. 14:30 (one `R.S. 14:107.1(C)(1)` self-edge, two
  `Code of Criminal Procedure Article 782` CrP edges). The CC pipeline's
  citation extractor is unchanged.

### Tests

42 new tests across 5 modules:

- `test_lrs_justia_root.py` (4) — root index, 54 Titles, name extraction.
- `test_lrs_justia_title.py` (15) — Title 1 banner/dup-chapter,
  Title 14 letter-hyphen subparts + subgroups + NOTE filtering + repealed
  anchor, Title 47 subtitles + subpart letter gap.
- `test_lrs_legis_section.py` (9) — `rs-14-30` parse: citation, heading,
  body, status, acts-raw, entity decoding, shared-CC-parser interop.
- `test_lrs_orchestrate.py` (7) — Phase 1 hierarchy + section index file
  output, Phase 3 record assembly, JSONL schema round-trip, repealed and
  blank synthesis paths, manifest + validation report written.
- `test_lrs_phase4.py` (7) — tree.json shape, citation edges CSV header
  + R.S./CrP edges, chunks include only actives, markdown per section,
  active body vs. repealed stub, manifest completeness block.

`.venv/bin/pytest` → **102 passed** (60 CC + 42 LRS), zero regressions.

### What's not yet there

- Pilot release artifacts for Title 1 (Wave 1) or Title 14 (Wave 2). The
  scaffolding will produce them; the next step is wiring the Phase 2
  fetcher and running the pipeline against the real legis.la.gov site at
  1 req/s. Per the plan, that means ~55 fetches for Phase 2 then 41-711
  per pilot wave for Phase 3.
- `verify_release.sh` updates for an LRS variant — defer until first
  release tag is cut.
- The cross-corpus integration test (one CC article + one Title 9 LRS
  section coexisting in one run) — defer to Wave 3 (Title 9) work.

## 2026-05-21 — Justia cache bootstrap + Phase 1 full corpus run

Justia is Cloudflare-protected. The 55 Justia HTML files (LRS root +
54 per-Title pages) were captured manually into `lars-test/html/` exactly
so we don't have to scrape Justia. Wrote `scripts/seed_justia_cache.py`
to copy each fixture into the `CachedClient` cache at `data/raw/{sha[:2]}/`
and register its source URL in `data/raw/index.json`. After one run of
the seed script, `usufruct rs phase1` completes against the **full
54-Title corpus offline** with zero network calls.

### Phase 1 corpus-wide validation

| Metric | Plan baseline | Actual |
| --- | --- | --- |
| Titles | 54 | 54 |
| Section anchors | 46,232 | 46,232 (exact) |
| Sub-decimal sections (e.g. 43.1.1) | 494 | 494 |
| NOTE: annotations | ~296 | 297 |
| Subtitle distribution T11/T30/T39/T47 | 4/2/3/11 | 4/2/3/11 |
| Top Titles (33/40/47/22/9/17/13/37) | matches | matches |

Top-loaded distribution is identical to the plan's table to the digit.

## 2026-05-21 — Discovered + fixed Title 9/15 CC-structure parsing

The Phase 1 corpus walk surfaced two Titles whose Justia hierarchy embeds
Civil-Code-shaped structural markers that the plan didn't anticipate:

- **Title 9** ("Civil Code—Ancillaries") has `CODE PRELIMINARY TITLE [BLANK]`,
  `CODE BOOK I-IV`, and `CODE TITLE I-XXII` markers between the LRS Title
  and its Chapters. Title 9 mirrors the Civil Code's own structure because
  it's the ancillary statutes for CC.
- **Title 15** ("Criminal Procedure") has `CODE TITLE I-XXX` markers
  *under* `CHAPTER 1` only — Chapters 1-A, 2, 3, etc. use regular PART /
  SUBPART hierarchy.

Before the fix, our parser didn't recognize the `CODE *` regexes and
treated every CODE marker as continuation text — concatenating them onto
the prior container's name. Title 9's name became `"CIVIL CODE--
ANCILLARIES CODE PRELIMINARY TITLE [BLANK] CODE BOOK I--OF PERSONS CODE
TITLE I--NATURAL AND JURIDICAL PERSONS"`, and Title 15's Chapter 1 name
included all 30 CODE TITLE labels.

### The fix

Three new `ContainerLevel` enum values, namespaced with `code_` to keep
them distinct from CC's own `book`/`title`/`preliminary_title`:

- `code_preliminary_title`
- `code_book`
- `code_title`

`code_book` and `code_preliminary_title` always sit between TITLE and
CHAPTER (their only observed home, in Title 9). `code_title` has
**dynamic placement per-Title**: the walker tracks
`_code_title_below_chapter: bool`, defaulting to False (above CHAPTER).
On the first `CODE TITLE` encountered in a Title, if `CHAPTER` is already
in scope, the flag flips to True for the rest of that Title — this is
Title 15's case. Otherwise it stays False — Title 9's case.

Two `LEVEL_ORDER` lists in `justia_title_parser.py` express the two
placements; the walker switches between them via `_level_order()`.

### Result

- **R.S. 9:2800** now chains: `title 9 → code_preliminary_title → code_book III
  → code_title V → chapter 2` (a 5-level chain that mirrors the Civil
  Code's own Book III / Title V / Chapter 3 home for the parallel
  liability article CC 2315). This is the structure Wave 3's cross-corpus
  citation work depends on.
- **Title 9 hierarchy** has 31 Chapter 1 containers across 4 CODE BOOKs;
  29 of them have distinct `parent_chain`s (the other 2 collisions are
  source ambiguity — CODE TITLE II in Title 9 genuinely contains two
  unrelated "Chapter 1"s: LOUISIANA TRUST CODE and LOUISIANA UNIFORM
  ELECTRONIC TRANSACTIONS ACT — no parser can invent a disambiguator).
- **Title 15** ends up with a clean `title 15 / chapter 1 / code_title IV`
  chain for sections in Chapter 1's CODE TITLE structure, and a clean
  `title 15 / chapter 2 / part II / subpart I` chain for Chapter 2's
  normal PART hierarchy.

8 new tests pin Title 9 (`title9_*`) and Title 15 (`title15_*`) parsing.
Full pytest: 110 passed (60 CC + 50 LRS), zero regressions.

### Container count change

| Level | Before | After | Δ |
| --- | ---: | ---: | ---: |
| title | 54 | 54 | 0 |
| subtitle | 20 | 20 | 0 |
| code_preliminary_title | — | 1 | +1 |
| code_book | — | 4 | +4 |
| code_title | — | 66 | +66 |
| chapter | 1,594 | 1,595 | +1 |
| part | 2,351 | 2,361 | +10 |
| subpart | 1,406 | 1,410 | +4 |
| subgroup | 18 | 18 | 0 |
| **Total** | **5,443** | **5,529** | **+86** |

Sections: 46,232 (unchanged). The +1 chapter and +10 parts came from
Title 15 Chapter 2+'s previously-mangled PART headers now classifying
correctly once CODE TITLE no longer ate their context.

