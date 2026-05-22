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

## 2026-05-21 — Phase 2 wired against legis.la.gov + Wave 1 (Title 1) end-to-end

The Phase 2 fetcher landed and ran corpus-wide; the Title 1 pilot (Wave 1)
completed Phase 3 + Phase 4 end-to-end against real legis.la.gov data.

### legis URL pattern discoveries (the plan was wrong)

The plan said Phase 2 used `Laws_Toc.aspx?folder=75` (root) and
`Laws_Toc.aspx?folder=N&title=T` (per-Title). Both URLs are incomplete.
Empirical findings against the live site:

- **Root TOC actually lives at `Laws_Toc.aspx?folder=75&level=Parent`.**
  Without `&level=Parent`, legis routes to a different page (the all-codes
  navigation listing CC/CCP/CrP/RS/etc). The same `&level=Parent` suffix
  is what the existing CC pipeline already uses at `folder=67`.
- **Per-Title TOC needs `Laws_Toc.aspx?folder=F&title=T&level=Parent`.**
- **The root TOC does *not* expose folder IDs in HTML.** Title rows are
  ASP.NET postback anchors (`href="javascript:__doPostBack(...)"`); the
  folder ID lives in server-side ViewState. So the plan's "parse folder
  map from root TOC" strategy can't work on the real page.

### Folder IDs are deterministic — sequential by sorted Title

Empirical lookups (verified by per-Title fetch + parse round-trip):

| Title | Folder | Title | Folder |
| ---: | ---: | ---: | ---: |
| 1 | 77 | 14 | 88 |
| 2 | 78 | 15 | 89 |
| 3 | 79 | ... | ... |
| 4 | 80 | 47 | 121 |
| 6 | 81 | 56 | 130 |

The pattern is `folder = 77 + sorted_index(title_number)` over the sorted
active-Title set (1, 2, 3, 4, 6, 8, 9, 10, …, 56). Gaps at Title 5 and 7
are real. Title 47 lands at 88 + 33 = 121 because there are 33 active
Titles between 14 and 47 inclusive. The base offset `_LEGIS_LRS_BASE_FOLDER
= 77` is the only constant we hardcode.

Robustness: the fetcher verifies each per-Title TOC by checking every
`(title, section)` key returned by the parser; any foreign-Title anchor
raises `RuntimeError("Phase 2 folder mismatch …")` so a future legis
renumber fails loudly instead of silently mis-mapping.

### New module surface

- `src/usufruct/lrs/corpus.py` — added `LEGIS_LRS_ROOT_TOC_URL` and
  `LEGIS_LRS_TITLE_TOC_URL_TEMPLATE` constants.
- `src/usufruct/lrs/fetch/legis_toc.py` — new module with
  `fetch_legis_root_toc` / `fetch_legis_title_toc` thin wrappers.
- `src/usufruct/lrs/parse/legis_lrs_toc_parser.py` — gained
  `parse_legis_root_toc_titles(html) -> List[str]` (reads anchor text
  matching `^TITLE N$`); existing `parse_legis_root_toc` retained for
  back-compat with synthetic fixtures.
- `src/usufruct/lrs/pipeline/orchestrate.py` — added `run_phase2_fetch`
  with deterministic folder derivation + per-Title verification +
  Justia↔legis join + side-channel gap report.
- `cli.py` — `rs phase2` subcommand and `rs phase3 --titles` filter for
  pilot-mode scoping.

### Phase 2 corpus-wide results

Single `usufruct rs phase2` run, 55 HTTP fetches at 1 req/s:

```
54/54 Titles fetched, 45,494 section IDs joined against Justia
```

| Bucket | Count |
| --- | ---: |
| Justia sections | 46,232 |
| Legis sections found | 45,536 |
| Joined (both sides agree) | 45,494 |
| In Justia, missing on legis | 738 |
| In legis, not in Justia | 43 |

`data/rs/section_index.json`, `data/rs/folder_map.json`, and
`data/rs/phase2_gaps.json` are the outputs.

### Gap-analysis findings

The 738 "in Justia but not on legis" sections — none of which Justia
flagged repealed — are heavily concentrated in two Titles:

| Title | Missing on legis | Cause |
| --- | ---: | --- |
| 10 (Commercial Laws) | 458 | UCC sub-decimal style (`10:1.101`, `10:1.102`, …). legis exposes ~9 "umbrella" sections, Justia enumerates every UCC subsection. |
| 12 (Corporations) | 248 | Same pattern. legis collapses what Justia enumerates. |
| 29 (Military Affairs) | 24 | Mostly title-9-style structural placeholders. |
| 17, 40, 47, 32, 33 | ≤2 each | Single-section drift. |

Phase 3's existing "Justia-known but no legis ID" backfill emits these as
`status: blank` records, so the corpus carries them with empty `text` and
`source_url=None`. **Wave 1 (Title 1) hits none of these gaps**, so the
question of how to represent UCC sub-decimals canonically is deferred to
the wave that scrapes Title 10.

The 43 "in legis but not in Justia" sections cluster in Title 30 (24).
They're flagged in `phase2_gaps.json` but deliberately not emitted — we
trust Justia as the corpus-membership source of truth per the plan.

### Acts-parser format divergence between CC and LRS

R.S. 14:30 (the pinned fixture) uses semicolon-separated citations —
`Amended by Acts 1973, No. 109, §1; Acts 1975, No. 327, §1; …` — which
the shared `parse_acts_citation_line` handles correctly. But older
sections like R.S. 1:11.1 serve a different template:

```
Acts 1958, No. 498, §1. Amended by Acts 1970, No. 465, §1.
```

Period-separated, no semicolons. The shared parser splits only on `;` and
requires its regex to anchor at end-of-line, so it returned **zero
parsed citations** for the period form.

Per the plan's strict-isolation rule we can't touch
`src/usufruct/parse/acts_parser.py`. Solution: a LRS-side normalizer
(`_normalize_lrs_acts_text` in `lrs/pipeline/orchestrate.py`) that
rewrites `. Amended by Acts` → `; Acts` and `. Acts YYYY` → `; Acts YYYY`
before invoking the shared parser. 3 unit tests pin the behavior.
Re-run of Title 1 Phase 3 jumped from 18 → 19 parsed-with-citations out
of 20 with raw acts; the remaining unparsed line is `Acts 2010, No. 845,
§2, eff. June 30, 2010, and §3, eff. Jan. 1, 2012.` — single Act with two
sections / two effective dates, a known shared-parser limitation.

The 14:30 special-session quirk (`Acts 2002, 1st Ex. Sess., No. 128`) is
unrelated and still flagged for a future CC-side touch.

### Wave 1 — Title 1 end-to-end

```
.venv/bin/usufruct rs phase2                    # 54 TOCs, 45,494 IDs
.venv/bin/usufruct rs phase3 --titles 1         # 41 sections fetched
.venv/bin/usufruct rs phase4
```

Output (snapshot-ready in `data/rs/`):

| Artifact | Count / Detail |
| --- | --- |
| `sections/rs_1_*.json` | 41 active |
| `sections.jsonl` | 41 records, all round-trip Pydantic |
| `tree.json` | 65 roots (full corpus hierarchy), Title 1 has 2 chapters / 41 sections |
| `citation_edges.csv` | 8 edges from Title 1 (`R.S. 1:1 → R.S. 20:1`, `1:55 → CCP Art. 5059`, etc.) |
| `chunks.jsonl` | 41 RAG-ready chunks |
| `markdown/title-1/*.md` | 41 markdown files with YAML frontmatter |
| `manifest.json` | per-Title totals: 41 active / 0 repealed / 0 reserved / 0 blank |
| `validation_report.json` | 0 hierarchy gaps; 45,453 sections in `section_index.json` not emitted (all other Titles — expected for pilot run) |

### Plan-vs-truth quirks discovered in Title 1's data

- RS 1:11.1 ("Special census") and other ~1958-era sections use the
  period-separated acts form, NOT the semicolon form the plan implied.
  Normalizer handles it.
- legis serves multiple body templates: 14:30 uses `<div id="WPMainDoc">`
  inside the Document label, but RS 1:11.1 has paragraphs directly under
  `<span id="ctl00_PageBody_LabelDocument">` (no inner `<div>`). The
  parser already falls back to the outer `<span>` when `WPMainDoc` is
  absent, so no code change needed — flagging this so future debugging
  doesn't repeat the chase.
- Section numbering on Title 1 jumps from §1:18 → §1:50 (chapter
  boundary). Hierarchy placement is correct: §1:50 → Chapter 2.

### Tests

12 new tests across two files; full pytest is **122 passed (60 CC +
62 LRS), zero regressions**:

- `tests/test_lrs_legis_toc.py` (9 new) — synthetic-HTML coverage of the
  two TOC parsers + 4 fetcher scenarios (default walk, titles filter,
  Justia/legis gap detection, foreign-Title rejection).
- `tests/test_lrs_orchestrate.py` (+3) — normalizer behavior, with a
  before/after parse round-trip pinning the 1:11.1 case.

### Performance

- Phase 2: ~54 sec wall clock (54 fetches @ 1 req/s with one fetch
  pre-cached from earlier debugging).
- Phase 3 (Title 1, 41 sections): completed in seconds from cache after
  the initial scrape (also ~40 sec at 1 req/s when fresh).
- Phase 4: instant (pure transforms).

### What's next

The Wave 1 deliverable is in `data/rs/`. To pin it as a release:

```
.venv/bin/usufruct rs snapshot   # → snapshots/lrs-2026-05-21/
```

(Deliberately not run yet — wait for a user go-ahead before tagging.)

Wave 2 (Title 14) is the next planned step: ~711 sections × 1 req/s ≈
12 minutes Phase 3 wall time, plus pinned fixtures for the four-source-
of-truth checks listed in the plan.

## 2026-05-21 — Wave 2 (Title 14) end-to-end + two parser fixes

Three things landed in this session, in order: pinned Title 1 legis-page
fixtures, fixed the CC acts parser to recognize Louisiana extraordinary
sessions, then ran Wave 2 — which surfaced and fixed a third legis-side
template variant.

### 1. Pinned Title 1 legis-section fixtures

Two cached HTML files copied into `tests/fixtures/lrs/legis_sections/`:

- `rs-1-1.html` — baseline parse; "Revised Statutes; how cited" rule.
- `rs-1-11_1.html` — exercises both new Title-1-era structural facts:
  the no-`WPMainDoc` body template (paragraphs hang directly under the
  outer `<span id="ctl00_PageBody_LabelDocument">`), and the legacy
  period-separated acts form (`"Acts 1958, No. 498, §1. Amended by
  Acts 1970, No. 465, §1."`).

8 new tests in `test_lrs_legis_section.py` pin: outer-span fallback,
citation/heading/status round-trip, raw period-separated text shape, and
the end-to-end normalize→shared-CC-parser round trip yielding the
1958/1970 enactment+amendment pair. The no-`WPMainDoc` invariant is
asserted directly against the saved HTML — if legis ever rewraps these
pages, the fixture needs regeneration and the assertion catches it.

### 2. CC acts parser: extraordinary-session form (CC-side touch)

The shared `parse_acts_citation_line` in `src/usufruct/parse/acts_parser.py`
required `No.` to follow `\d{4},` directly, so 50+ unique
`Acts YYYY, Nth Ex. Sess., No. N` entries in the legis cache (Title 14, 22,
47 etc. — all absent from the Civil Code) were silently dropped.

Surgical fix: inserted an optional non-capturing group between year and
`No.`. The regex now accepts `1st`/`2nd`/`3rd` ordinals plus the 1960-era
no-space `Ex.Sess.` variant. The session designation itself is *not*
stored as a structured field on `ActsCitation` — preserving it would have
required a schema change (and `extra="forbid"` makes that load-bearing
for CC consumers). Verbatim text in `acts_citations_raw` carries the
session info. Documented in a comment.

| Test count | Before | After |
| --- | ---: | ---: |
| `test_acts_parser.py` | 10 | 16 (+6 new: special-session + ordinal variants) |
| RS 14:30 parsed acts | 28 | 29 (the 2002 1st Ex. Sess., No. 128 entry) |

CC test suite: 60 → 60, zero regressions. The CC corpus has zero
"`Ex. Sess.`" strings, verified via grep — the regex change is a no-op
for CC and a behavioral fix for LRS.

### 3. Wave 2 — Title 14 Phase 3 + Phase 4 end-to-end

```
.venv/bin/usufruct rs phase3 --titles 1,14
   # 711 fresh fetches at 1 req/s → ~12 min wall clock
.venv/bin/usufruct rs phase4
```

Used `--titles 1,14` (not just `--titles 14`) because `run_phase3`
unconditionally wipes `data/rs/sections/*.json` before re-emitting; a
`--titles 14`-only run would have deleted the Wave 1 Title 1 outputs.
Filed as a follow-up: the cleanup pass should be scoped to the requested
Titles.

### 4. Discovery during Wave 2: "Added by Acts" enactment form

Spot-checking Title 14 outputs surfaced **136 of 655 active sections
(21%) with `acts_citations` empty**. 81 of those had an acts line bleeding
into the body text — legis serves a *third* enactment template the LRS
parser didn't recognize:

```
Added by Acts 1973, No. 111, §1. Amended by Acts 1975, No. 380, §1; …
```

Used for sections inserted after the 1950 codification (e.g., R.S. 14:30.1
"Second degree murder", added 1973). Two fixes, both **LRS-side only** —
no further CC touch needed:

- `src/usufruct/lrs/parse/legis_section_parser.py` — `_ACTS_LINE_RE`
  extended to recognize `(?:Amended|Added)\s+by\s+` as the optional
  prefix, so the body/acts split classifies "Added by Acts …" paragraphs
  as acts.
- `src/usufruct/lrs/pipeline/orchestrate.py` — `_normalize_lrs_acts_text`
  gained a third pass that strips leading `Added by ` before delegating
  to the shared CC parser (the CC parser's `_LEADING_NOISE` only knows
  `Amended by ` / `Acquired from `, so the first piece would otherwise
  fail the act regex and the enactment entry would be silently dropped).

3 new tests: a focused normalizer assertion and two synthetic-HTML
integration tests against the rs-14-30-1 shape. Pinned an `rs-14-30-1`
fixture file was *not* added to `tests/fixtures/lrs/legis_sections/` —
the synthetic HTML covers the structural facts without growing the
fixture set, respecting the user's "Title 14 only" Wave 2 scope.

After re-running Phase 3 (cached HTML, ~3 sec) and Phase 4:

| Bucket | Before fix | After fix |
| --- | ---: | ---: |
| Title 14 active sections with `acts_citations = []` | 136 | 47 |
| Title 14 sections with acts bleeding into body | 81 | 0 |
| RS 14:30.1 parsed acts | 0 | 17 |

The remaining 47 acts-less active sections split:
- **46 genuinely have no acts line** on legis — usually rule-statement
  sections (e.g., R.S. 14:1 is the citation-form rule; R.S. 14:10 is the
  general-intent definition). These are data, not bugs.
- **1 section (R.S. 14:102.29) uses singular `Act 2021, No. 100`** instead
  of plural `Acts`. Flagged as a follow-up — 0.14% of Title 14, not worth
  a parser touch on its own.

### Wave 2 final tallies

`data/rs/` after Wave 2:

| Artifact | Count |
| --- | ---: |
| `sections/rs_{1,14}_*.json` | 752 (41 Title 1 + 711 Title 14) |
| `sections.jsonl` | 752, all round-trip Pydantic |
| `manifest.json` totals | active=696, repealed=56, blank=0, reserved=0 |
| `tree.json` | full corpus hierarchy preserved; max depth 7 |
| `citation_edges.csv` | 808 edges (766 intra-LRS + 39 → CrP + 3 → CCP) |
| `chunks.jsonl` | 688 RAG-ready chunks (excludes repealed) |
| `markdown/title-{1,14}/*.md` | 752 markdown files |
| `validation_report.json` | 0 hierarchy gaps, 0 synthetic blanks, 0 synthetic repealed |

Title 14 repealed count is **56** (Justia-flagged), not "~25" as the plan
estimated. This is the corpus truth and matches the Phase 1 walk from
the prior session.

39 LRS→CrP edges (e.g., R.S. 14:30 → Code of Criminal Procedure
Article 782) is the first cross-corpus evidence that the citation
extractor functions on a real Title. No LRS→CC edges yet because Title 14
is criminal law; that's a Wave 3 (Title 9) target.

### Tests

`.venv/bin/pytest` → **139 passed** (66 CC + 73 LRS), zero regressions.

- `test_acts_parser.py`: 10 → 16 (+6: special-session + variants).
- `test_lrs_legis_section.py`: 9 → 19 (+10: Title 1 pins + Added-by-Acts).
- `test_lrs_orchestrate.py`: 10 → 11 (+1: Added-by normalizer).

### Known gaps / follow-ups (none release-blocking)

- The `run_phase3` cleanup pass deletes `data/rs/sections/*.json` for all
  Titles even when `--titles` scopes the run. Workaround used here: pass
  all built Titles to `--titles`. Cheap fix is to scope the cleanup glob
  to the requested Title set.
- R.S. 14:102.29 — singular "Act YYYY," parses to acts=0. Single instance
  in Title 14; defer to a later parser pass.
- The "single Act with multiple effective dates" form
  (`"Acts 2010, No. 845, §2, eff. June 30, 2010, and §3, eff. Jan. 1, 2012."`)
  in Title 1 RS 1:60 still parses to a partial acts entry. Known
  shared-parser limitation; flagged in Wave 1 notes, still pending.

### What's next

Wave 2 deliverable is in `data/rs/`. Snapshot remains deferred per the
user's earlier decision (no tagging until go-ahead). Wave 3 (Title 9 —
Civil Code Ancillaries, 2,325 sections) is the next planned wave; it's
also the wave that tests the cross-corpus citation flow for real (R.S.
9:2800 is the marquee section and the existing CODE BOOK/CODE TITLE
machinery from the Title 9/15 fix is the structural backbone).

## 2026-05-21 — Hardening pass before Wave 3

Two follow-ups from the Wave 2 entry above, landed before kicking off
Wave 3.

### Fix: `run_phase3` cleanup is now scoped to the requested Titles

Before: `paths.sections_dir.glob("*.json")` deleted every section file
regardless of which Titles were in scope. A `--titles 9` run after
Waves 1+2 would have wiped 752 records. Worked around in Wave 2 by
passing `--titles 1,14`, but the workaround gets uglier with each wave
(`--titles 1,14,9,22,47` by Wave 4) and one forgotten flag silently
trashes hours of fetch work.

After: the cleanup glob filters to `parts[1] in titles_set` (filenames
are `rs_{title}_{section}.json`). When `titles_set is None` (full-corpus
run) the sweep is unchanged. Post-cleanup, the orchestrator reloads the
union of all on-disk sections so `sections.jsonl`, the manifest, and the
validation report reflect the full state — that's what makes
wave-by-wave building work cleanly. `run_phase3` now returns the union
instead of just the in-scope dict, so Phase 4 emits union artifacts too.

Regression test `test_phase3_scoped_run_preserves_out_of_scope_section_files`
seeds an out-of-scope `rs_99_1.json` sentinel, runs Phase 3 with
`titles=["14"]`, and asserts the sentinel survives and flows through to
the union JSONL + manifest.

### Pin: rs-14-30.1 as a fixture-backed regression test

The two synthetic-HTML "Added by Acts" tests from the Wave 2 entry are
replaced by three fixture-backed tests against the real legis HTML for
R.S. 14:30.1 (the canonical witness — added in 1973, 17 acts spanning
1973→2025). Copied the cached `d=78398` page into
`tests/fixtures/lrs/legis_sections/rs-14-30_1.html`.

The fixture-backed pins are stronger than the synthetic ones because
they catch any future legis HTML rewrap (a new wrapper div, a CSS class
rename) that would silently break the parser — the synthetic HTML
froze a known-good shape but couldn't have caught upstream drift.

### Tally

`.venv/bin/pytest` → **141 passed** (66 CC + 75 LRS), zero regressions.

- `test_lrs_orchestrate.py`: 11 → 12 (+1 scoped-cleanup regression test).
- `test_lrs_legis_section.py`: 19 → 20 (+3 fixture-backed rs-14-30.1
  tests, −2 synthetic-HTML tests they replaced).
- New fixture file: `tests/fixtures/lrs/legis_sections/rs-14-30_1.html`.

### What's next

Wave 3 is now unblocked. The cleanup-scope fix lets us run
`usufruct rs phase3 --titles 9` without losing Wave 1+2 data, and the
union-rebuild ensures the post-Wave-3 manifest shows Titles 1+14+9
together. Estimated wall clock: 2,325 sections × 1 req/s ≈ 39 min.

## 2026-05-21 — Wave 3 (Title 9) end-to-end + hierarchy-lookup fix

Wave 3 fetched and emitted Title 9 — the Civil Code Ancillaries, the
cross-corpus integration wave. R.S. 9:2800 ↔ CC 2315/2317 is the marquee
case and `citation_edges.csv` now shows real LRS→CC traffic for the
first time. The wave also surfaced a Phase-3 hierarchy-lookup bug that
mangled the breadcrumb on 93.7% of Title 9 sections; fixed in this same
session before claiming the wave done.

### 1. Phase 3 fetch — 2,325 Title 9 sections in ~42 min

```
.venv/bin/usufruct rs phase3 --titles 1,14,9
   # 2,325 fresh fetches at ~0.58 req/s → 42 min wall clock
.venv/bin/usufruct rs phase4
```

The `1,14,9` belt-and-suspenders titles list is no longer required (the
[hardening pass](#2026-05-21--hardening-pass-before-wave-3) scoped the
cleanup glob), but kept for symmetry until Wave 4 picks a different
convention. Observed rate (~0.58 req/s) is slightly under the planned
1 req/s — legis response latency, not a throttle. No fetch errors.

### 2. Discovery during spot-check: incoherent hierarchy_path on Title 9

Eyeballing R.S. 9:2800 surfaced an immediate structural problem:

```
breadcrumb: Title 9 › Code Preliminary Title › Code Book I › Code Title IV ›
            Chapter 1 › Part III › Subpart A
```

`Code Title IV: PREDIAL SERVITUDES` actually lives under `Code Book II`,
not `Code Book I` (which is `OF PERSONS`). The path is structurally
impossible — it mixes parts from different real chains. A grep across
Title 9 quantified the spread:

| Bucket | Count | % |
| --- | ---: | ---: |
| Title 9 active+repealed sections | 2,325 | 100% |
| Incoherent (code_book, code_title) pair | 2,178 | **93.7%** |
| Coherent | 147 | 6.3% |

Title 1 and Title 14 had zero incoherent paths — they don't use
`CODE BOOK` or `CODE TITLE` structure (flat hierarchies). The bug was
exclusively Title-9-shaped.

### 3. Root cause — `LRSHierarchyIndex.lookup` can't disambiguate siblings

`src/usufruct/lrs/pipeline/hierarchy.py` builds an interval index keyed
by `(title, level, number)`. But Title 9 has **multiple containers
sharing (level, number) under different parents** — e.g., `code_title V`
appears under code_books I, II, III, and IV (with completely different
names: NATURAL JURIDICAL PERSONS, OWNERSHIP, QUASI CONTRACTS AND
OFFENSES, etc.). `by_key` collapses these to one entry (the last seen),
so `resolve_chain(c)` walks `c.parent_chain` and pulls back the wrong
ancestor at every collision.

`assign_ranges_from_sections` has a similar key collision on
`(title, level, number, name)`: multiple `Part I: IN GENERAL`
containers exist under different chapters. Their section_ranges merge
into a single union (e.g., `[51-5504]` for a "Part I" that should only
cover a small chapter), making range-based lookup pick the wrong leaf.

The earlier `2026-05-21 — Discovered + fixed Title 9/15 CC-structure
parsing` session correctly added the `code_book` / `code_preliminary_title`
/ `code_title` enum values and made the **Phase 1 walker** produce
coherent chains. That ground truth is stored as
`JustiaSectionEntry.container_chain` — a list of `(level, number, name)`
triples in document order. The runtime lookup was never reconciled with
the walker fix.

### 4. The fix — bypass the lookup, use Phase 1 ground truth

`src/usufruct/lrs/pipeline/orchestrate.py`:

- New helper `_hierarchy_path_from_justia_chain(chain) -> List[HierarchyNode]`
  that converts the Phase 1 triples directly into a `HierarchyNode`
  list. No re-derivation, no ambiguity.
- `run_phase3` uses the helper for every section where the Justia
  entry has a non-empty `container_chain` (effectively all of them); the
  `hierarchy_index.lookup` call remains as a defensive fallback.
- Same change at the backfill site (line 525) for synthesized
  blank/repealed records that aren't in `legis_section_ids`.

No edits to `hierarchy.py`. The `LRSHierarchyIndex` is still built and
remains useful for callers that don't have a `JustiaSectionEntry` in
scope; its lookup just isn't on the Phase 3 path anymore.

Two new tests in `test_lrs_orchestrate.py`:

- `test_hierarchy_path_from_justia_chain_preserves_coherent_chain` — a
  unit test asserting the helper round-trips the real R.S. 9:2800 chain
  (code_book III + code_title V) without mangling.
- `test_phase3_uses_justia_chain_for_title9_marquee_section` — an
  end-to-end pin against the Title 9 Justia fixture: Phase 1 walks
  Title 9, Phase 3 runs with an empty `section_index` so R.S. 9:2800
  goes through the backfill code path, and the emitted RSSection's
  `hierarchy_path` must equal the Phase 1 `container_chain` exactly.

After re-running Phase 3 (cached HTML, ~17 sec) and Phase 4:

| Bucket | Before fix | After fix |
| --- | ---: | ---: |
| Title 9 sections with incoherent code_book/code_title pair | 2,178 | **0** |
| R.S. 9:2800 breadcrumb | `Title 9 › ... › Code Book I › Code Title IV: PREDIAL SERVITUDES › ...` | `Title 9 › ... › Code Book III › Code Title V: QUASI CONTRACTS, OFFENSES › Chapter 2` |
| `validation_report.sections_without_hierarchy` | 0 | 0 |
| `citation_edges.csv` total | 2,309 | 2,309 (unchanged — fix doesn't touch citation extraction) |

The fix is a one-line replacement at each call site plus a 12-line
helper — small surface, no behavior change for Titles 1 & 14 (their
`container_chain` was already coherent because they don't have the
sibling-collision shape).

### 5. Wave 3 final tallies

`data/rs/` after Wave 3 + fix:

| Artifact | Wave 2 | Wave 3 | Δ |
| --- | ---: | ---: | ---: |
| `sections/rs_{1,14,9}_*.json` | 752 | **3,077** | +2,325 |
| `sections.jsonl` | 752 | 3,077 | +2,325 |
| `manifest.json` active | 696 | 2,718 | +2,022 |
| `manifest.json` repealed | 56 | 354 | +298 |
| `manifest.json` blank | 0 | 5 | +5 |
| `tree.json` max_depth | 7 | 7 | 0 |
| `citation_edges.csv` total | 808 | **2,309** | +1,501 |
| `chunks.jsonl` | 688 | 2,659 | +1,971 |
| `markdown/title-*/*.md` | 752 | 3,077 | +2,325 |
| `validation_report` hierarchy gaps | 0 | 0 | — |

By-Title section counts: Title 1 = 41 (unchanged), Title 14 = 711
(unchanged), Title 9 = 2,325 (new).

### 6. Edges by destination corpus — the cross-corpus payoff

| Destination | Wave 2 | Wave 3 | Δ |
| --- | ---: | ---: | ---: |
| `rs` (intra-LRS) | 766 | 2,098 | +1,332 |
| `civcode` (LRS → Civil Code) | 0 | **129** | +129 |
| `ccp` (LRS → Code of Civil Procedure) | 3 | 40 | +37 |
| `crp` (LRS → Code of Criminal Procedure) | 39 | 39 | 0 |
| `evidence` (LRS → Code of Evidence) | 0 | 3 | +3 |
| **Total** | **808** | **2,309** | **+1,501** |

**The 129 LRS→Civil Code edges is the marquee Wave 3 outcome** — the
first cross-corpus integration that was the whole point of building
Title 9 (Civil Code Ancillaries) before the other Titles. R.S. 9:2800
contributes two of those edges, both citing CC Article 2317
("Things in one's custody")  in the body of "Limitation of liability
for public bodies." +3 LRS→evidence is a nice side effect — Title 9
references the Code of Evidence in a few places that earlier Titles
didn't.

### 7. Title 9 acts-citation health

`acts_citations` were parsed for 1,633 of 2,022 active Title 9 sections
(80.8%). The 389 sections without parsed acts:

| Bucket | Count | % of active |
| --- | ---: | ---: |
| Genuinely no acts text on legis | 363 | 18.0% |
| Has raw text but parser missed it | **26** | 1.3% |

The 26 parser misses cluster around three known shared-parser
limitations (none specific to Title 9, none release-blocking):

- **Multi-section single Act** (`§§1, 2`, `§§1-3`) — same family as
  the Wave 1 R.S. 1:60 limitation. ~15 sections.
- **Embedded `{{NOTE: ...}}` block** that bleeds across the act boundary
  (e.g., R.S. 9:1253, 9:2372, 9:2373). ~5 sections.
- **Trailing footnote markers** (`1 As appears in enrolled act`,
  `1 26 U.S.C.A. §7425`) confusing the regex tail. ~6 sections.

No new template variants in the body — the [Wave 2 three template
classes](#2026-05-21--wave-2-title-14-end-to-end--two-parser-fixes)
(modern semicolon, period-separated legacy, `Added by Acts`
enactment) cover Title 9 too. Plus the structural fix above means
hierarchy_path is right for all 2,022 active sections.

### Tests

`.venv/bin/pytest` → **143 passed** (66 CC + 77 LRS), zero regressions.

- `test_lrs_orchestrate.py`: 12 → 14 (+2: helper unit test +
  end-to-end Title 9 marquee pin).
- All other test files unchanged.

### Known gaps / follow-ups (none release-blocking)

- 26 Title 9 active sections have raw acts text the shared CC parser
  can't split — multi-section single-Act, embedded NOTE blocks, and
  footnote-marker tails. ~1.3% of Title 9 active. Fixable with parser
  work but not on Wave 3's critical path.
- The underlying `LRSHierarchyIndex.lookup` is still buggy for any
  caller without a `JustiaSectionEntry` in scope. The Phase 3 path
  doesn't trigger it post-fix, but the index itself remains
  ambiguous. A proper fix would key both `by_key` and
  `sections_under` on the full parent path (parent_chain (lvl, num)
  tuples + own (level, number)). Filed as a follow-up; we deliberately
  shipped the surgical fix to stay within the wave's scope.
- `assign_ranges_from_sections` produces buggy `section_range_*` values
  for sibling-colliding containers (e.g., the various "Part I IN
  GENERAL" containers across Title 9 all share the union range
  [51-5504]). Same underlying cause as above; same follow-up.

### What's next

Wave 3 deliverable is in `data/rs/` and the snapshot remains deferred
per the user's earlier decision (no tagging until go-ahead). Wave 4
candidate is Title 22 (Insurance, ~1,500 sections) or Title 47
(Taxation, ~2,500 sections) — both significantly larger; both
exercise different domain vocabularies and may surface new acts-line
patterns. Title 47 is also the largest non-Title-9 corpus and would
stress the per-section throughput further.

The hierarchy-index follow-up (proper fix in `hierarchy.py` keyed on
full parent path) is independent of Wave 4 and could land at any
point. Useful to do before any other consumer of `LRSHierarchyIndex`
ships (currently none in the codebase).

## 2026-05-21 — Snapshot of Waves 1–3 (`lrs-2026-05-21`)

User called for a snapshot of the Wave 1+2+3 deliverable before kicking
off Wave 4. Per the [LRS plan §6](lars-test/LRS_IMPLEMENTATION_PLAN.md),
`usufruct rs snapshot` archives `data/rs/` into
`snapshots/lrs-YYYY-MM-DD/`. No code changes; pure copy operation.

```
.venv/bin/usufruct rs snapshot
# → snapshots/lrs-2026-05-21/
```

### What landed in the snapshot

`snapshots/lrs-2026-05-21/` (73M, ~14 entries):

| Artifact | Size |
| --- | ---: |
| `hierarchy.json` | 1.9M |
| `justia_section_index.json` | 22M (largest — Phase 1 ground truth) |
| `section_index.json` | 5.5M (Phase 2 join output) |
| `sections/` | 3,077 per-section JSON files |
| `sections.jsonl` | 7.8M, 3,077 lines |
| `notes.jsonl` | 86K (Justia NOTE: paragraphs) |
| `tree.json` | 2.2M |
| `citation_edges.csv` | 184K, 2,309 edges |
| `chunks.jsonl` | 4.7M, 2,659 chunks |
| `markdown/title-{1,9,14}/*.md` | 3,077 files |
| `manifest.json` | as-emitted by Phase 4 |
| `validation_report.json` | 632K, 0 hierarchy gaps |

Two Phase-2 intermediates are intentionally excluded from the whitelist
in `lrs.pipeline.orchestrate.snapshot()`: `folder_map.json` (the legis
folder ID → Title mapping, regenerable from the cached root TOC) and
`phase2_gaps.json` (Justia/legis join diagnostics). Matches the CC
snapshot convention — only the deliverable corpus ships, not phase-side
intermediates.

### Verification

- 143 tests pass (66 CC + 77 LRS), zero regressions. (Snapshot is a copy
  op — no source change — but confirmed baseline per `CLAUDE.md`.)
- `sections.jsonl` line count: 3,077 live = 3,077 snapshot.
- `sections/` file count: 3,077 live = 3,077 snapshot.
- `markdown/**.md` count: 3,077 live = 3,077 snapshot.
- Manifest totals in the snapshot match the Wave 3 final tallies
  (active=2,718, repealed=354, blank=5; total_citation_edges=2,309).

### What's deferred

- **No zip / no SHA sidecar.** The CC release pattern is
  `snapshots/YYYY-MM-DD/` directory + `usufruct-YYYY-MM-DD.zip` +
  `.sha256` sidecar. Only the directory exists for LRS so far; zipping
  is a manual release-prep step, not part of `rs snapshot`.
- **No GitHub Release tag.** Per per-wave Definition of Done §14, a
  Release is "drafted at user's discretion." Three waves bundled into
  one tag (`lrs-pilot-waves-1-3` or `lrs-2026-05-21`?) is a release-naming
  question for the user.
- **`scripts/verify_release.sh` is CC-only.** Per LRS plan §16 open
  question 5, the verify script expects `articles.jsonl`,
  `article_index.json`, etc.; it would fail against the LRS snapshot
  contents. Adding an LRS variant or a corpus flag is deferred to
  release prep, not gating this snapshot.

### What's next

Wave 4 candidate selection is the next decision: Title 22 (Insurance,
~1,500 sections) or Title 47 (Taxation, ~2,500 sections + Subtitles).
The proper `LRSHierarchyIndex.lookup` fix in `hierarchy.py` and the
CC `acts_parser.py` extension to clear the ~26 Title 9 misses are both
independent follow-ups that can land in any order relative to Wave 4.

## 2026-05-21 — Proper `LRSHierarchyIndex` fix (re-key on full parent path)

Wave 3's [hierarchy-lookup section](#2026-05-21--wave-3-title-9-end-to-end--hierarchy-lookup-fix)
shipped a *surgical* fix: Phase 3 bypassed `LRSHierarchyIndex.lookup`
via `_hierarchy_path_from_justia_chain`, which uses Phase 1's coherent
`JustiaSectionEntry.container_chain` directly. The index itself remained
buggy — Wave 3 just stopped calling its broken path. This session lands
the *proper* fix: `LRSHierarchyIndex` is now itself correct, and
`assign_ranges_from_sections` no longer collapses sibling containers
that share `(level, number, name)` into one union range.

### 1. Root cause recap

`src/usufruct/lrs/pipeline/hierarchy.py` had two symptoms of the same
bug — keys that didn't disambiguate the parent path:

| Function | Old key | What collapsed |
| --- | --- | --- |
| `build_lrs_hierarchy_index.by_key` | `(title, level, number)` | Same `(level, number)` under different parents (e.g., `code_title V` under code_books I, II, III, IV). |
| `assign_ranges_from_sections.sections_under` | `(title, level, number, name)` | Same `(level, number, name)` under different parents (e.g., `Part I: IN GENERAL` under 14 different chapters in Title 9). |

The `by_key` collapse fed `resolve_chain` and produced incoherent
ancestor chains (Wave 3 fixed the *Phase 3 consumer* of this). The
`sections_under` collapse merged section buckets and gave every
`Part I: IN GENERAL` a union `section_range = [51, 5504]` covering
essentially the entire Title — wrong by an order of magnitude per
container.

### 2. The fix — full-path keying

Both dicts re-keyed on the ordered tuple of `(level, number)` steps from
root to (and including) the container itself.

```python
PathKey = Tuple[Tuple[str, str], ...]   # ((level, number), ...) root-first
by_path:        Dict[Tuple[str, PathKey], Container]
sections_under: Dict[Tuple[str, PathKey], List[(SectionKey, str)]]
```

- `build_lrs_hierarchy_index`: each container indexed by
  `(title, parent_chain + ((own_level, own_number),))`. `resolve_chain`
  walks `parent_chain` with a growing prefix and looks up each prefix
  in `by_path` — every ancestor is uniquely identified by its full
  path from root.
- `assign_ranges_from_sections`: each section walks its
  `container_chain` with a growing prefix and stamps the section onto
  every prefix bucket; each container then reads its own
  `(title, full_path)` bucket. Names are no longer part of the key (the
  path already disambiguates) but agree by construction.

No edits to `orchestrate.py`, `paths.py`, or any other LRS module. The
defensive `_hierarchy_path_from_justia_chain` bypass in `run_phase3`
**stays** as belt-and-suspenders — it's a hair faster than the interval
walk and the Phase 1 chain is the authoritative ground truth anyway.
Trivial to remove later if preferred; the index is now correct on its
own.

### 3. Tests — three new pure-unit + two real-fixture pins

`tests/test_lrs_hierarchy.py` (new file, three unit tests built from
synthetic Containers — no fixtures, no `_FakeClient`, no I/O):

- `test_assign_ranges_disambiguates_same_name_siblings_under_different_parents`
  — two `Part I: IN GENERAL` under different chapters get disjoint
  ranges, not the union.
- `test_index_lookup_returns_coherent_chain_for_same_name_siblings`
  — lookup picks the correct sibling by chapter, not whichever happened
  to be inserted last.
- `test_resolve_chain_walks_full_path_not_collapsed_level_number_pairs`
  — the Title-9 shape: two `code_title V` containers under different
  code_books resolve their own chains coherently. Pins the deeper
  invariant the Phase 3 bypass test (`_hierarchy_path_from_justia_chain`)
  couldn't reach.

`tests/test_lrs_orchestrate.py` (two new real-fixture tests):

- `test_phase1_title9_part_i_in_general_containers_have_narrow_disjoint_ranges`
  — runs Phase 1 on the real Title 9 fixture; asserts at least 3 Part I
  `IN GENERAL` siblings exist, that *none* carry the pre-fix bug
  signature `("51", "5504")`, and that all have distinct ranges. The
  handoff brief's specific pin.
- `test_hierarchy_index_lookup_now_returns_coherent_chain_for_title9_marquee`
  — the complement to the existing Phase-3 bypass test: builds the
  index from the real Phase 1 walk and asserts `index.lookup("9", "2800")`
  matches the Phase 1 ground-truth chain. Confirms `.lookup` is itself
  correct, not just bypassed.

### 4. Live-data verification — strict improvement, no per-section drift

Regenerated `data/rs/` end-to-end from the cached HTML
(`phase1` → `phase3 --titles 1,9,14` → `phase4`, ~17s + ~5s) and
SHA-diffed every artifact against `snapshots/lrs-2026-05-21/`:

| Artifact | Result |
| --- | --- |
| `hierarchy.json` | **CHANGED** — sole purpose of the fix; ranges fixed. |
| `tree.json` | **CHANGED** — consumes container ranges. |
| `manifest.json` | bit-identical excluding `generated_at`. |
| `sections.jsonl` (3,077 records) | bit-identical excluding per-record `scrape_timestamp`. |
| `sections/*.json` (3,077 files) | bit-identical excluding per-record `scrape_timestamp`. |
| `markdown/*.md` (3,077 files) | bit-identical (markdown has no timestamp field). |
| `citation_edges.csv` | bit-identical. |
| `chunks.jsonl` | bit-identical. |
| `validation_report.json` | bit-identical. |
| `justia_section_index.json` | bit-identical. |
| `section_index.json` | bit-identical. |
| `notes.jsonl` | bit-identical. |

Per-section content is unchanged because Phase 3 already bypassed the
broken lookup via `_hierarchy_path_from_justia_chain` — every section's
`hierarchy_path` continues to flow from the Phase 1 ground truth, not
the index. The fix's user-visible effect is confined to
`hierarchy.json` and `tree.json`, both of which now report each
container's *actual* section range instead of the collapsed union.

### 5. Title 9 Part I — before and after

| | Pre-fix | Post-fix |
| --- | ---: | ---: |
| `Part I` containers in Title 9 | 37 | 37 |
| of which named `IN GENERAL` | 14 | 14 |
| with bug signature `[51, 5504]` | 13 | **0** |
| distinct ranges among the 14 IN GENERAL siblings | 1 | **14** |

Sample of the post-fix distinct narrow ranges for the IN GENERAL siblings:
`(51, 51)`, `(291, 292)`, `(301, 314)`, `(901, 901)`, `(1101, 1114)`,
`(1451, 1521)`, `(1701, 1702)`, `(3001, 3003)`, `(3051, 3051)`,
`(3301, 3308)`, `(3501, 3509.4)`, `(3590, 3590)`, `(3901, 3904)`,
`(5501, 5504)`. Each Part I now covers only its actual chapter's
sections — `(51, 5504)` was simply `min(starts)..max(ends)` of all 14.

### Tests

`.venv/bin/pytest` → **148 passed** (66 CC + 82 LRS), zero regressions.

- New file `tests/test_lrs_hierarchy.py`: 3 unit tests.
- `tests/test_lrs_orchestrate.py`: 14 → 16 (+2 real-fixture pins).
- All other test files unchanged.

### Snapshot status

The snapshot at `snapshots/lrs-2026-05-21/` predates this fix and
therefore captures pre-fix `hierarchy.json` + `tree.json` (with the
[51, 5504] union ranges) alongside post-fix per-section data (which
was already correct via the Wave 3 bypass). If you want the snapshot
to reflect post-fix `hierarchy.json` + `tree.json` too, re-snapshot
with `.venv/bin/usufruct rs snapshot` — that overwrites the same
`lrs-2026-05-21/` directory (today is 2026-05-21). Otherwise the
snapshot remains a faithful capture of the bypass-only Wave 3 state.

### What's next

The CC `acts_parser.py` extension (multi-section `§§N, M`, embedded
`{{NOTE: ...}}`, footnote-marker tails — ~20 of 26 Title 9 misses) and
Wave 4 (Title 22 or 47) are the two open options. The hierarchy work
is now complete: `LRSHierarchyIndex` is structurally correct on its
own, the Phase 3 bypass remains as belt-and-suspenders, and no
follow-up `hierarchy.py` work is needed before any subsequent wave.

## 2026-05-21 — CC `acts_parser.py` extension (second authorized CC touch)

User-authorized extension to `src/usufruct/parse/acts_parser.py` to
clear the 36 active sections across Titles 1+9+14 whose acts text the
shared CC parser couldn't split. Per the strict-isolation rule this is
the *second* allowed touch of the CC tree (the first was the
special-session regex from the earlier session); any further CC edit
needs new escalation. User decisions on the two design questions, taken
2026-05-21:

- **Multi-section emission** (`§§N, M`, `§§N-M`, `§§N to M`): emit one
  `ActsCitation` record per section, all sharing `act_year`,
  `act_number`, effective date, and role. No schema change.
- **Scope**: all 36 sections, including the 10 adjacent patterns
  outside the original "26 Title 9 misses" handoff list (Title 1's
  ``No 128`` typo, Title 14's 2024-era ``, See Act.`` form).

### 1. Patterns added (eleven, in two passes)

**First pass — six patterns from the original audit:**

| Pattern | Example | Witness |
| --- | --- | --- |
| Multi-section comma-list | `Acts 1999, No. 1315, §§1, 2, eff. Jan. 1, 2000.` | R.S. 9:3578.5 |
| Multi-section hyphen range | `Acts 1968, No. 154, §§1-3.` | R.S. 9:2725 |
| Multi-section `N to M` range | `Acts 1972, No. 451, §§1 to 3.` | R.S. 14:331 |
| Embedded `{{NOTE: ...}}` block | `Acts 1986, No. 225, §3. {{NOTE: SEE ACTS ...}}` | R.S. 9:2802 |
| Trailing `. NOTE: See Acts ...` | `Acts 2019, No. 325, §1. NOTE: See Acts 2019, No. 325, §§6, 7, and 10, regarding applicability.` | R.S. 9:4843 |
| Trailing `, See Act.` | `Acts 2024, No. 670, §1, See Act.` | R.S. 14:112.11–13 |
| Footnote-marker tail | `Acts 1999, No. 517, §1. 1 As appears in enrolled bill.` | R.S. 9:2945 |
| `No 128` (missing period) | `Acts 2021, No 128, §1.` | R.S. 1:55.1 |

**Second pass — five residual patterns** that surfaced as the post-first-pass
unparsed remainder:

| Pattern | Example | Witness |
| --- | --- | --- |
| Split ordinal `1 st Ex. Sess.` | `Acts 2011, 1 st Ex. Sess., No. 30, §1.` | R.S. 9:2921 |
| Missing comma before `eff.` | `Acts 1974, No. 546, §1 eff. Jan. 1, 1975.` | R.S. 9:5131 |
| Trailing `. *NOTE:` (leading asterisk) | `Acts 1985, No. 728, §1. *NOTE: AS APPEARS IN ENROLLED BILL.` | R.S. 9:5644 |
| Trailing `. *Note ...` (no colon) | `Acts 1984, No. 331, §4. *Note error in English translation ...` | R.S. 9:2785 |
| Trailing `. * See ...` (cross-ref) | `Acts 1950, No. 495, §1. * See R.S. 9:603, ...` | R.S. 9:675 |

Implementation: three new compiled regexes (`_BRACED_NOTE_RE`,
`_TRAILING_NOTE_RE`, `_TRAILING_STAR_SEE_RE`,
`_TRAILING_SEE_ACT_RE`, `_FOOTNOTE_TAIL_RE`, `_ACT_MULTI_RE`), three
tolerances in the existing `_ACT_RE` (`No\.?`, `\d+\s*(?:st|nd|rd|th)`,
`,?\s+eff\.`), and one new helper `_expand_section_spec` to turn a
spec like `"1, 2"` / `"1-3"` / `"1 to 3"` into a list of integers.
Whole-line preprocessing strips brace + trailing-NOTE + trailing-star-See
content; per-piece preprocessing strips `, See Act.` and footnote-marker
tails (with a defensive `rstrip(".")` after each per-piece sub since the
strip can leave the citation's own trailing period exposed).

### 2. Tests — 21 new in `test_acts_parser.py` (16 → 37)

First-pass (16): one parametrized table covering the five multi-section
shapes; effective-date-carries-through pin; braced-NOTE strip; trailing
unbraced NOTE; `, See Act.` strip (alone + combined with multi-section);
footnote-marker strip (alone + combined with multi-section); the
`No 128` missing-period case; direct unit test on `_expand_section_spec`
covering comma list / hyphen range / word-to range / "and" connector /
inverted range / empty input.

Second-pass (5): one test per residual pattern from the table above.

### 3. Live-data delta

Re-ran `phase3 --titles 1,9,14` + `phase4` from cache (~22s).

| | Pre-fix | Post-fix |
| --- | ---: | ---: |
| Title 1 raw-unparsed active | 1 | **0** |
| Title 9 raw-unparsed active | 26 | **0** |
| Title 14 raw-unparsed active | 9 | **0** |
| **Total raw-unparsed** | **36** | **0** |
| Title 1 total ActsCitation records | 100 | 105 |
| Title 9 total ActsCitation records | 3,100 | 3,271 |
| Title 14 total ActsCitation records | 1,987 | 2,119 |
| **Total ActsCitation records** | **5,187** | **5,495 (+308)** |

The +308 record delta breaks down as:

- **36 newly-parsed sections** (those with raw text but `acts_citations=[]`
  pre-fix) contribute **50 records** post-fix (more than 36 because
  multi-section forms expand into one record per section).
- **141 expanded-parse sections** (sections that parsed partially pre-fix
  because one of their semicolon-separated pieces had an unparseable
  form like `§§1, 2`) contribute **+258 additional records**. These were
  silent improvements — sections that already had *some* parsed acts
  but were missing entries the parser couldn't handle.
- **0 regressions** — no section emerged with fewer records than before.

### 4. Artifact diff vs `snapshots/lrs-2026-05-21/`

| Artifact | Change | Why |
| --- | --- | --- |
| `sections.jsonl` (3,077 records) | CHANGED | new acts_citations; per-record `scrape_timestamp` drift |
| `sections/*.json` (3,077 files) | CHANGED | same as above |
| `markdown/*.md` (177 of 3,077) | CHANGED | frontmatter exposes acts_citations |
| `manifest.json` | CHANGED | `generated_at`; total counts unchanged (still 3,077 emitted) |
| `hierarchy.json`, `tree.json` | CHANGED | unrelated — from the earlier `LRSHierarchyIndex` re-keying in this same session |
| `citation_edges.csv` | bit-identical | acts parsing doesn't affect intra-text citation extraction |
| `chunks.jsonl` | bit-identical | chunks exclude acts data |
| `validation_report.json` | bit-identical | no validation criteria depend on acts parsing |
| `justia_section_index.json`, `section_index.json`, `notes.jsonl` | bit-identical | Phase 1/2 outputs untouched |

The 177 changed markdown files = 36 newly-parsed + 141 expanded —
exactly the sections whose `acts_citations` shape changed.

### Tests

`.venv/bin/pytest` → **169 passed** (87 CC + 82 LRS), zero regressions.

- `tests/test_acts_parser.py`: 16 → 37 (+21).
- All other test files unchanged.

### CC-touch accounting

This is the *second* authorized edit to `src/usufruct/parse/acts_parser.py`:
- First touch (earlier session): extraordinary-session regex
  (`1st/2nd/3rd Ex. Sess.,` + 1960-era no-space `1st Ex.Sess.,`).
- Second touch (this session): eleven new patterns / tolerances as
  enumerated above.

No other CC file under `src/usufruct/{fetch,parse,pipeline,model}/`
was touched. Any further CC edit still needs explicit escalation per
the strict-isolation rule.

### What's next

Wave 4 (Title 22 Insurance, ~1,500 sections, or Title 47 Taxation,
~2,500 sections + Subtitles) is the remaining open item. The hierarchy
work and the acts-parser work are both complete; both `LRSHierarchyIndex`
and `parse_acts_citation_line` are now corpus-clean for Waves 1–3 and
ready for the next wave's data. The snapshot at
`snapshots/lrs-2026-05-21/` predates today's two fixes — re-snapshot
with `.venv/bin/usufruct rs snapshot` to refresh it (overwrites the
same dir since the date is unchanged), or leave it as a bypass-only
record of the pre-fix Wave 3 state.

## 2026-05-22 — Wave 4 (Title 22 Insurance) end-to-end

Fourth wave of LRS data landed. Title 22 (Insurance Code) walks Phases 3+4
clean against legis.la.gov on its own and joined with Titles 1+9+14. Zero
source-code edits this session — no new acts-line variant emerged and no
hierarchy fix was needed. The wave was purely a fetch + parse + verify
exercise against the existing parser/orchestrator surface.

### Scope

- **2,651 sections** in `data/rs/sections/rs_22_*.json` (handoff brief
  estimated ~1,500; the actual count was uncovered in pre-flight from
  `section_index.json`).
- **182 containers** (1 title root + 22 chapters + 55 parts + 104
  subparts; **no Subtitle level** — Title 22 is chapter/part/subpart
  only, so the Subtitle code path is still unexercised. Title 47 would
  be the first wave to exercise it).
- Container status: 2,518 active + 132 repealed + 1 blank.

### Process

1. Promoted `lars-test/html/title-22.html` →
   `tests/fixtures/lrs/justia/title-22.html` (no fixture change needed
   — phase1/phase2 already covered Title 22 in earlier corpus-wide
   runs; this just brings it under fixture-pin coverage).
2. `.venv/bin/python -m usufruct.cli rs phase3 --titles 1,9,14,22`
   (~44 min wall time at 1 req/s; Title 22 fully uncached at start,
   Titles 1/9/14 fully cached and short-circuited).
3. `.venv/bin/python -m usufruct.cli rs phase4` (~10s; rebuilds
   tree/edges/chunks/markdown for the full union).
4. Spot-checks + edge breakdown + acts-parse rate inspection.
5. `.venv/bin/pytest -q` → **169 passed, zero regressions.**

### Output deltas (Wave 4 effect on data/rs/)

| Metric | Pre-Wave 4 (W1–3) | Post-Wave 4 | Δ |
| --- | --- | --- | --- |
| Sections emitted | 3,077 | 5,728 | +2,651 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (unchanged; W4 containers were already in `hierarchy.json` from corpus-wide Phase 1) |
| ActsCitation records | 5,495 | 10,210 | +4,715 |
| Citation edges | 2,309 | 4,494 | +2,185 |
| RAG chunks | 2,659 | 4,411 | +1,752 |
| Markdown files | 3,077 | 5,728 | +2,651 |
| Pytest passing | 169 | 169 | 0 |

`validation_report.sections_without_hierarchy = 0` (clean).
`in_section_index_but_unemitted` is 39,766 — all from Titles outside
the {1,9,14,22} scope (expected).

### Title 22 citation-edge breakdown

2,185 edges originate from Title 22 sources (~0.82 edges per section,
about half the Title 9 rate of 1.5 — insurance is meaty but less
interleaved than family law). By destination corpus:

| Dst corpus | Count |
| --- | --- |
| `rs` (intra-LRS) | 2,162 |
| `civcode` | 8 |
| `crp` | 9 |
| `ccp` | 5 |
| `evidence` | 1 |

Top Title 22 intra-LRS destinations (top 8): T22 self=1,763,
T49=71 (state administration / agencies), T44=39 (public records),
T37=34 (professions/businesses), T40=33 (health/welfare),
T23=31 (labor / worker comp), T32=27 (motor vehicles —
auto-insurance touch), T51=24 (trade/commerce). Cross-corpus edge
volume (23 total: 8+9+5+1) is small but non-zero — confirms the
LRS→{civcode,ccp,crp,evidence} resolvers stay healthy under the
larger Title 22 input.

Wave 1–3 totals are unchanged (Title 1=8, Title 9=1,501, Title 14=800
source edges → 2,309 total). No drift from the rerun.

### Acts parsing: no new variant

Title 22 has **133 raw-unparsed sections**, but **132 of 133 are
repealed-status** — orchestrate.py:510 only parses
`acts_citations_raw` for `status == "active"`, so all repealed
sections retain their raw text without parsed records by design. This
is the *same* mechanism that left 354 raw-unparsed records in Titles
9+14 (298 + 56) in earlier waves. Corpus-wide the count is now
486 raw-unparsed repealed sections, which matches the manifest's
`by_status.repealed = 486` exactly.

The single Title 22 active-status unparsed case:

| Citation | Raw text | Why |
| --- | --- | --- |
| R.S. 22:1059.7 | `Acts 2025, No. 367, §1, eff. See Act.` | Effective-date slot contains the literal "See Act", which the shared parser's eff-date regex cannot capture. Known limit (same family as R.S. 1:60). Defer. |

**No new acts_parser pattern was needed.** The eleven patterns added
in the 2026-05-21 CC-touch session are sufficient for Title 22. The
CC-touch budget remains spent (two touches: special-session regex,
then the eleven-pattern extension). Any further `src/usufruct/parse/`
edit still needs explicit escalation.

### Marquee spot-checks

| Citation | Heading | Hierarchy depth | Body | Acts |
| --- | --- | --- | --- | --- |
| R.S. 22:1 | Louisiana Insurance Code | 3 (title/ch/part) | 77 chars | 3 records (1958/1993/2003) |
| R.S. 22:2 | Insurance regulated in the public interest | 4 (title/ch/part/subpart) | 3,347 chars | 18 records (heavy "Amended by Acts" history) |
| R.S. 22:11 | Rules and regulations by commissioner | 4 | 7,808 chars | 6 records (handles `Redesignated from R.S. 22:3 by Acts ...`) |
| R.S. 22:1011 | Employer-provided health plan; limitation to specific pharmacies prohibited | 4 | 936 chars | 1 record (trailing `NOTE: Former R.S. 22:...` stripped cleanly) |
| R.S. 22:31 | Division of diversity and opportunity | 4 | 3,177 chars | 10 records (long enactment chain with multiple `eff.` clauses) |

All hierarchy paths are root-anchored chains of `{level, number,
name}` records. Title 22 doesn't exercise a deeper level than W1–3
already exercised.

### Tests

Test counts unchanged at **169 passed** (87 CC + 82 LRS, same as
post–2026-05-21). No new acts-parser tests were needed because no
new pattern was added. No new orchestrator tests were needed because
no orchestrate.py change was made.

### Source changes

**None.** This is the first wave to land with zero edits to any file
under `src/usufruct/`. Wave 4 is a pure data-pipeline rerun.

The only filesystem changes outside `data/rs/`:

| Path | Change |
| --- | --- |
| `tests/fixtures/lrs/justia/title-22.html` | NEW (594KB, copied from `lars-test/html/title-22.html`) |
| `BUILDHISTORY.md` | This entry. |

### Standing items (carry forward)

- **Re-snapshot**: `snapshots/lrs-2026-05-21/` still predates the
  2026-05-21 hierarchy + acts_parser fixes AND now predates Wave 4
  entirely. Re-snapshot would archive the post-W4 state under
  `snapshots/lrs-2026-05-22/`. Quick (~5s, no fetch). Deferred pending
  user go-ahead.
- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain` in
  orchestrate.py): trivial 15-line cleanup now that
  `LRSHierarchyIndex.lookup` is structurally correct. Deferred.
- **R.S. 22:1059.7** (`eff. See Act`): same family as the deferred
  R.S. 1:60 multi-eff case. Known shared-parser limit, not in the
  current CC-touch budget.
- **Title 47** (~2,500 sections + 11 Subtitles): the natural Wave 5,
  and the first wave that would exercise the Subtitle structural
  level in `parse/justia_title_parser.py` end-to-end.

## 2026-05-22 — Wave 5 (Title 47 Revenue and Taxation) end-to-end

Fifth wave. Title 47 lands clean structurally — the Subtitle level
propagates end-to-end through Phase 3 attachment, Phase 4
breadcrumbs/tree/markdown, with **zero source-code edits** (second
consecutive pure data-pipeline wave). Tree max depth bumps 6 → 7 as
expected. The 2026-05-22 rate-limit bump (1 → 2 req/s, commit
`c6caf18`) shipped its first practical use here: ~22 min wall time for
the 2,602 fresh fetches vs. the projected ~45 min at the old rate.

### Scope

- **2,665 sections** in `data/rs/sections/rs_47_*.json` (handoff
  estimated ~2,500; actual count from `section_index.json` was 2,663
  joined + 2,665 Justia, the 2 unjoined are out of Justia-only scope).
  Status breakdown: **2,239 active + 422 repealed + 4 blank**.
- **235 containers** for Title 47: 1 title + **11 subtitles** + 62
  chapters + 77 parts + 84 subparts. This is the first wave whose
  hierarchy includes a Subtitle level; `hierarchy.json` total
  unchanged at 5,529 (Title 47 containers were already present from
  corpus-wide Phase 1).
- All 2,665 Title 47 sections have **subtitle in `hierarchy_path`**
  (verified). Depth distribution: 10 at depth 2 (title/subtitle), 703
  at depth 3, 1,398 at depth 4, 554 at depth 5 (full
  title/subtitle/chapter/part/subpart chain).

### Process

1. Wave 5 candidate selection: Title 47 over Title 13 / Title 23
   because Subtitle was the only unexercised LRS structural level.
2. `.venv/bin/python -m usufruct.cli rs phase3 --titles 1,9,14,22,47`
   — symmetric form. ~22 min wall at 2 req/s (61 / 2,663 Title 47
   pages were already cached from prior probes; the rest were fresh).
   Titles 1/9/14/22 short-circuited via cache.
3. `.venv/bin/python -m usufruct.cli rs phase4` — ~10s; rebuilt
   tree/edges/chunks/markdown across the 8,393-section union.
4. Verification via `lars-test/wave5_verify.py` (new, retained as
   reproducible probe for future waves): Subtitle propagation,
   acts-parse rate, citation edges, validation report.
5. `.venv/bin/pytest -q` → **169 passed, zero regressions.**

### Output deltas

| Metric | Pre-Wave 5 (W1–4) | Post-Wave 5 | Δ |
| --- | --- | --- | --- |
| Sections emitted | 5,728 | 8,393 | +2,665 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (Title 47 was already in hierarchy.json) |
| Tree max depth | 6 | **7** | +1 (Subtitle exercised) |
| ActsCitation records | 10,210 | 15,745 | +5,535 |
| Citation edges | 4,494 | 7,706 | +3,212 |
| RAG chunks | 4,411 | 6,625 | +2,214 |
| Markdown files | 5,728 | 8,393 | +2,665 |
| Pytest passing | 169 | 169 | 0 |

`validation_report.sections_without_hierarchy = []` (clean).
`in_section_index_but_unemitted = 37,103` (all outside the
{1,9,14,22,47} scope, expected).

### Subtitle end-to-end confirmation

Sample breadcrumbs at each depth:

| Depth | Sample | Breadcrumb |
| --- | --- | --- |
| 2 | R.S. 47:8051 | `Title 47 › Subtitle X` |
| 3 | R.S. 47:1 | `Title 47 › Subtitle I › Chapter 1` |
| 4 | R.S. 47:820.5.4.1 | `Title 47 › Subtitle II › Chapter 7 › Part VI` |
| 5 | R.S. 47:21 | `Title 47 › Subtitle II › Chapter 1 › Part I › Subpart A` |

`tree.json` nests Subtitles cleanly under Title 47 — `_build_tree`
uses parent_chain keys, so Subtitle nesting was free. The breadcrumb
formatter (`_level_label_for_node` at orchestrate.py:75) already had
the Subtitle case wired from prior structural prep.

### Title 47 citation-edge breakdown

3,212 edges from Title 47 sources (~1.21/section — between Title 9's
1.5 and Title 22's 0.82). By destination corpus:

| Dst corpus | Count |
| --- | --- |
| `rs` (intra-LRS) | 3,192 |
| `civcode` | 10 |
| `ccp` | 8 |
| `crp` | 2 |
| `evidence` | 0 |

Top intra-LRS destinations: T47 self=2,734, T32=53 (motor
vehicles / fuel tax), T13=31, T39=25 (state revenue / budget),
T46=24 (public welfare), T49=22 (state administration), T40=22
(health), T33=22 (municipalities). Pattern reads as
taxation-centric — heavy self-references, modest cross-references
to revenue-adjacent Titles.

### Acts parsing: 18 active raw-unparsed (new pattern families)

Title 47 is the first wave to surface new acts-line patterns since
the 2026-05-21 eleven-pattern extension. **Active parse rate 2,016 /
2,239 = 90.04%** (cf. T22 89.6%, T9 82.0%, T14 94.2%). The gap
breaks down as:

- **205 sections with no acts-line at all** (`acts_citations_raw is
  null`) — legitimate; the legis page carries body only. In-family
  with T9 (363/2022), T22 (774/2518), T14 (38/655), T1 (19/41).
  Not a parse failure.
- **18 sections with raw text that didn't parse**. Five pattern
  families:

| Family | Count | Example |
| --- | --- | --- |
| `, applicable to taxable years...` | 4 | R.S. 47:120.191: `Acts 2013, No. 194, §1, applicable to taxable years on or after Jan. 1, 2013.` |
| `; Redesignated from R.S. X:Y pursuant to Acts ...` | 6 | R.S. 47:338.87–338.96 |
| Trailing `H.C.R. No. N, YYYY R.S.` | 2 | R.S. 47:305.9, 305.17 (House Concurrent Resolution) |
| Inline asterisk footnote (`. *...`) | 3 | R.S. 47:138, 633.2, 6002 (`*In (A)(1)(c), "..." is as it appears in enrolled bill.`) |
| Should-parse-but-don't | 3 | R.S. 47:813 (`Acts 1964, Ex.Sess., No. 3, §2.`), R.S. 47:1542.1, R.S. 47:641 |

The "should-parse-but-don't" family is the most surprising — these
are single Ex.Sess. citations or `Added by Acts YYYY, …. eff. DATE`
forms that look syntactically equivalent to known-good citations
elsewhere in the corpus (Ex.Sess. parses 76/87 times across all
titles). Inline `parse_acts_citation_line` runs return 0 records
silently. Likely an edge-case in the CC parser's continuation logic
when there are no further citations after the Ex.Sess. one — but
**confirming/fixing requires a CC-touch escalation** that has not
been granted in this session.

**No CC-touch budget consumed this wave.** Per the standing rule,
the 18 raw-unparsed are deferred to a future escalation along with
the 486 corpus-wide repealed-status raw-only sections and the
already-deferred R.S. 1:60 / R.S. 22:1059.7 `eff. See Act` family.

### Marquee spot-checks

| Citation | Heading | Hierarchy depth | Body | Acts |
| --- | --- | --- | --- | --- |
| R.S. 47:1 | Citation of Title | 3 (title/subtitle/chapter) | 41 chars | 1 record (1950 enactment) |
| R.S. 47:21 | Application of Chapter | 5 (title/subtitle/chapter/part/subpart) | 280 chars | 0 records (no_raw — legitimate) |
| R.S. 47:120.191 | (income-tax check-off) | 5 | normal body | RAW-UNPARSED — `applicable to taxable years` clause |
| R.S. 47:338.87 | (sales-tax redesignation) | 4 | normal body | RAW-UNPARSED — Redesignated-from semicolon tail |
| R.S. 47:813 | (corporate franchise tax) | 4 | normal body | RAW-UNPARSED — clean Ex.Sess. but parser returns 0 (surprising) |

### Tests

Test count unchanged at **169 passed** (87 CC + 82 LRS). No new
acts-parser tests were added because no new pattern was added; the
should-parse-but-don't family is the only candidate that *might*
warrant a test once the underlying CC behavior is understood, but
that's gated on a CC-touch escalation.

The pinned `tests/fixtures/lrs/justia/title-47.html` continues to
exercise Title 47 Justia parsing — Subtitle level presence, dense
Subpart letter gap (O → Q jump), and Subpart letters extending past
M. Those tests already passed before Wave 5; Wave 5 adds the
end-to-end Phase-3/4 demonstration.

### Source changes

**None.** Second consecutive wave with zero edits to any file under
`src/usufruct/`. Wave 5 is a pure data-pipeline rerun + new
verification helper.

Filesystem changes outside `data/rs/`:

| Path | Change |
| --- | --- |
| `lars-test/wave5_verify.py` | NEW (read-only verification harness, retained for reuse) |
| `BUILDHISTORY.md` | This entry. |

### Snapshot

Cut to `snapshots/lrs-2026-05-22-w5/` (151 MB). The CLI emits to
`snapshots/lrs-<date>/`, which collided with the W4 snapshot under
the same date — so the snapshot was renamed post-write to disambiguate.
The W4 data state is still recoverable from commit `61923fd`'s tracked
aggregates (`data/rs/*.json{l,csv}` + Title 1 per-section JSONs).

### Standing items (carry forward)

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain` in
  orchestrate.py): still trivial; still deferred.
- **18 Title 47 active raw-unparsed** (5 pattern families above): all
  require a third CC-touch on `src/usufruct/parse/acts_parser.py` to
  address. The **should-parse-but-don't** sub-family (R.S. 47:813,
  47:641, 47:1542.1) is the most worth investigating; the other
  families are genuinely new domain patterns.
- **R.S. 22:1059.7 / R.S. 1:60** (`eff. See Act`): same family,
  same CC-touch gate.
- **Wave 6 candidates** (smallest first): Title 23 (~700, labor /
  worker comp, cross-refs to T22/33), Title 13 (~1,500, courts &
  judicial procedure — no Subtitles), Title 39 (~1,500, state
  finance), Title 49 (~1,200, state administration), Title 33
  (~2,500, municipalities — second Subtitle wave), Title 17
  (~3,500, education — largest remaining mid-tier title).

## 2026-05-22 — Wave 7 (Title 49 State Administration) end-to-end

Seventh wave. Title 49 lands clean with **zero source-code edits**
(fourth consecutive pure data-pipeline wave). T49's actual section
count was 598, ~50% **under** the handoff estimate of ~1,200, so
the wall-clock cost was the smallest of any wave so far. The
primary W7 validation goal — that cross-corpus inbound edges to
T49 sections resolve cleanly — landed *exactly* on the briefing
prediction: T23 → T49 = 15 edges, T47 → T49 = 22 edges, both
exact matches.

### Scope

- **598 sections** in `data/rs/sections/rs_49_*.json` (handoff
  estimated ~1,200; actual `section_index.json` count was 598).
  Status: **527 active + 64 repealed + 7 blank**.
- **80 containers** for Title 49: 1 title + 0 subtitles + 28
  chapters + 39 parts + 12 subparts. Same structural shape as
  Title 14 / 22 / 23 (no Subtitle level). `hierarchy.json` total
  unchanged at 5,529 (T49 containers were already present from
  corpus-wide Phase 1).
- Tree `max_depth = 7` (unchanged from W5; T49 has no Subtitle so
  T49's deepest chain is depth-4 title/chapter/part/subpart).
  Depth distribution for T49: 156 at depth 2 (title/chapter), 319
  at depth 3 (title/chapter/part), 123 at depth 4
  (title/chapter/part/subpart).

### Process

1. `.venv/bin/usufruct rs phase3 --titles 1,9,14,22,47,23,49` —
   symmetric form, 6 cached titles short-circuited, T49 fetched
   fresh. Wall time ~5 min for the 598 fresh fetches at 2 req/s.
2. `.venv/bin/usufruct rs phase4` — ~10s; rebuilt
   tree/edges/chunks/markdown across the 9,863-section union.
3. Verification via `lars-test/wave7_verify.py` (new, adapted from
   wave6_verify.py with title filter and T23/T47 inbound-edge
   spotlights; also trimmed the unemitted-list dump to a count to
   avoid the 397KB output blob noted in the W6 handoff): depth
   distribution, acts-parse rate, citation edge breakdown, inbound
   edge validation, validation report.
4. `.venv/bin/pytest -q` → **169 passed, zero regressions.**

### Output deltas

| Metric | Pre-Wave 7 (W1–6) | Post-Wave 7 | Δ |
| --- | --- | --- | --- |
| Sections emitted | 9,265 | 9,863 | +598 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (T49 was already in hierarchy.json) |
| Tree max depth | 7 | 7 | 0 (T49 has no Subtitle) |
| ActsCitation records | 17,827 | 19,192 | +1,365 |
| Citation edges | 8,386 | 8,812 | +426 |
| RAG chunks | 7,346 | 7,848 | +502 |
| Markdown files | 9,265 | 9,863 | +598 |
| Pytest passing | 169 | 169 | 0 |

`validation_report.sections_without_hierarchy = []` (clean).
`in_section_index_but_unemitted = 35,633` (down from 36,231 by
exactly 598 — the T49 delta).

### Cross-corpus inbound validation — exact match

The primary motivation for picking T49 next was that T23 (W6) and
T47 (W5) already emitted 37 outbound edges into T49, and those
edges had no resolved targets until T49 itself was emitted. Both
counts come out **exactly** as predicted in the W5/W6 briefings:

| Source | Target | Briefing prediction | Observed |
| --- | --- | --- | --- |
| T23 → T49 | 49:950 / 49:978.1 etc. | 15 | **15** ✓ |
| T47 → T49 | 49:950 / 49:214.5.4 etc. | 22 | **22** ✓ |

Both inbound channels target T49's APA chapter heavily
(49:950, 49:951, 49:963 — the Administrative Procedure Act). This
validates the LRS→LRS cross-corpus resolver for every cross-title
edge written into the corpus during W5 and W6, retroactively.

### Title 49 citation-edge breakdown

426 edges from Title 49 sources (~0.71/section — new lowest density
of any wave so far, just under T23's 0.78; consistent with the
state-admin-statute style of self-contained Chapter-internal
references). By destination corpus:

| Dst corpus | Count |
| --- | --- |
| `rs` (intra-LRS) | 423 |
| `ccp` | 2 |
| `crp` | 1 |
| `civcode` | 0 |
| `evidence` | 0 |

Top intra-LRS destinations: T49 self=228 (53.9% of T49-source rs
edges — much lower self-ref ratio than T23 82.5%; T49 reaches
broadly across the corpus), **T42=24** (public officers — natural
companion to state admin), T30=18 (mineral law), T39=16 (state
finance — predicted hub), T37=10, T36=9, T13=9 (courts), T43=9,
T24=7 (industrial loans), T40=7, T17=7 (education), T6=7. T49
shows the broadest cross-Title reach of any wave so far —
consistent with state administration touching most subject-matter
domains.

### Acts parsing: 3 active raw-unparsed (3 new pattern families)

**Active parse rate 478 / 527 = 90.70%**, the second-highest
single-title rate (behind only T14 94.20%). The gap breaks down
as:

- **46 sections with no acts-line at all** (`acts_citations_raw is
  null`) — legitimate; the legis page carries body only. In-family
  with T9, T22, T23, T47. Not a parse failure.
- **3 sections with raw text that didn't parse** — three new
  pattern families:

| Family | Count | Example |
| --- | --- | --- |
| Multi-section in one Acts line (`§1, 2.`) | 1 | R.S. 49:157 (State artist laureate): `Acts 1952, No. 14, §1, 2.` |
| Compound enactment with comma separator (`§1, Amended by Acts ...`) | 1 | R.S. 49:159 (State bird): `Acts 1958, No. 486, §1, Amended by Acts 1966, No. 457, §1.` |
| `1st Ex.Sess.` (ordinal-prefixed special session) | 1 | R.S. 49:211 (Commissions; formalities): `Added by Acts 1975, 1st Ex.Sess. No. 46, §1, eff. Feb. 20, 1975.` |

All three are very narrow tail-end variants. The `1st Ex.Sess.`
variant is interesting — the existing `Ex.Sess.` regex already
parses 76/87 corpus-wide bare `Ex.Sess.` instances, so the
ordinal prefix (`1st`) is the specific gap. Adding these to the
consolidated CC-touch backlog rather than fixing standalone.

**Corpus active parse rate**: 81.66% → **82.21%** (+0.55 points).
**Per-title active parse rates**: T1 53.66%, T9 82.05%, T14
94.20%, T22 69.22%, T23 88.15%, T47 90.04%, T49 90.70%.

**No CC-touch budget consumed this wave.** Per the standing rule,
the 3 T49 raw-unparsed are deferred and folded into the existing
consolidated escalation backlog (now 22 active raw-unparsed across
9 pattern families).

### Marquee spot-checks

| Citation | Heading | Depth | Body | Acts records | Breadcrumb |
| --- | --- | --- | --- | --- | --- |
| R.S. 49:1 | Gulfward boundary | 3 | 1,357 b | 3 | Title 49 › Chapter 1 › Part I |
| R.S. 49:131 | Board created; members | 4 | 233 b | 1 | Title 49 › Chapter 1 › Part VII › Subpart A |
| R.S. 49:200.51 | Public funding for abortion providers; prohibition | 2 | 2,974 b | 0 | Title 49 › Chapter 1-A |
| R.S. 49:950 | Title and form of citation | 3 | 117 b | 1 | Title 49 › Chapter 13 › Part I |
| R.S. 49:951 | Definitions | 3 | 4,003 b | 6 | Title 49 › Chapter 13 › Part I |
| R.S. 49:963 | Department of Environmental Quality; procedure for adoption of rules | 3 | 9,713 b | 1 | Title 49 › Chapter 13 › Part II |
| R.S. 49:157 | State artist laureate | 3 | 301 b | 0 | Title 49 › Chapter 1 › Part VIII (RAW-UNPARSED — multi-section in one Acts line) |
| R.S. 49:159 | State bird | 3 | 286 b | 0 | Title 49 › Chapter 1 › Part VIII (RAW-UNPARSED — compound enactment) |
| R.S. 49:211 | Commissions; formalities | 3 | 192 b | 0 | Title 49 › Chapter 2 › Part I (RAW-UNPARSED — `1st Ex.Sess.`) |

R.S. 49:200.51 sits at depth 2 (chapter-direct) under Chapter
1-A, which has no Parts. R.S. 49:131 lands at the deepest T49
depth (4) under Chapter 1 / Part VII / Subpart A. R.S. 49:950 is
the inbound target of the bulk of the T23 and T47 cross-corpus
edges — the citation form of the Administrative Procedure Act
that those external titles reference.

### Tests

Test count unchanged at **169 passed** (87 CC + 82 LRS). No new
acts-parser tests, no new fixtures promoted. Title 49's Justia
parse is unremarkable (no new structural features) — fine to
defer fixture promotion until a wave needs it.

### Source changes

**None.** Fourth consecutive wave with zero edits to any file
under `src/usufruct/`. Wave 7 is a pure data-pipeline rerun +
new verification helper.

Filesystem changes outside `data/rs/`:

| Path | Change |
| --- | --- |
| `lars-test/wave7_verify.py` | NEW (read-only verification harness, retained for reuse — gitignored under lars-test/) |
| `BUILDHISTORY.md` | This entry. |

### Snapshot

Cut to `snapshots/lrs-2026-05-22-w7/` (172 MB). Per the standing
collision pattern, the CLI emits to `snapshots/lrs-<date>/` and
same-day snapshots collide, so the snapshot was renamed
post-write (W5 = `lrs-2026-05-22-w5/`, W6 =
`lrs-2026-05-22-w6/`, W7 = `lrs-2026-05-22-w7/`). W4 state
remains recoverable from commit `61923fd`.

### Standing items (carry forward)

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain`
  in orchestrate.py): still deferred. Prior session broke
  something attempting this cleanup; root cause was never
  captured in the standing-items notes, so don't touch without
  reconstructing the breakage first.
- **Consolidated CC-touch backlog** — now **22 active
  raw-unparsed across 9 pattern families** (still deferred; one
  escalation will fix all of these together once sized):
  - T47 (18 in 5 families): `applicable to taxable years` (4);
    `; Redesignated from R.S. X:Y pursuant to Acts ...` (6);
    trailing `H.C.R. No. N, YYYY R.S.` (2); inline asterisk
    footnote (3); should-parse-but-don't trio R.S. 47:813 /
    47:1542.1 / 47:641 (3, needs diagnostic-first).
  - T23 (1): trailing federal-code footnote tail in R.S.
    23:1491.
  - T49 (3 new this wave): multi-section in one Acts line
    (49:157); compound enactment with comma separator (49:159);
    `1st Ex.Sess.` ordinal-prefixed special session (49:211).
  - `eff. See Act` family (R.S. 22:1059.7 / R.S. 1:60).
- **Wave 8 candidates** (smallest first):
  - Title 13 (~1,500, courts & judicial procedure — no
    Subtitles). Heavy civcode/ccp cross-refs likely; a strong
    "5th consecutive no-edit rerun" candidate.
  - Title 39 (~1,500, state finance — T49 emits 16 outbound
    into T39, so emitting T39 would close another inbound-edge
    loop similar to T49's W7 validation).
  - Title 33 (~2,500, municipalities — second Subtitle wave;
    would re-exercise the Subtitle handling added during W5).
  - Title 17 (~3,500, education — largest remaining mid-tier
    title).
  - Title 42 (~unknown, public officers and employees — T49
    emitted 24 outbound edges into T42, the heaviest T49 cross-
    ref destination; an inbound-edge closure candidate).

## 2026-05-22 — Wave 6 (Title 23 Labor and Worker's Compensation) end-to-end

Sixth wave. Title 23 lands clean with **zero source-code edits**
(third consecutive pure data-pipeline wave). The actual section
count was 872, ~25% over the handoff estimate of ~700, which made
the wall-clock cost a touch higher than projected but well inside
the small-blast-radius envelope. Cross-corpus citation behavior
mostly matched the W5 briefing: T22 (insurance) cross-refs were
modest but real (19 edges); T33 (municipalities) cross-refs turned
out small (2 edges), so the "stresses LRS→LRS resolver against
T33" prediction in the briefing was overstated. T22→T23 traffic was
the more interesting cross-corpus signal.

### Scope

- **872 sections** in `data/rs/sections/rs_23_*.json` (handoff
  estimated ~700; actual `section_index.json` count was 872).
  Status: **726 active + 143 repealed + 3 blank**.
- **128 containers** for Title 23: 1 title + 0 subtitles + 23
  chapters + 70 parts + 34 subparts. Same structural shape as
  Title 14 (no Subtitle level). `hierarchy.json` total unchanged
  at 5,529 (T23 containers were already present from corpus-wide
  Phase 1).
- Tree `max_depth = 7` (unchanged from W5; T23 has no Subtitle so
  T23's deepest chain is depth-4 title/chapter/part/subpart).
  Depth distribution for T23: 83 at depth 2 (title/chapter), 486
  at depth 3 (title/chapter/part), 303 at depth 4
  (title/chapter/part/subpart).

### Process

1. `.venv/bin/usufruct rs phase3 --titles 1,9,14,22,47,23` —
   symmetric form, 5 cached titles short-circuited, T23 fetched
   fresh. Wall time ~7.5 min for the 872 fresh fetches at 2 req/s.
2. `.venv/bin/usufruct rs phase4` — ~10s; rebuilt
   tree/edges/chunks/markdown across the 9,265-section union.
3. Verification via `lars-test/wave6_verify.py` (new, adapted from
   wave5_verify.py with title filter and T22/T33 cross-ref
   spotlights): depth distribution, acts-parse rate, citation edge
   breakdown, validation report.
4. `.venv/bin/pytest -q` → **169 passed, zero regressions.**

### Output deltas

| Metric | Pre-Wave 6 (W1–5) | Post-Wave 6 | Δ |
| --- | --- | --- | --- |
| Sections emitted | 8,393 | 9,265 | +872 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (T23 was already in hierarchy.json) |
| Tree max depth | 7 | 7 | 0 (T23 has no Subtitle) |
| ActsCitation records | 15,745 | 17,827 | +2,082 |
| Citation edges | 7,706 | 8,386 | +680 |
| RAG chunks | 6,625 | 7,346 | +721 |
| Markdown files | 8,393 | 9,265 | +872 |
| Pytest passing | 169 | 169 | 0 |

`validation_report.sections_without_hierarchy = []` (clean).
`in_section_index_but_unemitted = 36,231` (down from 37,103 by
exactly 872 — the T23 delta).

### Title 23 citation-edge breakdown

680 edges from Title 23 sources (~0.78/section — lowest density of
any wave so far, slightly below T22's 0.82; consistent with the
labor-statute style of self-contained Chapter-internal references).
By destination corpus:

| Dst corpus | Count |
| --- | --- |
| `rs` (intra-LRS) | 671 |
| `ccp` | 5 |
| `civcode` | 4 |
| `crp` | 0 |
| `evidence` | 0 |

Top intra-LRS destinations: T23 self=561 (82.5% of T23-source rs
edges — the densest self-ref ratio yet), **T22=19** (insurance,
matches briefing prediction), T49=15 (state administration), T42=10,
T13=7 (courts), T40=5, T36=5, T9=5 (CC family), T51=4, T17=4
(education), T29=4 (military), T46=4 (public welfare), **T33=2**
(municipalities — far smaller than the briefing predicted "heavy
cross-references"). The T22 link is concentrated in the
worker-comp Chapters; the T49 link reflects the LWC (Louisiana
Workforce Commission) administrative structure.

### Acts parsing: 1 active raw-unparsed (new pattern family)

**Active parse rate 640 / 726 = 88.15%**, between T14 (94.20%) and
T22 (69.22%). The gap breaks down as:

- **85 sections with no acts-line at all** (`acts_citations_raw is
  null`) — legitimate; the legis page carries body only. In-family
  with T9 (363/2022), T22 (774/2518), T47 (205/2239). Not a parse
  failure.
- **1 section with raw text that didn't parse** — new pattern
  family:

| Family | Count | Example |
| --- | --- | --- |
| Trailing federal-code footnote (`1 42 U.S.C.A. §1103.`) | 1 | R.S. 23:1491: `Amended by Acts 1959, No. 4, §1. 1 42 U.S.C.A. §1103.` |

This is a single instance, very narrow. The first token (the
single digit `1`) is the footnote anchor; `42 U.S.C.A. §1103.` is
the federal-code reference. The parser stops at the first
non-Acts-line token, which here is the literal `1`. Adding this to
the consolidated CC-touch backlog rather than fixing standalone.

**Corpus active parse rate**: 81.03% → **81.66%** (+0.63 points).
**Per-title active parse rates**: T1 53.66%, T9 82.05%, T14
94.20%, T22 69.22%, T23 88.15%, T47 90.04%.

**No CC-touch budget consumed this wave.** Per the standing rule,
the 1 T23 raw-unparsed is deferred and folded into the existing
consolidated escalation backlog (18 T47 in 5 families + the
`eff. See Act` family).

### Marquee spot-checks

| Citation | Heading | Depth | Body | Acts records | Breadcrumb |
| --- | --- | --- | --- | --- | --- |
| R.S. 23:1 | Louisiana Works established; purpose; definitions | 3 | 2,349 b | 8 | Title 23 › Chapter 1 › Part I |
| R.S. 23:301 | Short title | 3 | 94 b | 1 | Title 23 › Chapter 3-A › Part I |
| R.S. 23:631 | Discharge or resignation of employees; payment after termination | 2 | 4,206 b | 8 | Title 23 › Chapter 6 |
| R.S. 23:1031 | Employee's right of action; joint employers, extent of liability | 4 | 2,941 b | 2 | Title 23 › Chapter 10 › Part I › Subpart B |
| R.S. 23:1491 | Establishment and control | 4 | 950 b | 0 | Title 23 › Chapter 11 › Part II › Subpart A (RAW-UNPARSED — federal-code footnote tail) |

R.S. 23:631 lands at depth 2 (chapter-direct), the most common
shallow shape in Title 23 — Chapter 6 has its sections attached
directly under Chapter without any intervening Part. R.S.
23:1031 (the worker's-comp action statute) lands at the deepest
T23 depth, depth 4.

### Tests

Test count unchanged at **169 passed** (87 CC + 82 LRS). No new
acts-parser tests, no new fixtures promoted. `lars-test/html/
title-23.html` (Justia index, ~4.6 k lines) is a candidate for
promotion to `tests/fixtures/lrs/justia/title-23.html` to match
the W4 precedent for T22, but Title 23's Justia parse is
unremarkable — no new structural features to exercise — so it's
fine to defer until a wave needs the fixture.

### Source changes

**None.** Third consecutive wave with zero edits to any file
under `src/usufruct/`. Wave 6 is a pure data-pipeline rerun +
new verification helper.

Filesystem changes outside `data/rs/`:

| Path | Change |
| --- | --- |
| `lars-test/wave6_verify.py` | NEW (read-only verification harness, retained for reuse — gitignored under lars-test/) |
| `BUILDHISTORY.md` | This entry. |

### Snapshot

Cut to `snapshots/lrs-2026-05-22-w6/`. Per the W5 collision
note, the CLI emits to `snapshots/lrs-<date>/` and same-day
snapshots collide, so the snapshot was renamed post-write to
disambiguate (W4 → lrs-2026-05-22/ was already overwritten by
W5; W5 = lrs-2026-05-22-w5/; W6 = lrs-2026-05-22-w6/). W4
state remains recoverable from commit `61923fd`.

### Standing items (carry forward)

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain`
  in orchestrate.py): still deferred. Prior session broke
  something attempting this cleanup; root cause was never
  captured in the standing-items notes, so don't touch without
  reconstructing the breakage first.
- **Consolidated CC-touch backlog** (third escalation will fix
  all of these together once sized):
  - 18 Title 47 active raw-unparsed in 5 pattern families.
  - 1 Title 23 active raw-unparsed (federal-code footnote tail
    in R.S. 23:1491).
  - `eff. See Act` family (R.S. 22:1059.7 / R.S. 1:60).
  - Should-parse-but-don't trio (R.S. 47:813 / 47:1542.1 /
    47:641) — needs diagnostic-before-fix (trace why
    `parse_acts_citation_line` returns 0 records on
    `Acts 1964, Ex.Sess., No. 3, §2.` in solo-citation
    context, when Ex.Sess. parses 76/87 times corpus-wide).
- **Wave 7 candidates** (smallest first): Title 13 (~1,500,
  courts & judicial procedure — no Subtitles), Title 39
  (~1,500, state finance), Title 49 (~1,200, state
  administration), Title 33 (~2,500, municipalities — second
  Subtitle wave), Title 17 (~3,500, education — largest
  remaining mid-tier title). Title 49 has appeal as a small
  cross-ref hub (already a T47/T23 destination); Title 13 is
  the natural next "small" wave for a 5th consecutive no-edit
  rerun.

