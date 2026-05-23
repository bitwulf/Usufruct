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

## 2026-05-22 — Corpus completion: bulk-fetch remaining 42 Titles

**Methodology shift.** After eleven per-wave runs (W1–W11) consumed
~3 days and validated the inbound-closure prediction methodology
across 22 source-target predictions (all exact), the marginal
information value of another per-wave checkpoint had dropped to
near zero. Per user direction, switched to a single bulk run for
the remaining 42 numbered Titles: one phase3, one phase4, one
pytest, one consolidated verify, one snapshot, one BUILDHISTORY
entry, one commit.

### Top-line result

| Metric | Pre-bulk (W11) | Post-bulk | Δ |
| --- | --- | --- | --- |
| Titles processed | 11 | **53** (corpus-complete) | +42 |
| Sections emitted | 15,064 | **45,774** | +30,710 |
| Active / Repealed / Blank | 13,178 / 1,856 / 30 | 39,129 / 6,280 / 365 | +25,951 / +4,424 / +335 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (all titles already in `hierarchy.json` from phase 1) |
| Tree max depth | 7 | 7 | 0 |
| ActsCitation records | 28,269 | **87,254** | +58,985 |
| Citation edges | 11,850 | **31,631** | +19,781 |
| RAG chunks | 12,210 | **37,020** | +24,810 |
| Markdown files | 15,064 | 45,774 | +30,710 |
| Pytest passing | 169 | 169 | 0 |
| `in_section_index_but_unemitted` | 30,432 | **0** | -30,432 (corpus complete) |

`validation_report.sections_without_hierarchy = 0` (clean).

### Process

1. **Capture pre-bulk prediction baseline.** Wrote
   `lars-test/bulk_predictions.json` with per-source × per-target
   inbound-edge counts for every (S, T) pair where S ∈ original 11
   processed and T ∈ 42 remaining. **216 (S, T) pairs, total 1,860
   predicted edges**.
2. **Bulk phase3** (first attempt): `.venv/bin/usufruct --rate-limit
   5.0 rs phase3 --titles <all 53>`. Crashed mid-run with
   `ValueError: Could not locate LabelName on legis section page`
   after fully fetching T11 (1,796 sections) and one T12 section.
3. **Two LRS-side parser fixes** (see "Source changes" below).
   Tests: 169 → 169 pass.
4. **Bulk phase3** (re-run, with fixes): completed cleanly.
   45,774 sections emitted across all 53 titles. ~2.5 hours wall
   time for ~28,635 fresh fetches at observed ~3 req/s (rate-limit
   5/s sets upper bound; HTML parse cost per page gates effective
   throughput).
5. **Bulk phase4**: tree(max_depth=7), 31,631 edges, 37,020 chunks,
   45,774 markdown files — full corpus rebuild ~30s.
6. **Pytest**: 169 passing.
7. **Consolidated verify** (`lars-test/bulk_verify.py`): per-pair
   inbound-closure check across all 216 predicted (S, T) pairs,
   plus per-title parse rates and family-clustered raw-unparsed
   backlog for all 42 new titles.
8. **Snapshot**: `snapshots/lrs-2026-05-22-corpus-complete/`
   (672 MB).

### Inbound-closure validation — 216/216 exact

| Aggregate | Result |
| --- | --- |
| Source-target pairs predicted | **216** |
| Pairs with observed = predicted | **216** |
| Total predicted edges | 1,860 |
| Total observed edges | **1,860** |
| Delta | **0** |
| Cumulative streak (W7-W11 + bulk) | **238 predictions all exact** |

The single-shot 216-way exact match across 42 newly-emitted target
titles is the strongest possible validation that the cross-corpus
citation resolver scales without degradation. Highlights from the
prediction-vs-observed table:

| Target | Predicted | Observed | Sources |
| --- | --- | --- | --- |
| T40 (Public Health) | 261 | **261** | T14=69, T13=49, T9=43, T22=33, T47=22 (+others) |
| T46 (Public Welfare) | 150 | **150** | T14=41, T9=37, T47=24, T13=22 |
| T32 (Military) | 149 | **149** | T47=53, T14=35, T22=27, T13=15, T9=12 |
| T33 (Municipalities) | 131 | **131** | T13=46, T47=22, T9=21, T38=13 |
| T15 (Criminal Procedure) | 129 | **129** | T14=66, T13=30, T39=9, T47=9 |
| T17 (Education) | 125 | **125** | T14=30, T39=29, T47=19, T42=15, T13=13 |
| T24 (Insurance Code redux) | 101 | **101** | T39=32, T38=15, T13=11, T42=11 |
| T44 (House/Legislative) | 96 | **96** | T22=39, T13=18, T39=8 |
| T37 (Telecomms/Utilities) | 87 | **87** | T22=34, T9=30, T49=10 |
| T51 (Public Property) | 82 | **82** | T22=24, T47=18, T39=13, T9=9, T14=7 |
| **(all 216 pairs exact; six targets at 0 predicted=0 observed)** | | | |

### Source changes — two LRS-side parser fixes

The first bulk-phase3 attempt surfaced a parser format the regex
didn't handle: **R.S. 12:1** (`Law.aspx?d=1016260`) returns
`LabelName = "RS 12:1-1705"` — a hyphenated section number from
the 2015 Business Corporation Act renumbering. The strict regex
`[0-9.]+` for the section group rejected the hyphen → ValueError
→ whole-run abort. Two LRS-side fixes applied:

| File | Change |
| --- | --- |
| `src/usufruct/lrs/parse/legis_section_parser.py` | Relax `_LABELNAME_RE` section group from `[0-9.]+` to `[0-9A-Za-z.\-]+`. Now accepts hyphens (R.S. 12:1-1705) and any future alphanumeric section identifiers. |
| `src/usufruct/lrs/pipeline/orchestrate.py` | Wrap `parse_legis_section` in `try/except ValueError` → `continue` (let backfill emit blank). Replace the misleading "soft conflict" `pass` with `continue` (so legis-vs-Justia title/section mismatches no longer mis-attribute content; backfill emits blank instead). |

Both fixes are LRS-side (`src/usufruct/lrs/`) — no CC-touch.
Empirical result of the second fix: **52 sections** had genuine
parse failures or title/section mismatches (mostly T12's
hyphenated-numbering redirects — 249 T12 blanks total when
combined with backfill); all gracefully emitted as blank
placeholders rather than crashing the run.

### Per-title parse-rate breakdown (newly-added 42)

Distribution: **30 of 42 new titles** parse at ≥80%; **5** parse
at 90%+. The bulk's high-parse-rate titles lifted the corpus
active parse rate from W11's 82.52% → **84.33%** (+1.81 pts).

| Tier | Titles | Cluster behavior |
| --- | --- | --- |
| ≥95% | T8 (99.49%), T26 (99.61%), T27 (99.26%), T28 (98.68%), T31 (99.54%), T6 (98.32%), T19 (98.01%), T32 (97.70%), T18 (96.47%), T37 (96.39%), T36 (98.84%) | Most have ≤2 raw-unparsed; modern statutes with consistent acts-line format |
| 90–95% | T15 (94.35%), T46 (94.71%), T56 (94.36%), T4 (94.40%), T17 (91.55%), T52 (91.67%), T21 (90.62%), T24 (92.08%), T25 (92.27%) | Strong baseline with a handful of legacy edge-cases |
| 80–90% | T2 (83.55%), T3 (86.83%), T12 (81.70%), T16 (84.21%), T29 (87.11%), T30 (87.90%), T34 (82.30%), T35 (81.82%), T40 (80.01%), T43 (84.93%), T48 (80.16%), T51 (89.69%) | Lower because of mixed-era statutes with older format clusters |
| 60–80% | T11 (72.05%), T33 (73.10%), T44 (77.63%), T20 (60.00%) | T11 (Public Retirement) and T33 (Municipalities) — large titles with many legacy `1st Ex.Sess.` and `Redesignated` clusters |
| <60% | T41 (58.90%), T45 (59.48%), T50 (39.02%), T53 (14.81%), T54 (22.22%), T55 (0.00%) | All but T41 and T45 are tiny (≤44 sections); their parse rates reflect 1-2 stuck sections rather than systemic gaps |

### Citation network — cross-corpus and outbound-rate

| Dst corpus | Edges | Notes |
| --- | --- | --- |
| `rs` (intra-LRS) | 30,942 | 97.8% of all edges |
| `civcode` | 276 | up from 28 (T13's civcode-heavy share + new contributions) |
| `ccp` | 238 | up from 100 |
| `crp` | 156 | up from 22 |
| `evidence` | 19 | up from 11 |

Top outbound-rate newly-added titles (edges per section, intra-LRS
+ cross-corpus combined):

| Title | Edges | Sections | Rate |
| --- | --- | --- | --- |
| T15 (Criminal Procedure) | 1,404 | 1,050 | **1.34**/section |
| T18 (Election Code) | 776 | 591 | **1.31**/section |
| T11 (Public Officers' Retirement) | 1,881 | 1,796 | **1.05**/section |
| T32 (Military) | 880 | 871 | **1.01**/section |
| T30 (Mineral / Wildlife) | 778 | 1,025 | 0.76/section |
| T17 (Education) | 1,458 | 2,162 | 0.67/section |
| T33 (Municipalities) | 2,531 | 4,117 | 0.61/section |
| T40 (Public Health) | 1,951 | 3,330 | 0.59/section |
| T46 (Public Welfare) | 669 | 1,237 | 0.54/section |
| T37 (Telecomms / Utilities) | 1,016 | 2,014 | 0.50/section |

### Marquee spot-checks (heaviest sections from the bulk)

| Citation | Heading | Depth | Text | Acts records | Breadcrumb |
| --- | --- | --- | --- | --- | --- |
| **R.S. 33:9038.76** | **College economic development districts** | 3 | **93,339 b** | 1 | Title 33 › Chapter 27 › Part XII-A (NEW corpus heavyweight, surpassing R.S. 13:5554's 72,326 b) |
| R.S. 33:1236 | Powers of parish governing authorities | 3 | 90,860 b | 61 | Title 33 › Chapter 4 › Part I |
| R.S. 33:4720.151 | East Baton Rouge Redevelopment Authority | 2 | 72,651 b | 6 | Title 33 › Chapter 27-A |
| R.S. 33:4720.181 | New Iberia Redevelopment Authority | 2 | 69,923 b | 2 | Title 33 › Chapter 27-A |
| R.S. 33:4720.161 | Parish redevelopment authority | 3 | 69,587 b | 3 | Title 33 › Chapter 27-A |
| R.S. 13:5554 | Group insurance; kinds; amounts; subrogation | 3 | 72,326 b | 81 | Title 13 (prior champ, now #5) |

T33 (Municipalities and Parishes) dominates the heaviest-section
list — its redevelopment-authority and parish-governance statutes
carry decades of compounded amendments.

### Raw-unparsed backlog — 366 active raw-unparsed across new titles

Up from W11's 56 cross-Title cumulative. Family clustering across
the 42 new titles (active status only):

| Family | New (bulk) | Cumulative (cross-Title) | Notes |
| --- | --- | --- | --- |
| **OTHER** | 102 | 102 | Largely `Renumbered from R.S.1950, §X:Y by Acts ...` — a major NEW family from older codification cleanup (esp. T12, T29) |
| `Ex.Sess.` (no-space) | 101 | 122 (was 21) | Existing top-leverage family — 5× growth from the bulk |
| `Redesignated` | 67 | 74 (was 7) | T11 alone contributed dozens (R.S. 17:7XX → 11:9XX transfers) |
| `emerg. eff.` | 22 | 24 (was 2) | Modest growth; the `<date>` regex would now close 22 instances |
| `Ex. Sess.` (with space) | 19 | 21 (was 2) | The space-form cousin |
| `federal-code footnote` (`U.S.C.A.`) | 19 | 20 (was 1) | T12, T17 — banking/education with federal-code refs |
| `eff. See Act` | 11 | 12 (was 2) | T17 cluster |
| `operative` (vs `eff.`) | 8 | 9 (was 1) | T25 cluster |
| `inline asterisk footnote` | 6 | 9 (was 3) | T26, T33, T41 |
| `As-appears-in` footnote | 4 | 5 (was 1) | T25, T45, T48 |
| typo `No,` | 3 | 4 (was 1) | T17, T33, T4 |
| `§N(A)` parenthesized subsection | 2 | 3 (was 1) | T11 cluster |
| `Act No.` (vs `No.`) | 2 | 3 (was 1) | T33 cluster |

The top three families (OTHER, `Ex.Sess.` no-space, `Redesignated`)
account for **270 of 366 (74%)** of the post-bulk backlog. A
focused LRS-side parser-extension pass against those three would
collapse the backlog to ~100 sections — and the OTHER bucket is
mostly one new pattern (`Renumbered from R.S.1950`) that a single
regex would close.

### Tests

Test count unchanged at **169 passed** (87 CC + 82 LRS). The
parser-regex relaxation and orchestrate.py exception handling did
not affect existing test fixtures.

### Per-title section emission summary

Newly-added (42 titles, sorted by section count):

```
T33 4117 | T40 3330 | T17 2162 | T37 2014 | T11 1796 | T3 1436
T46 1237 | T51 1237 | T15 1050 | T30 1025 | T56 896 | T34 901
T48 927 | T32 871 | T6 809 | T18 591 | T45 554 | T25 545
T12 924 | T36 350 | T28 365 | T29 476 | T16 272 | T26 272
T4 273 | T24 284 | T27 313 | T44 207 | T19 203 | T8 200
T31 230 | T41 305 | T2 188 | T35 129 | T43 81 | T50 44
T21 32 | T53 27 | T54 18 | T20 5 | T52 13 | T55 1
```

Together with the original 11 (T1, T9, T13, T14, T22, T23, T38,
T39, T42, T47, T49) the corpus now covers all 53 numbered Titles
present in `section_index.json`.

### Files committed

- **BUILDHISTORY.md** — this entry (above all per-wave W1–W11
  entries; remains reverse-chronological).
- **src/usufruct/lrs/parse/legis_section_parser.py** — regex
  relaxation.
- **src/usufruct/lrs/pipeline/orchestrate.py** — exception
  handling and mismatch-skip.
- **data/rs/{citation_edges.csv, manifest.json, tree.json,
  validation_report.json}** — corpus metadata aggregates.
- **data/rs/sections/rs_1_*.json** — T1 sample (per the
  `.gitignore` allowlist; other titles' section JSONs remain
  local-only and regenerable from the data/raw/ cache).
- **`.gitignore`** — added `data/rs/sections.jsonl` (~120 MB) and
  `data/rs/chunks.jsonl` (~80 MB) to the ignore list. Both
  crossed GitHub's file-size limits after corpus completion and
  are trivially rebuilt by phase4; they ship via local cache +
  Releases rather than git.
- **lars-test/bulk_predictions.json** — gitignored.
- **lars-test/bulk_verify.py** — gitignored.

### Snapshot

`snapshots/lrs-2026-05-22-corpus-complete/` (672 MB). Renamed
post-write per the standing collision pattern. All prior W4–W11
snapshots retained.

### Standing items (carry forward)

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain`):
  still deferred ([[project_hierarchy_bypass_prior_breakage]]).
- **LRS-side parser-extension backlog** — now **366 cross-Title
  active raw-unparsed** (was 56 post-W11). With the corpus now
  complete, this is the natural next session: build
  `src/usufruct/lrs/parse/lrs_acts_parser.py` (wrapper around the
  CC `parse_acts_citation_line` with LRS-specific fallback regexes
  for the top families) + `usufruct rs reparse` CLI subcommand
  (in-place reparse of `acts_citations_raw` for sections where
  `acts_citations == []`). Estimated fix yield: ~70–80% of the
  backlog from the top three families alone.
- **CC `parse_acts_citation_line` itself remains untouched** —
  no CC-touch was consumed by the bulk run.
- **52 parse-skipped sections + 313 backfill blanks = 365 blank
  sections.** Most concentrate in T12 (249) where the legis
  section URLs redirect to the 2015 corporations-code numbering
  that doesn't match Justia's section_index. Recoverable via a
  future reverse-lookup phase if needed.

## 2026-05-22 — Wave 11 (Title 38 Public Contracts, Works and Improvements) end-to-end

Eleventh wave. Title 38 lands clean with **zero source-code edits**
(eighth consecutive pure data-pipeline wave). Three distinguishing
features:

- **Fifth consecutive exact inbound-closure wave.** Pre-W11
  prediction: T39=26, T49=5, T47=4, T42=1, T9=1 (sum 37). Post-W11:
  every source matched exactly. The "All cross-Title inbound to T38"
  table shows **only** those 5 sources — no other prior-processed
  Title emits a single edge into T38. The streak now spans W7–W11
  (five waves, twenty-one source-Title predictions all exact).
- **Lowest outbound-rate wave so far.** T38's 582 source edges over
  1,355 sections = **0.43 edges/section** — well below T13 (0.61),
  T39 (0.63), T49 (0.71), T22 (0.79), T42 (0.79), T23 (0.93).
  Public-contracts code is by nature a **citation target rather than
  a citation source**: it gets referenced by finance/admin/works
  statutes more than it cites them. Only 10 non-rs outbound edges
  (6 ccp + 4 civcode + 0 crp + 0 evidence) — the inverse profile
  of T13's 141.
- **Second wave to drag corpus parse rate down.** Corpus active
  parse rate 82.88% → **82.52%** (-0.36 pts, more drag than W9's
  T42 drop of -0.25 pts). T38's own active parse rate is 78.93% —
  fourth-lowest individual rate (above T22 69.22% and T1 53.66%
  and T42 77.07%; below T13 82.96%). Driver: high no-raw proportion
  (20.1%) plus a `emerg. eff. June 14, 1971` cluster.

### Scope

- **1,355 sections** in `data/rs/sections/rs_38_*.json`. Status:
  **1,182 active + 171 repealed + 2 blank**. Same size as T39
  exactly (W8 was also 1,355 sections — coincidence).
- T38 has no Subtitles (depth-4 max profile, same shape family as
  T13/T14/T22/T23/T42/T49). Depth distribution: 225 d2, 1,067 d3,
  63 d4 (modal d3 — chapter/part). `hierarchy.json` unchanged.
- Tree `max_depth = 7` (unchanged corpus-wide). Deepest T38 example:
  R.S. 38:401 → Title 38 › Chapter 4 › Part VI › Subpart A.

### Process

1. `.venv/bin/usufruct rs phase3 --titles 1,9,14,22,47,23,49,39,42,13,38` —
   symmetric form, 10 cached titles short-circuited, T38 fetched
   fresh. Wall time ~11 min for the 1,355 fresh fetches at 2 req/s.
2. `.venv/bin/usufruct rs phase4` — rebuilt
   tree/edges/chunks/markdown across the 15,064-section union.
3. Verification via `lars-test/wave11_verify.py` (new, per-source
   inbound assertions against the 5-source baseline).
4. `.venv/bin/pytest -q` → **169 passed, zero regressions.**

### Output deltas

| Metric | Pre-Wave 11 (W1–10) | Post-Wave 11 | Δ |
| --- | --- | --- | --- |
| Sections emitted | 13,709 | 15,064 | +1,355 |
| Active / Repealed / Blank | 11,996 / 1,685 / 28 | 13,178 / 1,856 / 30 | +1,182 / +171 / +2 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (T38 already in hierarchy.json) |
| Tree max depth | 7 | 7 | 0 |
| ActsCitation records | 26,427 | 28,269 | +1,842 |
| Citation edges | 11,268 | 11,850 | +582 |
| RAG chunks | 11,034 | 12,210 | +1,176 |
| Markdown files | 13,709 | 15,064 | +1,355 |
| Pytest passing | 169 | 169 | 0 |

`validation_report.sections_without_hierarchy = 0` (clean).
`in_section_index_but_unemitted = 30,432` (down from 31,787 by
exactly 1,355 — the T38 delta).

### Cross-corpus inbound validation — five exact matches, exhaustive

| Source | Briefing prediction | Observed |
| --- | --- | --- |
| T39 → T38 | 26 | **26** ✓ |
| T49 → T38 | 5 | **5** ✓ |
| T47 → T38 | 4 | **4** ✓ |
| T42 → T38 | 1 | **1** ✓ |
| T9 → T38 | 1 | **1** ✓ |
| **Sum** | **37** | **37** ✓ |

The "All cross-Title inbound to T38 (top sources)" view shows
exactly the same five sources with no others — **the predicted
set was complete**, not just accurate. With only 11 titles
processed, T38 receives inbound from less than half of them
(5/10 prior titles); public-contracts is a relatively isolated
node in the citation network.

Cumulative streak: W7 (15+22 exact) + W8 (16 exact) + W9 (24+22
exact) + W10 (8 sources, sum 145 exact) + W11 (5 sources, sum 37
exact) = **22 source-Title predictions across 5 consecutive waves,
all exact**.

### Title 38 citation-edge breakdown

582 edges from Title 38 sources (~0.43/section, lowest-ever
outbound rate). By destination corpus:

| Dst corpus | Count |
| --- | --- |
| `rs` (intra-LRS) | 572 |
| `ccp` | 6 |
| `civcode` | 4 |
| `crp` | 0 |
| `evidence` | 0 |

Top intra-LRS destinations: T38 self=326 (56% self-ref —
typical), T39=56 (bidirectional with the 26 inbound — net flow
T38→T39 is +30), T49=52 (state admin), T47=24, T42=17, T14=15,
T24=15, T33=13, T31=10, T45=8.

### Acts parsing: 11 active raw-unparsed (one cluster dominates)

**Active parse rate 933 / 1,182 = 78.93%**. T38 sits between
T42 (77.07%) and T13 (82.96%) — fourth-lowest. 238 active
sections legitimately have no acts-line (`acts_citations_raw is
null` — 20.1% of T38 active, similar to T22's 30.8% but lower).

The 11 active raw-unparsed cluster as follows:

| Family | Count this wave | Cumulative cross-Title | Example |
| --- | --- | --- | --- |
| **`emerg. eff. June 14, 1971`** — same-date cluster from one Act | 6 | (single-instance family, 6 sequential sections) | R.S. 38:3102 / 3104 / 3106 / 3107 / 3108 / 3109 (`Acts 1971, No. 116, §1, emerg. eff. June 14, 1971.`) |
| **`emerg. eff.` + trailing federal-code footnote** — composite of T23/T38 families | 1 | 2 (with T23:1491) | R.S. 38:3103 (`emerg. eff.` plus `1 12 U.S.C.A. §1715 L (d)(2). 2 15 U.S.C.A. §636(b)(3).`) |
| **`Redesignated by Acts`** — existing T47 family | 1 | 7 (with T47:338.87/92–96) | R.S. 38:2212.3 (`Acts 1985, No. 922, §1. Redesignated by Acts 1999, No. 768, §3.`) |
| **`S.C.R. No.`** (Senate Concurrent Resolution) — NEW family | 1 | 1 | R.S. 38:90.6 (`S.C.R. No. 4, 1986 1st Ex. Sess.; S.C.R. No. 2, 1988 1st Ex. Sess.`) |
| **`As it appears in...original House Bill`** — variant of T39's "As appears in enrolled bill" | 1 | 2 (with T39:330.2) | R.S. 38:3053 (`1 As it appears in Acts 1970, No. 682 and in the original House Bill No. 1635.`) |
| **Typo `, 6.` (missing `§`)** — NEW data quality | 1 | 1 | R.S. 38:2756 (`Added by Acts 1962, No. 308, 6.` — should be `§6.`) |

Notable: **6 of 11** are the same `Acts 1971, No. 116, §1,
emerg. eff. June 14, 1971` footer, occurring in consecutive
sections of Chapter 14 (Administration of relocation assistance
programs — 38:3102, 3104, 3106, 3107, 3108, 3109). This means a
single regex update to handle `emerg. eff. <Month Day, Year>.`
would close 6 T38 sections AND 38:3103 (the composite variant)
AND T13:2488.2 / T39:991.1 (existing emerg.eff. instances) — a
**9-section yield** from one targeted fix.

**Corpus active parse rate**: 82.88% → **82.52%** (-0.36 pts;
second wave to depress corpus parse rate, larger drag than W9's
-0.25 pts).
**Per-title active parse rates**: T1 53.66%, T9 82.05%, T13
82.96%, T14 94.20%, T22 69.22%, T23 88.15%, T38 78.93%, T39
90.58%, T42 77.07%, T47 90.04%, T49 90.70%.

**No CC-touch budget consumed this wave.** The 11 T38 raw-
unparsed are deferred and fold into the (LRS-side reframed)
parser-extension backlog ([[feedback_cc_touch_budget_strategy]]).

### Marquee spot-checks

| Citation | Heading | Depth | Text | Acts records | Breadcrumb |
| --- | --- | --- | --- | --- | --- |
| R.S. 38:1 | Department of public works; domicile; service of process | 2 | 743 b | 1 | Title 38 › Chapter 1 |
| R.S. 38:111 | Contracts by drainage districts, levee boards, and police juries | 3 | 1,162 b | 3 | Title 38 › Chapter 3 › Part I |
| R.S. 38:401 | Levee boards and levee and drainage boards authorized to construct | 4 | 287 b | 1 | Title 38 › Chapter 4 › Part VI › Subpart A |
| **R.S. 38:291** | **Naming; limits of districts; composition of boards** | 3 | **59,588 b** | **56** | Title 38 › Chapter 4 › Part II (heaviest T38 single section) |
| R.S. 38:2211 | Definitions | 3 | 6,358 b | 13 | Title 38 › Chapter 10 › Part II (heaviest single-section inbound: 44, primarily from T39) |
| R.S. 38:2212 | Advertisement and letting to lowest responsible and responsive bidders | 3 | 34,489 b | 53 | Title 38 › Chapter 10 › Part II (the LRS public-bidding statute — anchor of public-contracts law) |
| R.S. 38:2212.3 | Right to reject bids from Communist countries | 3 | 526 b | 0 | Title 38 › Chapter 10 › Part II (RAW-UNPARSED — `Redesignated by Acts`; Cold War-era statute) |
| R.S. 38:3102 | Administration of relocation assistance programs | 2 | 400 b | 0 | Title 38 › Chapter 14 (RAW-UNPARSED — `emerg. eff. June 14, 1971`, the dominant T38 cluster) |

R.S. 38:2211 (Definitions for Part II — public bidding) is the
single most-cited T38 section at 44 inbound edges, including the
26 from T39. R.S. 38:291 (Naming, limits, boards composition for
levee/drainage districts) is the heaviest T38 single section at
59,588 b with 56 acts records — public-works districts have
extensive amendment history.

### Tests

Test count unchanged at **169 passed** (87 CC + 82 LRS). No new
acts-parser tests, no new fixtures promoted.

### Source changes

**None.** Eighth consecutive wave with zero edits to any file
under `src/usufruct/`. Wave 11 is a pure data-pipeline rerun +
new verification helper.

Filesystem changes outside `data/rs/`:

| Path | Change |
| --- | --- |
| `lars-test/wave11_verify.py` | NEW (read-only verification harness with per-source inbound assertions, retained for reuse — gitignored under lars-test/) |
| `BUILDHISTORY.md` | This entry. |

### Snapshot

Cut to `snapshots/lrs-2026-05-22-w11/` (241 MB). Per the standing
collision pattern, the CLI emits to `snapshots/lrs-<date>/` and
same-day snapshots collide, so the snapshot was renamed post-
write (W5–W11 all renamed). W4 state remains recoverable from
commit `61923fd`.

### Standing items (carry forward)

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain`):
  still deferred. Prior breakage uncaptured ([[project_hierarchy_bypass_prior_breakage]]).
- **LRS-side parser-extension backlog** — now **56 active raw-
  unparsed** (was 45 post-W10):
  - T13 (12), T38 (11 — 6 in single `emerg. eff. June 14, 1971`
    cluster), T47 (18), T39 (8), T49 (3), T42 (2), T23 (1),
    T22 (1)
  - Highest-fix-leverage single regex: `emerg. eff. <Month Day,
    Year>.` — closes 6 T38 + 1 T38 composite + 1 T13 + 1 T39 =
    **9 sections from one diagnostic** (one of the densest
    fix-yield ratios in the backlog).
  - Highest-cumulative family: `Ex.Sess.` (no-space) at 21
    cross-Title instances (T13 added 5 in W10).
- **Wave 12 candidates** (smallest first):
  - Title 33 (~2,500, municipalities — third Subtitle wave;
    T39 emits 7, T13 emits 46, T38 emits 13 into T33; combined
    ~66-edge inbound-closure).
  - Title 40 (T13 emits 49 into T40 alone — heaviest single-
    source inbound-closure prediction yet; T38 emits 7 also).
  - Title 17 (~3,500, education — T42 emits 15, T39 emits 29,
    T13 emits 13 into T17; combined ~57-edge inbound-closure).
  - Title 18 (election code — T42 emitted 23 into T18 in W9;
    likely the heaviest single-edge target from T42).

## 2026-05-22 — Wave 10 (Title 13 Courts and Judicial Procedure) end-to-end

Tenth wave. Title 13 lands clean with **zero source-code edits**
(seventh consecutive pure data-pipeline wave). Four notable
features distinguish W10:

- **Fourth consecutive exact inbound-closure wave — now across
  eight sources simultaneously.** Pre-W10 the corpus carried 145
  unresolved-but-recorded edges into T13 from the prior nine
  titles. Post-W10: all eight source-title totals match exactly
  (T9=33, T47=31, T14=23, T39=23, T22=14, T49=9, T23=7, T42=5;
  sum 145=145, every source perfectly matched). The cross-corpus
  citation resolver has now landed exact predictions across
  W7/W8/W9/W10 (15 + 22 + 16 + 24 + 22 + 145-from-8-sources = 244
  cross-wave edges all correctly predicted).
- **Heaviest non-`rs` outbound of any wave.** T13 source edges
  break down `rs` 1,094 / `ccp` 94 / `crp` 22 / `civcode` 14 /
  `evidence` 11 — **141 cross-corpus edges**, by far the most of
  any single Title. This validates the briefing's prediction
  that T13 (judicial procedure) would be a civcode/ccp cross-ref
  exercise. For scale: T42 (W9) had 9 civcode + 0 ccp; T13 has
  14 civcode + 94 ccp + 22 crp + 11 evidence — judicial procedure
  references the procedural and evidentiary codes much more
  heavily than substantive titles do.
- **Largest single section landed in any wave.** R.S. 13:5554
  (Group insurance; kinds; amounts; subrogation) = **72,326 b
  with 81 acts records** — surpassing the prior champion R.S.
  42:1123 (Code of Ethics Exceptions, 44,268 b). T13 carries
  several big sections in the public-retirement / state-liability
  / specialty-court chapters.
- **First wave to nudge corpus parse rate UP.** Corpus active
  parse rate edged **82.88%** (up from W9's 82.86%, +0.02 pts).
  T13's own rate of 82.96% sits slightly above the pre-W10 corpus
  rate, so the addition is mildly accretive — the opposite of
  W9's structural drag.

### Scope

- **2,033 sections** in `data/rs/sections/rs_13_*.json` — **the
  largest wave so far**, 4.4× W9's T42 (458). Status: **1,807
  active + 221 repealed + 5 blank**.
- T13 has no Subtitles (depth-4 max profile, same shape as
  T14/T22/T23/T42/T49). The `hierarchy.json` containers built by
  phase4 produce 321 sections at depth 2 (chapter-direct), 1,191
  at depth 3 (chapter/part — the modal shape), 521 at depth 4
  (chapter/part/subpart).
- Tree `max_depth = 7` (unchanged corpus-wide). T13's deepest
  example: R.S. 13:581 → Title 13 › Chapter 4 › Part III ›
  Subpart A.

### Process

1. `.venv/bin/usufruct rs phase3 --titles 1,9,14,22,47,23,49,39,42,13` —
   symmetric form, 9 cached titles short-circuited, T13 fetched
   fresh. Wall time ~17 min for the 2,033 fresh fetches at 2 req/s
   (the longest single wave fetch yet).
2. `.venv/bin/usufruct rs phase4` — rebuilt
   tree/edges/chunks/markdown across the 13,709-section union.
3. Verification via `lars-test/wave10_verify.py` (new, adapted
   from wave9_verify.py): per-source inbound prediction check
   across all 8 prior-processed titles vs the captured baseline.
4. `.venv/bin/pytest -q` → **169 passed, zero regressions.**

### Output deltas

| Metric | Pre-Wave 10 (W1–9) | Post-Wave 10 | Δ |
| --- | --- | --- | --- |
| Sections emitted | 11,676 | 13,709 | +2,033 |
| Active / Repealed / Blank | 10,189 / 1,464 / 23 | 11,996 / 1,685 / 28 | +1,807 / +221 / +5 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (T13 already in hierarchy.json) |
| Tree max depth | 7 | 7 | 0 |
| ActsCitation records | 22,530 | 26,427 | +3,897 |
| Citation edges | 10,033 | 11,268 | +1,235 |
| RAG chunks | 9,287 | 11,034 | +1,747 |
| Markdown files | 11,676 | 13,709 | +2,033 |
| Pytest passing | 169 | 169 | 0 |

`validation_report.sections_without_hierarchy = 0` (clean).
`in_section_index_but_unemitted = 31,787` (down from 33,820 by
exactly 2,033 — the T13 delta).

### Cross-corpus inbound validation — eight exact matches in one wave

| Source | Briefing prediction | Observed |
| --- | --- | --- |
| T9 → T13 | 33 | **33** ✓ |
| T47 → T13 | 31 | **31** ✓ |
| T14 → T13 | 23 | **23** ✓ |
| T39 → T13 | 23 | **23** ✓ |
| T22 → T13 | 14 | **14** ✓ |
| T49 → T13 | 9 | **9** ✓ |
| T23 → T13 | 7 | **7** ✓ |
| T42 → T13 | 5 | **5** ✓ |
| **Sum** | **145** | **145** ✓ |

The prior wave-by-wave closures (W7 T23/T47→T49 = 37, W8 T49→T39 =
16, W9 T49+T39→T42 = 46) targeted at most two source titles per
test. W10's single-shot eight-way exact match is the strongest
evidence yet that the resolver doesn't lose accuracy as more
sources are folded in.

### Title 13 citation-edge breakdown

1,235 edges from Title 13 sources (~0.61/section — same range
as T39 0.63 and T49 0.71). By destination corpus:

| Dst corpus | Count |
| --- | --- |
| `rs` (intra-LRS) | 1,094 |
| `ccp` | 94 |
| `crp` | 22 |
| `civcode` | 14 |
| `evidence` | 11 |

**141 non-rs edges** — judicial procedure rightly anchors itself
to the procedural/evidentiary codes much more than substantive
titles. Top intra-LRS destinations: T13 self=666 (54% self-ref),
T14=90 (criminal law — heavy proc cross-refs), T40=49 (public
health), T33=46 (municipalities), T15=30 (crim proc — paired
with the 22 to `crp`), T46=22 (welfare), T9=22 (civil-code
ancillaries), T47=22, T44=18 (clerks of court), T32=15, T22=14,
T17=13.

### Acts parsing: 12 active raw-unparsed (six families, four new)

**Active parse rate 1,499 / 1,807 = 82.96%**. T13 sits in the
middle of the per-title distribution (above T9 82.05%, below
T23 88.15%). 296 active sections legitimately have no acts-line
(`acts_citations_raw is null` — 16.4% of T13 active).

Of the 12 active raw-unparsed, **6 fold into existing
backlog families** and **6 surface NEW families**:

| Family | Count this wave | Cumulative cross-Title | Example |
| --- | --- | --- | --- |
| **`Ex.Sess.` (no-space)** — existing top-backlog family | 5 | 21 | R.S. 13:621.6 / 621.8 / 621.20 / 621.28 / 1597 |
| **`eff. See Act`** — existing (was T22:1059.7 only) | 1 | 2 | R.S. 13:2623 (`Acts 2025, No. 155, §1, eff. See Act.`) |
| **`emerg. eff.` + trailing time-of-day** — promoted from one-off | 1 | 2 | R.S. 13:2488.2 (`emerg. eff. July 4, 1968, at 10:05 A.M.`) |
| **`operative` (vs `eff.`)** — NEW family | 1 | 1 | R.S. 13:312.2 (`§1, operative Aug. 1, 1975.`) |
| **`§N(A)` parenthesized subsection** — NEW family | 1 | 1 | R.S. 13:621.12 (`§2(A), eff. June 8, 1984.`) |
| **`Act No.` (instead of `No.`)** — NEW family | 1 | 1 | R.S. 13:2087 (`Acts 1983, Act No. 442, §1, ...`) |
| **Typo `No,` (comma where period belongs)** — NEW data quality | 1 | 1 | R.S. 13:2562.3 (`Acts 1966, No, 5, §3, ...`) |
| **Trailing comma (truncated acts-line)** — NEW data quality | 1 | 1 | R.S. 13:5727 (`Acts 2022, No. 403, §1,`) |

Two of the new families look like data-quality issues at the
legis source (the typo `No,` and the trailing-comma truncation),
not parser gaps — those would survive a corpus-wide CC-touch
unchanged but might be patched by an LRS-side reparse layer
(see [[feedback_cc_touch_budget_strategy]] for the architectural
clarification).

**Corpus active parse rate**: 82.86% → **82.88%** (+0.02 pts —
**first wave to lift corpus parse rate** rather than depress it).
**Per-title active parse rates**: T1 53.66%, T9 82.05%, T13
82.96%, T14 94.20%, T22 69.22%, T23 88.15%, T39 90.58%, T42
77.07%, T47 90.04%, T49 90.70%.

**No CC-touch budget consumed this wave.** The 12 T13 raw-
unparsed are deferred and fold into the (now reframed) LRS-
side parser-extension backlog.

### Marquee spot-checks

| Citation | Heading | Depth | Text | Acts records | Breadcrumb |
| --- | --- | --- | --- | --- | --- |
| R.S. 13:1 | Duties of the minute clerks of courts of Orleans Parish | 3 | 530 b | 2 | Title 13 › Chapter 1 › Part I |
| R.S. 13:42 | Judicial Compensation Commission; creation; membership | 2 | 924 b | 2 | Title 13 › Chapter 1-B |
| R.S. 13:581 | Judge's powers at chambers | 4 | 97 b | 0 | Title 13 › Chapter 4 › Part III › Subpart A |
| R.S. 13:961 | Court reporters; generally | 3 | 16,959 b | 23 | Title 13 › Chapter 4 › Part V (heaviest single-source inbound: 33 from T9) |
| R.S. 13:4202 | Rates of judicial interest | 3 | 3,263 b | 4 | Title 13 › Chapter 23 › Part I (heavily cross-cited — 28 inbound) |
| **R.S. 13:5554** | **Group insurance; kinds; amounts; subrogation** | 3 | **72,326 b** | **81** | Title 13 › Chapter 35 › Part I (heaviest single section corpus-wide) |
| R.S. 13:5366 | The Veterans Court program | 2 | 21,393 b | 2 | Title 13 › Chapter 33-B (specialty-court program) |
| R.S. 13:2623 | Territorial jurisdiction; Iberville Parish justice of the peace courts | 3 | 1,134 b | 0 | Title 13 › Chapter 9 › Part II (RAW-UNPARSED — `eff. See Act`) |
| R.S. 13:621.6 | Sixth judicial district | 4 | 56 b | 0 | Title 13 › Chapter 4 › Part III › Subpart B (RAW-UNPARSED — `Ex.Sess.` no-space, 5 of T13's 12 are this family) |

R.S. 13:5554 is the new heaviest single section corpus-wide (72KB
body, 81 acts records — a state-employee group insurance statute
with decades of amendment history; the prior heavyweight R.S.
42:1123 was 44KB). R.S. 13:961 (Court reporters; generally) is
the most-inbound T13 section at 33 inbound edges — heavily
referenced from sibling clerks-of-court statutes.

### Tests

Test count unchanged at **169 passed** (87 CC + 82 LRS). No new
acts-parser tests, no new fixtures promoted.

### Source changes

**None.** Seventh consecutive wave with zero edits to any file
under `src/usufruct/`. Wave 10 is a pure data-pipeline rerun +
new verification helper.

Filesystem changes outside `data/rs/`:

| Path | Change |
| --- | --- |
| `lars-test/wave10_verify.py` | NEW (read-only verification harness with per-source inbound assertions, retained for reuse — gitignored under lars-test/) |
| `BUILDHISTORY.md` | This entry. |

### Snapshot

Cut to `snapshots/lrs-2026-05-22-w10/` (223 MB). Per the standing
collision pattern, the CLI emits to `snapshots/lrs-<date>/` and
same-day snapshots collide, so the snapshot was renamed post-
write (W5–W10 all renamed). W4 state remains recoverable from
commit `61923fd`.

### Standing items (carry forward)

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain`):
  still deferred. Prior breakage uncaptured ([[project_hierarchy_bypass_prior_breakage]]).
- **LRS-side parser-extension backlog** — now **45 active raw-
  unparsed** (was 33 post-W9):
  - T13 (12 in 8 families — 5 to existing Ex.Sess. no-space + 1
    to eff. See Act + 1 to emerg.eff. + 6 NEW families)
  - T47 (18 in 5 sub-families)
  - T39 (8 in 4 sub-families)
  - T49 (3 in 3 sub-families)
  - T42 (2 in 1 family — `Ex. Sess.` space form)
  - T23 (1 federal-code footnote)
  - T22 (1 `eff. See Act` — now 2 cross-Title with T13:2623)
  - Highest-fix-leverage family: `Ex.Sess.` (no-space) at 21
    cross-Title instances. Six NEW T13 families surfaced this
    wave (`operative`, `§N(A)` parenthesized, `Act No.`, typo
    `No,`, trailing-comma truncation, plus two data-quality
    issues that won't yield to regex alone).
- **Wave 11 candidates** (smallest first):
  - Title 38 (T39 emits 26 outbound into T38 + T47 emits some;
    inbound-closure candidate — would be **fifth consecutive**
    exact-match test).
  - Title 33 (~2,500, municipalities — third Subtitle wave;
    T39 emits 7, T13 emits 46 into T33).
  - Title 17 (~3,500, education — T42 emits 15, T39 emits 29,
    T13 emits 13 into T17; combined ~57-edge inbound-closure).
  - Title 40 (T13 emits 49 into T40 — heaviest T13 outbound
    after self; would be the largest single-source inbound-
    closure prediction yet).

## 2026-05-22 — Wave 9 (Title 42 Public Officers and Employees) end-to-end

Ninth wave. Title 42 lands clean with **zero source-code edits**
(sixth consecutive pure data-pipeline wave). Two unusual aspects
distinguish W9 from prior waves:

- **Three consecutive exact inbound-closure matches.** T49 → T42 =
  24 (predicted 24), T39 → T42 = 22 (predicted 22), combined 46
  (predicted 46). With W7 (T23/T47 → T49 = 15/22 exact) and W8
  (T49 → T39 = 16 exact), the inbound-closure prediction
  methodology has now landed exact three waves in a row.
- **First wave to drag corpus parse rate down.** T42 has the
  third-lowest individual-title active parse rate (77.07% — below
  T9 / T23 / T47 / T49 / T39 which all sit 82–94%). Corpus
  active parse rate edged **82.86%** (down 0.25 pts from W8's
  83.11%). The drag is structural, not regression — see the parse
  analysis below.

### Scope

- **458 sections** in `data/rs/sections/rs_42_*.json` — **smallest
  wave so far**, even under W7 T49's 598. Status: **410 active +
  48 repealed + 0 blank**.
- **75 containers** for Title 42: 1 title + 0 subtitles + 34
  chapters + 34 parts + 6 subparts. No-Subtitle, depth-4 max
  profile (same shape family as T14 / T22 / T23 / T49).
  `hierarchy.json` total unchanged at 5,529.
- Tree `max_depth = 7` (unchanged corpus-wide). T42 depth
  distribution: 167 at d2 (chapter-direct, the modal shape), 243
  at d3 (chapter/part), 48 at d4 (chapter/part/subpart).

### Process

1. `.venv/bin/usufruct rs phase3 --titles 1,9,14,22,47,23,49,39,42` —
   symmetric form, 8 cached titles short-circuited, T42 fetched
   fresh. Wall time ~4 min for the 458 fresh fetches at 2 req/s.
2. `.venv/bin/usufruct rs phase4` — ~10s; rebuilt
   tree/edges/chunks/markdown across the 11,676-section union.
3. Verification via `lars-test/wave9_verify.py` (new, adapted from
   wave8_verify.py): spotlights both T49 and T39 inbound to T42
   plus their combined total against the briefing's 46.
4. `.venv/bin/pytest -q` → **169 passed, zero regressions.**

### Output deltas

| Metric | Pre-Wave 9 (W1–8) | Post-Wave 9 | Δ |
| --- | --- | --- | --- |
| Sections emitted | 11,218 | 11,676 | +458 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (T42 already in hierarchy.json) |
| Tree max depth | 7 | 7 | 0 |
| ActsCitation records | 21,481 | 22,530 | +1,049 |
| Citation edges | 9,670 | 10,033 | +363 |
| RAG chunks | 8,896 | 9,287 | +391 |
| Markdown files | 11,218 | 11,676 | +458 |
| Pytest passing | 169 | 169 | 0 |

`validation_report.sections_without_hierarchy = []` (clean).
`in_section_index_but_unemitted = 33,820` (down from 34,278 by
exactly 458 — the T42 delta).

### Cross-corpus inbound validation — three exact matches in a row

| Source | Target | Briefing prediction | Observed |
| --- | --- | --- | --- |
| T49 → T42 | 42:11, 42:1123, 42:1157, 42:1170 etc. | 24 | **24** ✓ |
| T39 → T42 | 42:11, 42:375.2 etc. | 22 | **22** ✓ |
| **T49 + T39 → T42** | combined | **46** | **46** ✓ |

Also surfaced: T22 → T42 = 12, T47 → T42 = 12, T23 → T42 = 10, T9
→ T42 = 4 — **84 total cross-Title inbound edges closed** by
emitting T42, the heaviest single-wave cross-corpus closure yet.
The bulk of the inbound traffic targets the Code of Governmental
Ethics chapter (Chapter 15 — R.S. 42:1123 Exceptions, R.S.
42:1157 Late filing fees, R.S. 42:1170 Ethics education), which
naturally is what state-admin (T49) and state-finance (T39)
statutes cross-reference for officer conduct rules.

### Title 42 citation-edge breakdown

363 edges from Title 42 sources (~0.79/section — back up to the
T22/T23 range, after the T39/T49 trough of 0.63–0.71). By
destination corpus:

| Dst corpus | Count |
| --- | --- |
| `rs` (intra-LRS) | 354 |
| `civcode` | 9 |
| `ccp` | 0 |
| `crp` | 0 |
| `evidence` | 0 |

Top intra-LRS destinations: T42 self=210 (57.8% self-ref), T18=23
(election code — natural neighbor for "public officers"), T49=17
(state admin, bidirectional with T49's 24 inbound), T17=15
(education), T24=11, T39=8 (also bidirectional, T39 sent 22),
T40=8, T14=8, T33=7, T23=5, T13=5, T37=5. T42's 9 outbound civcode
edges are the most civcode-rich Title yet — public-officer
statutes reference Civil Code articles on bonds, fiduciary duty,
etc.

### Acts parsing: 2 active raw-unparsed (`Ex. Sess.` with space)

**Active parse rate 316 / 410 = 77.07%**. This is the third-
lowest single-title rate (above T22 69.22% and T1 53.66% — T1
being too small to compare). The driver is **NOT parse failures**:

- **92 sections with no acts-line at all** (`acts_citations_raw is
  null`) — **22.4% of T42 active sections**, much higher than
  other titles (T22 30.8%, T9 17.9%, others <10%). These are
  legitimate no-raw cases; the legis page carries body only. T42
  has many old-statute public-officer rules whose acts-line history
  doesn't appear on the legis page.
- **316 / 318 = 99.37% parse rate when raw text IS present** —
  highest "raw → parsed" rate of any wave.
- **2 sections with raw text that didn't parse**, both with the
  same surface text:

| Family | Count this wave | Cumulative cross-Title | Example |
| --- | --- | --- | --- |
| **`Ex. Sess.` (with space) bare form** — close cousin of the existing should-parse-but-don't `Ex.Sess.` (no space) family; this is the space-variant | 2 | 2 (new sub-family) | R.S. 42:1351 / 42:1358 (Code of Ethics public-records statutes): `Added by Acts 1974, Ex. Sess., No. 8, §1, eff. Jan. 1, 1975.` |

A corpus-wide check turned up an instructive contrast in special-
session variants:

| Variant | Active sections with this text | Parses cleanly | Failure rate |
| --- | --- | --- | --- |
| `Ex. Sess.` **WITH** space | 793 | **791** | 0.25% |
| `Ex.Sess.` **NO** space | 136 | 120 | 11.8% |

So the space form (`Ex. Sess.`) is overwhelmingly the normalized
one and parses 99.75% of the time; the no-space form (`Ex.Sess.`)
is the rarer, problematic one with a 11.8% failure rate. **The
T42 pair are the only 2 exceptions to the space-form pattern**
corpus-wide — narrow, specific. The 16 `Ex.Sess.` (no-space)
non-parsers cover most of the consolidated should-parse-but-don't
backlog plus the redesignated-pursuant-to family and the
1st.Ex.Sess. variant.

**Corpus active parse rate**: 83.11% → **82.86%** (-0.25 pts —
**first wave to drag corpus parse rate down**; structural cause
is T42's no-raw-line proportion, not parser regression).
**Per-title active parse rates**: T1 53.66%, T9 82.05%, T14 94.20%,
T22 69.22%, T23 88.15%, T39 90.58%, T42 77.07%, T47 90.04%, T49
90.70%.

**No CC-touch budget consumed this wave.** Per the standing rule,
the 2 T42 raw-unparsed are deferred and folded into the
consolidated escalation backlog.

### Marquee spot-checks

| Citation | Heading | Depth | Body | Acts records | Breadcrumb |
| --- | --- | --- | --- | --- | --- |
| R.S. 42:1 | Public office defined | 2 | 359 b | 0 | Title 42 › Chapter 1 |
| R.S. 42:11 | Short title | 2 | 72 b | 1 | Title 42 › Chapter 1-A |
| R.S. 42:31 | Eligibility requirements for certain unclassified employees | 3 | 1,560 b | 1 | Title 42 › Chapter 2 › Part I |
| R.S. 42:375.2 | Agency attrition analysis process, higher education systems | 2 | 2,360 b | 1 | Title 42 › Chapter 7 |
| R.S. 42:801 | Administration | 4 | 579 b | 2 | Title 42 › Chapter 12 › Part I › Subpart A |
| **R.S. 42:1123** | **Exceptions** | 3 | **44,268 b** | **87** | Title 42 › Chapter 15 › Part II (Code of Governmental Ethics — heaviest inbound target) |
| R.S. 42:1157 | Late filing fees | 4 | 4,889 b | 17 | Title 42 › Chapter 15 › Part III › Subpart C |
| R.S. 42:1170 | Ethics education; mandatory requirements; ethics designee | 3 | 8,355 b | 8 | Title 42 › Chapter 15 › Part IV |
| R.S. 42:1351 | Qualifications; term of office | 3 | 392 b | 0 | Title 42 › Chapter 19 › Part I (RAW-UNPARSED — `Ex. Sess.` space form) |
| R.S. 42:1358 | Contest of election | 3 | 196 b | 0 | Title 42 › Chapter 19 › Part I (RAW-UNPARSED — same pattern as 42:1351) |

R.S. 42:1123 is the heaviest single section landed in any wave so
far (44KB body, 87 acts records) — the "Exceptions" section in
the Code of Governmental Ethics carrying decades of amendments.
R.S. 42:11 (a 72-byte "Short title" section) is the most-cited
inbound destination for both T39 and T49 — short-title sections
are typical citation anchors.

### Tests

Test count unchanged at **169 passed** (87 CC + 82 LRS). No new
acts-parser tests, no new fixtures promoted.

### Source changes

**None.** Sixth consecutive wave with zero edits to any file
under `src/usufruct/`. Wave 9 is a pure data-pipeline rerun + new
verification helper.

Filesystem changes outside `data/rs/`:

| Path | Change |
| --- | --- |
| `lars-test/wave9_verify.py` | NEW (read-only verification harness, retained for reuse — gitignored under lars-test/) |
| `BUILDHISTORY.md` | This entry. |

### Snapshot

Cut to `snapshots/lrs-2026-05-22-w9/`. Per the standing collision
pattern, the CLI emits to `snapshots/lrs-<date>/` and same-day
snapshots collide, so the snapshot was renamed post-write (W5–W9
all renamed). W4 state remains recoverable from commit `61923fd`.

### CC-touch budget — escalation now on deck

The standing rule was "accumulate parser gaps across W7/W8/W9,
then do one consolidated CC-touch escalation." We've now done
W7, W8, W9. The consolidated backlog stands at **33 active raw-
unparsed across ~10 pattern families**, with the highest-yield
single family being the **`Ex.Sess.` (no-space) should-parse-but-
don't** group at **16 instances cross-Title** (47:813, 47:641,
47:633.2, 47:338.87, 47:338.92–96, 39:1361, 39:1363, 39:1404,
39:1406, 39:1422, 39:1425, 49:211) — note that several of those
are caught up in adjacent families (`Redesignated`, `1st`-prefix,
trailing-footnote) so the actual diagnostic-first scope is the 6
bare-form cases (47:813, 47:641, 39:1361, 39:1363, 39:1404,
39:1406).

The 2 T42 `Ex. Sess.` (space-form) failures are a separate
narrow family and likely fix with the same diagnostic.

This is the natural next decision point: **continue waves
(W10 = T13 or T38 or T33)** OR **spend the third CC-touch on
the consolidated escalation now**. Recommend: one more wave (T13
is small and exercises civcode/ccp cross-refs, broadening
coverage) before the escalation, to maximize the value of a
single diagnostic-first session against the largest possible
backlog. But the call is the user's.

### Standing items (carry forward)

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain`):
  still deferred. Prior breakage uncaptured.
- **Consolidated CC-touch backlog** — now **33 active raw-
  unparsed**:
  - T47 (18 in 5 sub-families)
  - T39 (8 in 4 sub-families)
  - T49 (3 in 3 sub-families)
  - T42 (2 in 1 new sub-family — `Ex. Sess.` space form)
  - T23 (1 federal-code footnote)
  - T22 (1 `eff. See Act`)
- **Wave 10 candidates** (smallest first):
  - Title 13 (~1,500, courts & judicial procedure — no Subtitles,
    the perennial "next small no-edit rerun"). Heavy civcode/ccp
    cross-refs likely.
  - Title 38 (T39 emits 26 outbound into T38; inbound-closure
    candidate — would be **fourth consecutive** exact-match test).
  - Title 33 (~2,500, municipalities — third Subtitle wave; T39
    emits 7 into T33).
  - Title 17 (~3,500, education — T42 emits 15, T39 emits 29 into
    T17; combined 44-edge inbound-closure candidate).

## 2026-05-22 — Wave 8 (Title 39 State Finance) end-to-end

Eighth wave. Title 39 lands clean with **zero source-code edits**
(fifth consecutive pure data-pipeline wave). Two structural notes
matter for this wave:

- **T39 has 3 Subtitles** — I did not flag this when recommending
  T39 over T13. Since the Subtitle code was added and hardened in
  W5 (T47), no new code was needed, but T39 effectively
  re-validates the W5 Subtitle path on a second corpus. **100%
  Subtitle penetration**: all 1,355 T39 sections sit under a
  Subtitle (compare T47 where Subtitles were partial), so this is
  the strongest possible re-exercise of that path. The path held.
- **T49 → T39 inbound edges = 16, matching the briefing prediction
  exactly** — second consecutive wave where the
  inbound-closure-prediction validation came in exact (W7 was
  T23 → T49 = 15 and T47 → T49 = 22).

### Scope

- **1,355 sections** in `data/rs/sections/rs_39_*.json` (handoff
  estimated ~1,500; actual `section_index.json` count was 1,355).
  Status: **1,051 active + 301 repealed + 3 blank**.
- **225 containers** for Title 39: 1 title + **3 subtitles** + 48
  chapters + 60 parts + 113 subparts. Same structural shape as
  Title 47 (W5) — three-Subtitle shape. `hierarchy.json` total
  unchanged at 5,529.
- Tree `max_depth = 7` (unchanged corpus-wide). T39 depth
  distribution: **361 at depth 3** (title/subtitle/chapter), **378
  at depth 4** (title/subtitle/chapter/part), **616 at depth 5**
  (title/subtitle/chapter/part/subpart). Depth-5 is the modal
  T39 shape — T39 is the deepest-skewed Title in the corpus so
  far. The deepest individual section, R.S. 39:1 (Division of
  administration), lives at depth 5 in `Title 39 › Subtitle I ›
  Chapter 1 › Part I › Subpart A`.

### Process

1. `.venv/bin/usufruct rs phase3 --titles 1,9,14,22,47,23,49,39` —
   symmetric form, 7 cached titles short-circuited, T39 fetched
   fresh. Wall time ~11 min for the 1,355 fresh fetches at 2
   req/s.
2. `.venv/bin/usufruct rs phase4` — ~10s; rebuilt
   tree/edges/chunks/markdown across the 11,218-section union.
3. Verification via `lars-test/wave8_verify.py` (new, adapted from
   wave7_verify.py — corrected manifest key nesting, added the
   `sections_with_subtitle_in_chain` count, added an all-source
   cross-Title inbound-to-target ranking for context).
4. `.venv/bin/pytest -q` → **169 passed, zero regressions.**

### Output deltas

| Metric | Pre-Wave 8 (W1–7) | Post-Wave 8 | Δ |
| --- | --- | --- | --- |
| Sections emitted | 9,863 | 11,218 | +1,355 |
| Containers (hierarchy) | 5,529 | 5,529 | 0 (T39 already in hierarchy.json) |
| Tree max depth | 7 | 7 | 0 (T39's Subtitle depth matches T47's existing depth-5 ceiling) |
| ActsCitation records | 19,192 | 21,481 | +2,289 |
| Citation edges | 8,812 | 9,670 | +858 |
| RAG chunks | 7,848 | 8,896 | +1,048 |
| Markdown files | 9,863 | 11,218 | +1,355 |
| Pytest passing | 169 | 169 | 0 |

`validation_report.sections_without_hierarchy = []` (clean).
`in_section_index_but_unemitted = 34,278` (down from 35,633 by
exactly 1,355 — the T39 delta).

### Cross-corpus inbound validation — exact match (again)

| Source | Target | Briefing prediction | Observed |
| --- | --- | --- | --- |
| T49 → T39 | 39:11, 39:28, 39:33, 39:1403 etc. | 16 | **16** ✓ |

This is the second consecutive wave where the inbound-closure
prediction landed exact. Worth noting also the unbidden inbound
edges that surfaced: T47 → T39 = **25** (the heaviest inbound
source, larger than T49 — natural since revenue/tax sits beside
state finance), T22 → T39 = 7, T9 → T39 = 4, T23 → T39 = 3.

### Title 39 citation-edge breakdown

858 edges from Title 39 sources (~0.63/section — **new lowest
density** of any wave, just under T49's 0.71). By destination
corpus:

| Dst corpus | Count |
| --- | --- |
| `rs` (intra-LRS) | 855 |
| `ccp` | 2 |
| `civcode` | 1 |
| `crp` | 0 |
| `evidence` | 0 |

Top intra-LRS destinations: T39 self=477 (55.7% self-ref — in
family with T49 53.9%; T39 reaches broadly), **T47=48** (revenue
— natural finance↔tax pairing), **T49=36** (state admin —
bidirectional with T49's 16 inbound), T24=32 (industrial loans),
T17=29 (education), T38=26 (banking), T13=23 (courts), T42=22
(public officers), T40=17, T51=13, T23=10, T28=9. T39 → T38
(banking) is a new significant cross-corpus signal — when T38 is
emitted, that's a 26-edge inbound channel to close.

### Acts parsing: 8 active raw-unparsed (4 new families + existing should-parse-but-don't grows)

**Active parse rate 952 / 1,051 = 90.58%**, in family with T47
90.04%, T49 90.70%, T23 88.15%. The gap breaks down as:

- **91 sections with no acts-line at all** (`acts_citations_raw is
  null`) — legitimate; the legis page carries body only. Not a
  parse failure.
- **8 sections with raw text that didn't parse** — 4 of them
  expand an existing family (now important for diagnostic-first
  sizing); 4 are new pattern variants:

| Family | Count this wave | Cumulative | Example |
| --- | --- | --- | --- |
| **Should-parse-but-don't** (`[Added by ]Acts YYYY, Ex.Sess., No. N, §M.`) — **existing family grows from 3 to 7 instances** | 4 | 7 | R.S. 39:1361 / 39:1363 / 39:1404 / 39:1406. Same surface shape as the previously-tracked T47 trio (47:813 / 47:1542.1 / 47:641). The W6 standing items called this family out as **diagnostic-before-fix**; this wave doubles+ the population and substantially raises the priority. |
| Trailing asterisk footnote (`*As appears in enrolled bill.`) | 1 | 1 | R.S. 39:330.2: `Acts 1984, No. 618, §1. *As appears in enrolled bill.` — similar in spirit to the T47 inline asterisk family but with a different anchor (`*As appears` rather than inline `. *`). |
| `emerg. eff.` (emergency-effective date variant) | 1 | 1 | R.S. 39:991.1: `Added by Acts 1976, No. 503, §1, emerg. eff. Aug. 1, 1976.` — `emerg. eff.` instead of plain `eff.`. |
| `1st.Ex.Sess.` (literal period after `1st`) | 2 | 2 | R.S. 39:1422 / 39:1425: `Acts 1975, 1st.Ex.Sess., No. 19, §N, ...` — narrowly distinct from T49:211's `1st Ex.Sess.` (no period) but probably one regex fix covers both. |

**Corpus active parse rate**: 82.21% → **83.11%** (+0.90 points,
largest single-wave jump since W6).
**Per-title active parse rates**: T1 53.66%, T9 82.05%, T14
94.20%, T22 69.22%, T23 88.15%, T39 90.58%, T47 90.04%, T49 90.70%.

**No CC-touch budget consumed this wave.** Per the standing rule,
the 8 T39 raw-unparsed are deferred and folded into the
consolidated escalation backlog.

#### Correction to W6/W7 backlog summary

While verifying T39's raw-unparsed, I checked a previous standing
item — the W6 entry listed `eff. See Act family (R.S. 22:1059.7 /
R.S. 1:60)`. R.S. 1:60's raw text is
`Acts 1999, No. 175, §1, eff. June 9, 1999; Acts 2001, No. 451, §6,
eff. Jan. 12, 2004.` — both citations have explicit eff. dates
and both parse fine (2 records). **R.S. 1:60 is not in the `eff.
See Act` family**; the W6 standing item entry was wrong on that
point. The `eff. See Act` family has just **1** instance corpus-
wide (R.S. 22:1059.7).

### Marquee spot-checks

| Citation | Heading | Depth | Body | Acts records | Breadcrumb |
| --- | --- | --- | --- | --- | --- |
| R.S. 39:1 | Division of administration | 5 | 200 b | 3 | Title 39 › Subtitle I › Chapter 1 › Part I › Subpart A |
| R.S. 39:11 | Authority | 5 | 687 b | 1 | Title 39 › Subtitle I › Chapter 1 › Part I › Subpart B |
| R.S. 39:33 | Agency budget request; time of submission; standing committees | 5 | 3,691 b | 8 | Title 39 › Subtitle I › Chapter 1 › Part II › Subpart A |
| R.S. 39:131 | Statement of purpose | 4 | 255 b | 1 | Title 39 › Subtitle I › Chapter 1 › Part IV |
| R.S. 39:371 | Cash management review board; creation | 3 | 278 b | 3 | Title 39 › Subtitle I › Chapter 1-A |
| R.S. 39:1403 | All other state bonds | 4 | 3,157 b | 5 | Title 39 › Subtitle III › Chapter 11 › Part I |
| R.S. 39:991.1 | Authorization to issue revenue bonds; South Louisiana Port Commission | 4 | 2,525 b | 0 | Title 39 › Subtitle II › Chapter 4 › Part XII (RAW-UNPARSED — `emerg. eff.`) |
| R.S. 39:1422 | Legislative intent | 3 | 342 b | 0 | Title 39 › Subtitle III › Chapter 13 (RAW-UNPARSED — `1st.Ex.Sess.`) |

R.S. 39:33 is one of the two heaviest T49-inbound destinations
(the other is 39:1403). Both resolve cleanly with full body text.
R.S. 39:11 also accepts inbound from T49. Together these are the
core of the T49 → T39 closure path that was the W8 hypothesis.

### Tests

Test count unchanged at **169 passed** (87 CC + 82 LRS). No new
acts-parser tests, no new fixtures promoted. T39's Justia parse
is unremarkable (no new structural features — the Subtitle handling
that runs is the same code path tested via T47 fixtures).

### Source changes

**None.** Fifth consecutive wave with zero edits to any file
under `src/usufruct/`. Wave 8 is a pure data-pipeline rerun +
new verification helper.

Filesystem changes outside `data/rs/`:

| Path | Change |
| --- | --- |
| `lars-test/wave8_verify.py` | NEW (read-only verification harness, retained for reuse — gitignored under lars-test/) |
| `BUILDHISTORY.md` | This entry. |

### Snapshot

Cut to `snapshots/lrs-2026-05-22-w8/`. Per the standing collision
pattern, the CLI emits to `snapshots/lrs-<date>/` and same-day
snapshots collide, so the snapshot was renamed post-write (W5 =
`lrs-2026-05-22-w5/`, W6 = `lrs-2026-05-22-w6/`, W7 =
`lrs-2026-05-22-w7/`, W8 = `lrs-2026-05-22-w8/`). W4 state
remains recoverable from commit `61923fd`.

### Standing items (carry forward)

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain`
  in orchestrate.py): still deferred. Prior session broke
  something attempting this cleanup; root cause was never
  captured. Don't touch without reconstructing the breakage.
- **Consolidated CC-touch backlog** — now **31 active raw-
  unparsed across 11 pattern families** (still deferred; one
  escalation will fix all of these together once sized):
  - T47 (18 in 5 sub-families): `applicable to taxable years`
    (4); `; Redesignated from R.S. X:Y pursuant to Acts ...` (6);
    trailing `H.C.R. No. N, YYYY R.S.` (2); inline asterisk
    footnote (3); should-parse-but-don't trio R.S. 47:813 /
    47:1542.1 / 47:641 (3, joins family below).
  - **Should-parse-but-don't `[Added by] Acts YYYY, Ex.Sess., No.
    N, §M.`** (cross-Title family — now 7 instances: 3 T47 + 4
    T39): R.S. 47:813, 47:1542.1, 47:641, 39:1361, 39:1363,
    39:1404, 39:1406. **Needs diagnostic-first** — trace why
    `parse_acts_citation_line` returns 0 records on this exact
    shape when `Ex.Sess.` parses 76/87 times elsewhere. With 7
    instances now, this is the highest-yield single family in
    the backlog.
  - T23 (1): trailing federal-code footnote tail in R.S. 23:1491.
  - T49 (3): multi-section in one Acts line (49:157); compound
    enactment with comma separator (49:159); `1st Ex.Sess.`
    ordinal-prefixed special session (49:211).
  - T39 (4 new this wave, distinct from the should-parse-but-
    don't family): trailing asterisk footnote (39:330.2); `emerg.
    eff.` (39:991.1); `1st.Ex.Sess.` with literal period
    (39:1422, 39:1425 — close cousin of T49:211's `1st Ex.Sess.`,
    probably one regex change covers both).
  - `eff. See Act` (1 instance, R.S. 22:1059.7) — **corrected**
    from W6's listing of "R.S. 22:1059.7 / R.S. 1:60"; R.S. 1:60
    actually parses fine.
- **Wave 9 candidates** (smallest first):
  - Title 13 (~1,500, courts & judicial procedure — no Subtitles).
    The natural "6th consecutive no-edit rerun"; would re-exercise
    civcode/ccp cross-refs.
  - Title 38 (T39 emits 26 outbound into T38 — Banking and Finance
    Companies; inbound-closure candidate, third in a row).
  - Title 33 (~2,500, municipalities — third Subtitle wave; would
    further harden the Subtitle path on a third corpus).
  - Title 17 (~3,500, education — T39 emits 29 outbound into T17;
    medium-size inbound-closure candidate).
  - Title 42 (~unknown, public officers — T49 emitted 24 outbound
    into T42, T39 emits 22 outbound into T42; combined 46-edge
    inbound-closure candidate, the heaviest unresolved pair).

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

## 2026-05-22 — LRS-side reparse: 397/422 raw-unparsed sections closed (no CC-touch)

After corpus completion, the accumulated parser-extension backlog
(366 active raw-unparsed at corpus-completion time, regrown to 422
when the sample re-ran with current code) sat as the only
remaining headline gap. The handoff's architectural clarification
was crucial: the CC-touch budget rule applies to top-level
`src/usufruct/{parse,fetch,pipeline,model}/`, NOT to LRS-side
code. The right path was an LRS-side wrapper around CC's
`parse_acts_citation_line` that does aggressive LRS-specific
normalization and falls back to a tolerant per-piece regex when
CC declines. That is what shipped this session.

### Architecture

New file: `src/usufruct/lrs/parse/lrs_acts_parser.py`. The single
public entry point is `parse_lrs_acts_citation_line(raw)`:

1. **Normalize** (LRS-aggressive text rewrites — Phase A):
   - Strip trailing `at HH:MM A.M./P.M.` time-of-day, `Noon/Midnight`,
     `*AS APPEARS IN ENROLLED BILL`, `1 N U.S.C.A.` footnotes (any
     U.S.C. title — CC only matched "26"), `1 As (it) appears`,
     `1 So in / In subsec / Present R.S. / Former` editorial
     footnotes, `1 See / R.S. / LSA-Const / Acts YYYY...amended`
     digit-prefix footnotes, `, applicable to taxable years`,
     `, see Act for effective date [. NOTE: ...]`, `, see Act.`
     short form, `. H.C.R.` / `. S.C.R.` resolution refs,
     `. Redesignated from R.S. X pursuant to R.S. Y` reference
     clauses, `, pars. N, M` paragraph specs.
   - Rewrite period-separator pieces to semicolons: `. [Amended [by ] |
     Added by ] Acts? YYYY...` → `; Acts? YYYY...`, plus three
     specialized extractions for the trailing
     `Renumbered from R.S.[1950, ]§X:Y by Acts ...`,
     `Redesignated [from R.S. X:Y] by Acts ...`, and
     `Redesignated from R.S. X. See Acts ...` patterns. Also
     handles missing-period continuations (`§N Acts YYYY...`)
     and anchored-end bare-Redesignated strips.
2. **Split** on `;`.
3. **Per piece**: try CC's `parse_acts_citation_line` first
   (preserves CC behavior for the 99% case). If CC returns empty
   for the piece, try the LRS-tolerant regex.

The LRS-tolerant regex is a superset of CC's `_ACT_RE` with these
tolerances: no-ordinal `Ex.Sess.` / `Ex. Sess.` (including the
`E.S.` short variant), comma-after-ordinal (`1st,`), ordinal-with-
period (`1st.`), `2d` ordinal, comma-after-`No.` optional,
`Act No.` accepted, `No,` typo accepted, `operative DATE` and
`emerg. eff. DATE` as eff. synonyms, single `Act` vs plural `Acts`,
trailing-comma tolerance, alphanumeric §N suffix (`§2(A)`, `§8C`).
For plural-section specs (`§§...`), an LRS-side `_lrs_expand_section_spec`
accepts alphanumeric ids (`§§1, 3A` → integer prefixes `[1, 3]`,
letter suffixes preserved in `acts_citations_raw`).

Per-piece prefix stripping handles `Added by` / `Amended [by]` /
`Acquired from` / `Renumbered from R.S.[1950, ]§X:Y by` /
`Redesignated [from R.S. X:Y] by` continuations so the trailing
`Acts ...` parses on its own.

Per-piece tail cleanup handles the per-piece `1 N U.S.C.A.`
footnote that CC's parser misses, plus a trailing comma.

The "Acts " prefix synthesis (used when a piece doesn't begin with
"Acts") is now `re.match(r"^Acts?\b", s, re.IGNORECASE)`-aware so
singular `Act 1966` pieces aren't double-prefixed into the broken
`Acts Act 1966` form.

### CC-touch scope

**Zero.** No edits to `src/usufruct/{parse,fetch,pipeline,model}/`.
The CC parser, its regexes, its `_expand_section_spec`, and its
`parse_effective_date` are reused but unchanged. Phase 3 already
imported `parse_acts_citation_line` from `usufruct.parse`; that
import is replaced with `parse_lrs_acts_citation_line` from the
new LRS module. The legacy `_normalize_lrs_acts_text` in
orchestrate.py is retained as a thin re-export shim that delegates
to `lrs_acts_parser._normalize_acts_text` so the existing tests
in `tests/test_lrs_orchestrate.py` and `tests/test_lrs_legis_section.py`
keep importing the old symbol and the same behavior is preserved.

### New CLI subcommand

```sh
.venv/bin/usufruct rs reparse
```

Offline (no network). Reads `data/rs/sections.jsonl`, re-runs the
new parser on every active section where `acts_citations_raw`
exists but `acts_citations` is empty, writes back the updated
per-section JSONs + `sections.jsonl`. Prints
`candidates / closed / remaining / new_citation_records`.

`usufruct rs phase4` is run separately to refresh markdown
frontmatter (which embeds parsed acts), manifest, and validation
report. (`citation_edges.csv` and `chunks.jsonl` are unaffected —
those derive from section body text, not acts citations.)

### Closure measurement

Before reparse: 422 active sections with `acts_citations_raw`
but empty `acts_citations`. After: **25 remaining (94.1%
closure, 397 sections recovered, +473 new ActsCitation records)**.

Family-level breakdown (closed / original):

| Family                        | Closed | Original |
| ----------------------------- | -----: | -------: |
| Ex.Sess. (no-space + ord.)    |   ~117 |     ~122 |
| Renumbered / Redesignated     |   ~165 |     ~174 |
| emerg. eff.                   |     31 |       31 |
| federal-code (any U.S.C.A.)   |     20 |       20 |
| Ex. Sess. (with space)        |     21 |       22 |
| eff. See Act (long + short)   |     13 |       13 |
| operative (vs eff.)           |      9 |        9 |
| inline asterisk footnotes     |     14 |       14 |
| typos (No, / Act No.)         |      5 |        5 |
| trailing junk (HCR/SCR/pars.) |      4 |        4 |
| applicable to taxable years   |      4 |        4 |
| **Total**                     |**397** | **422**  |

The remaining 25 are deep edge cases: bare-date eff. without
keyword (`§1, June 19, 2002`), `eff,` typo, `special eff. date`
literal, double-§ (`§279, §4`), bare integer continuations
(`§1, 2.`), and editorial commentary that embeds plausible
`Acts ` references (`1 House Bill No. 295 and Senate Bill...`).
0.07% of the 33,418 active-with-raw sections.

### Corpus impact

- Active section parse rate: **98.74% → 99.93%** (33,393 / 33,418).
- Total `ActsCitation` records: 87,254 → **87,727** (+473).
- All structural counts unchanged: 45,774 sections, 5,529 containers,
  tree max_depth 7, 31,631 citation edges, 37,020 RAG chunks,
  validation report still clean (0 sections without hierarchy,
  0 unemitted from index).
- `bulk_verify.py` still reports **216/216 source-target pairs
  exact match, cumulative streak 238 predictions all exact**.

### Tests

`tests/test_lrs_acts_parser.py` (NEW): 26 tests, one per parser
family plus 4 transparent-delegation cases plus an idempotency
test on the normalizer. Total suite: **169 → 195 passing**.

### Files changed

| Path | Change |
| --- | --- |
| `src/usufruct/lrs/parse/lrs_acts_parser.py` | NEW — wrapper + normalize + LRS-tolerant fallback (~330 lines) |
| `src/usufruct/lrs/parse/__init__.py` | Export `parse_lrs_acts_citation_line` |
| `src/usufruct/lrs/pipeline/orchestrate.py` | Swap CC `parse_acts_citation_line` call for `parse_lrs_acts_citation_line`; add `run_reparse`; legacy `_normalize_lrs_acts_text` becomes a re-export shim |
| `src/usufruct/cli.py` | New `usufruct rs reparse` subcommand |
| `tests/test_lrs_acts_parser.py` | NEW — 26 unit tests |
| `data/rs/sections.jsonl` | Reparse output (gitignored; ~120 MB) |
| `data/rs/sections/*.json` | 397 per-section files updated by reparse |
| `data/rs/markdown/**/*.md` | Phase 4 refresh of frontmatter for the 397 |
| `data/rs/manifest.json`, `validation_report.json` | Phase 4 refresh |

### Standing items update

- **Drop Phase 3 bypass** (`_hierarchy_path_from_justia_chain`):
  still deferred (memory `hierarchy-bypass-prior-breakage` flags
  this as non-trivial despite the standing-items label).
- **Consolidated CC-touch backlog**: largely **OBSOLETED** by this
  session. The Ex.Sess. / `eff. See Act` / federal-footnote / typo
  families that were queued for CC-touch are all closed LRS-side.
  Any future CC-touch escalation would target only the 25
  remaining deep-edge sections (0.07% of active-with-raw) — a
  poor ROI; defer indefinitely.

### Snapshot

Not cut this session — the LRS structural data hasn't changed
(snapshots/lrs-2026-05-22-corpus-complete/ remains current for
the corpus shape). The acts-parsing improvements are visible in
the working `data/rs/` tree; a future snapshot will capture them.

