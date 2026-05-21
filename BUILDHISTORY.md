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

