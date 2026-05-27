# theusufruct-site — LRS (Louisiana Revised Statutes) refactor plan

**Status:** Plan, not implementation. Draft 1, written 2026-05-27.

**Audience:** A future Claude Code session (or human) implementing the refactor
cold. Self-contained — should not require digging into prior conversation
transcripts.

**Repos involved:**

- This plan lives in **`bitwulf/Usufruct`** (the corpus repo at
  `/Users/d0j3/Dev/Usufruct`) but describes work that happens almost entirely
  in **`bitwulf/theusufruct-site`** (the site repo). A local clone of the site
  repo is at `/Users/d0j3/Dev/Usufruct/theusufruct-site/` for read-only
  reference; do NOT push site-side changes from there unless you've confirmed
  it has remote write access to bitwulf/theusufruct-site. The canonical
  development checkout for site work lives wherever you (the user) keep it.

---

## 1. Goal

The site (theusufruct.com) currently publishes only the **Louisiana Civil Code
(CC)** — 3,623 articles under `/cc/...`. The corpus release as of tag
`2026-05-22` now also includes the **Louisiana Revised Statutes (LRS)** — 45,774
sections — under an `rs/` subdirectory inside the same bundled zip. The site
currently ignores `rs/` entirely.

This refactor adds full LRS rendering to the site under `/rs/...`, making LRS
sections individually addressable and searchable on theusufruct.com.

End state:

- ~45,774 LRS section pages at `/rs/title-{N}/section-{X}`
- ~5,529 LRS container browse pages at `/rs/title-{N}/chapter-{Y}/...`
- Pagefind index covers both corpora; users can filter by corpus
- Site continues to render CC identically (no regression in `/cc/...`)
- Hosting cut over from Cloudflare Pages to Cloudflare R2 + Worker (necessary
  — Pages caps at 20,000 files per deploy; current build is 18,692 files,
  adding LRS pushes it to ~65,000)

---

## 2. Constraints (hard rules)

1. **Do not regress CC.** Every existing `/cc/{article}` URL must continue to
   render the same content. Existing CC tests / link integrity must not break.
2. **`/cc/{article_number}` is a stable forever-URL** per `BRIEF.md`. Do not
   change the CC URL scheme as a side effect of generalizing it.
3. **`fetch-corpus.sh` must keep working unchanged.** The bundled release zip
   was specifically designed to be invisible to it: CC at the root of
   `usufruct-<TAG>/`, LRS under `usufruct-<TAG>/rs/`. The script doesn't
   reach into `rs/` for validation, so no script change is needed.
4. **No new runtime tracking, no third-party fonts at runtime, no client-side
   analytics** (BRIEF.md non-negotiables — same rules apply to LRS pages).
5. **The corpus is read-only at build time.** Treat the unpacked corpus dir as
   immutable; do not write back into it.

---

## 3. Background: where the data lives

After `fetch-corpus.sh` runs, the corpus is unpacked at
`tmp/corpus/usufruct-<TAG>/`. As of 2026-05-22, the layout is:

```
tmp/corpus/usufruct-2026-05-22/
├── manifest.json              ← CC manifest; site already reads this
├── articles.jsonl             ← CC sections (3,623)
├── article_index.json         ← CC index
├── tree.json                  ← CC hierarchy tree
├── hierarchy.json             ← CC flat hierarchy
├── citation_edges.csv         ← CC cross-refs
├── validation_report.json
├── chunks.jsonl               ← (not used by site)
├── articles/                  ← CC per-article JSON
├── markdown/                  ← CC per-article markdown
└── rs/
    ├── manifest.json          ← LRS manifest (different shape; see §5)
    ├── sections.jsonl         ← LRS sections (45,774)
    ├── section_index.json     ← LRS index
    ├── tree.json              ← LRS hierarchy tree (different shape)
    ├── hierarchy.json         ← LRS flat hierarchy (different shape)
    ├── citation_edges.csv     ← LRS cross-refs
    ├── validation_report.json
    ├── sections/              ← LRS per-section JSON (45,774 flat files,
    │                            named `rs_<title>_<section>.json`)
    └── markdown/
        └── title-{N}/
            └── {section_number}.md   ← e.g. title-14/30.md
```

**`chunks.jsonl` is intentionally excluded** from the release (80 MB, not
needed for the site). If a future change wants RAG-side chunks, request a
follow-up corpus release with that file included.

---

## 4. URL layout decision: consistent with CC

**Chosen layout** (user-selected):

| Page kind | CC pattern | LRS pattern (parallel) |
|-----------|-----------|------------------------|
| Corpus root browse | `/cc` | `/rs` |
| Top-level container | `/cc/book-3` | `/rs/title-14` |
| Nested container | `/cc/book-3/title-5/chapter-3` | `/rs/title-14/chapter-1/part-2/subpart-a` |
| Leaf (citable) | `/cc/2315` (flat: article number) | `/rs/title-14/section-30` |

### Why this layout, and the tension with BRIEF

BRIEF.md states: *"Flat article URLs are non-negotiable. Articles get cited;
the URL must be stable forever and short."* For CC, that yielded `/cc/2315` —
article numbers are globally unique within CC, so the URL needs no
disambiguator.

LRS sections are NOT globally unique: section "30" exists in many titles
(`R.S. 14:30` ≠ `R.S. 16:30`). So a citable URL needs title + section. Three
options were considered:

- `/rs/14/30` — minimal, two bare numbers. Most BRIEF-aligned ("flat,
  short").
- `/rs/14:30` — colon mirrors the official citation form `R.S. 14:30`. URL-
  fiddly (colons require encoding in some contexts).
- `/rs/title-14/section-30` — level-prefixed, mirrors CC's container slug
  pattern (`book-N`, `title-N`, `chapter-N`). Verbose, but structurally
  consistent with CC URLs.

**User chose option 3** for structural consistency with the CC URL pattern.
This is a deliberate departure from BRIEF's "flat citable URL" principle in
favor of "every URL segment is a level-prefixed slug." Future-proofing
tradeoff: LRS section URLs are ~2× longer than they'd be under option 1, but
they look like the rest of the URL space.

### Notes on the layout

- **Section URLs SKIP intermediate hierarchy.** A section's full hierarchy
  might be `title-14/chapter-1/part-ii/subpart-a/section-30`, but the URL is
  just `/rs/title-14/section-30`. This mirrors CC: an article at
  `/cc/2315` lives deep in `book-3/title-5/chapter-3` but the URL doesn't
  encode that. Hierarchy is for *browsing*, URL is for *citing*.
- **Container URLs DO encode full hierarchy** (one level prefix per segment).
- **Section numbers with decimals**: `/rs/title-14/section-30.3` works
  identically to CC's `/cc/2315.1` — Astro handles dots in path segments.
- **No collision with container "section"**: CC's hierarchy includes a
  `section` container level (`/cc/.../section-2`). LRS's hierarchy levels
  are `title`, `chapter`, `part`, `subpart` — "section" only appears as the
  leaf. So `section-X` in an LRS URL is unambiguous within `/rs/...`.
- **Per-section data downloads** (mirroring CC's `/cc/2315.json` and
  `/cc/2315.md`):
  - `/rs/title-14/section-30.json`
  - `/rs/title-14/section-30.md`

---

## 5. Schema diff: LRS vs CC

The two corpora share most fields but differ on identity and tree shape. The
refactor must absorb this without leaking corpus-specific assumptions into
shared code.

### 5a. Section / Article record

Shared fields (identical types):

```
urn, heading, text, status, hierarchy_path, breadcrumb, acts_citations,
acts_citations_raw, source_url, source_html_hash, scrape_timestamp,
schema_version
```

| Field            | CC (`Article`)     | LRS (`Section`)              |
|------------------|--------------------|------------------------------|
| ID field         | `article_number`   | `title_number` + `section_number` |
| Citation form    | (none in data)     | `citation` (e.g. `"R.S. 14:30"`) |
| Extra            | —                  | `website_law_id` (number)    |

`hierarchy_path` entries are identical shape (`{level, number, name}`) in
both, but the LRS hierarchy can be deeper (max depth 7 vs CC's 5) and uses
different level vocabulary (`title`, `chapter`, `part`, `subpart` vs CC's
`book`, `title`, `chapter`, `section`, `subsection`, `paragraph`,
`preliminary_title`).

### 5b. Tree (`tree.json`)

CC node:

```jsonc
{
  "level": "book", "number": "III", "name": "...",
  "range_start": "1", "range_end": "1000",
  "status": "active",                  // string
  "children": [...],
  "articles": ["1", "2", ...]          // article numbers
}
```

LRS node:

```jsonc
{
  "level": "title", "number": "14", "name": "...",
  "section_range": ["1", "61"],        // array, not separate fields
  "is_repealed": false,                // boolean
  "is_reserved": false,                // boolean
  "children": [...],
  "sections": ["1", "2", ...]          // section numbers (scoped to title)
}
```

Refactor: the loader for LRS tree must normalize to a shared internal shape
OR the code paths must be parameterized. (Recommendation in Phase 1 below.)

### 5c. Hierarchy (`hierarchy.json`)

CC: nested `ancestors: HierarchyEntry[]` (each entry contains its own
ancestors recursively).

LRS: flat `parent_chain: [[level, number], ...]` (just tuples).

These don't normalize naturally; treat them as separate shapes and provide
parallel accessors. The existing CC `corpus.ts` doesn't depend heavily on
`hierarchy.json` (it derives most things from `tree.json`); leave LRS the
same way.

### 5d. Manifest (`manifest.json`)

CC's `totals.articles_emitted` → LRS's `totals.sections_emitted`. LRS
manifest also adds `corpus: "rs"` field. CC's manifest doesn't have a
`corpus` field (implicitly CC).

---

## 6. Files currently hardcoded to CC

A `grep` on the site repo found 18 files referencing `/cc/` or "Civil Code".
Triage:

### CC-only (leave as-is or barely touch):

- `src/pages/cc/[...slug].astro` — keep, but its logic should ride on
  shared corpus helpers after Phase 1.
- `src/pages/cc/index.astro` — CC root browse page; create parallel
  `src/pages/rs/index.astro` in Phase 3.
- `src/pages/cc/[article].md.ts` — CC markdown download; create parallel
  `src/pages/rs/[...slug].md.ts` in Phase 3.
- `src/pages/cc/[article].json.ts` — CC JSON download; same as above.
- `src/pages/about.astro`, `src/pages/roadmap.astro` — talk about Civil Code
  specifically by design. Phase 5: update the about copy to mention LRS and
  the cross-corpus design.

### Needs corpus-aware refactor (Phase 1):

- `src/lib/corpus.ts` — the centerpiece. Currently all `/cc/` and "Civil
  Code" knowledge lives here.
- `src/lib/slug.ts` — `containerPath()` and `articlePath()` hardcode `/cc/`.
- `src/lib/cite.ts` — generates Bluebook / permalink / BibTeX strings; CC-
  specific format ("La. Civ. Code art. X"). LRS needs "La. Rev. Stat. Ann.
  § X:Y". Add an LRS-aware code path.
- `src/components/CrossRefs.astro` — links to other articles; needs to know
  which corpus to link into.
- `src/components/PrevNext.astro` — prev/next nav; needs corpus context.
- `src/layouts/Base.astro` — site shell; check nav links and any "Civil
  Code"-labeled affordances.
- `src/pages/index.astro` — home page CTAs ("Read the Code →"). Phase 5:
  add LRS CTA.
- `src/pages/search.astro` — Pagefind UI; Phase 5: add corpus filter.
- `src/pages/data.astro` — describes the corpus; mention LRS in Phase 5.
- `src/pages/feed.xml.ts`, `src/pages/sitemap.xml.ts`, `src/pages/404.astro`
  — must include LRS pages.

### Components that already take primitive props (good):

`ArticleBody`, `Breadcrumb`, `History`, `StatusPill`, `CiteDialog`,
`Provenance` — per `src/pages/cc/[...slug].astro`, these all take primitive
props (text, status, citations, crumbs). No refactor needed; they'll Just
Work for LRS once the page route passes them the right data.

---

## 7. Phase breakdown

Each phase is a single PR. Smallest-to-largest blast radius. Each phase ends
with the site building cleanly and all existing CC pages identical to
baseline (until Phase 4).

### Phase 1 — Make `corpus.ts` and `slug.ts` corpus-agnostic (no LRS visible yet)

**Goal:** No user-visible change. Pure refactor. Existing CC pages render
identically. After this phase, the shared infrastructure is ready to accept
a second corpus, but no LRS data is loaded.

**Changes:**

1. **`src/lib/slug.ts`**:
   - Rename `articlePath(num)` → `ccArticlePath(num)`. Keep its body as-is.
   - Rename `containerPath(...)` → keep name but parameterize: accept a
     `prefix: string` first argument (e.g. `'/cc'` or `'/rs'`). Update all
     callers.
   - Similarly parameterize `pathSegments` if needed (the segments are the
     same, just the prefix differs).

2. **`src/lib/corpus.ts`**:
   - Move all CC-specific exports into `src/lib/cc.ts`:
     - `articles`, `articlesByNumber`, `ALL_NUMBERS`, `ACTIVE_NUMBERS`,
       `ALL_IDX`, `ACTIVE_IDX`
     - `neighbors()`, `activeNeighborsByPosition()`
     - `citationEdges`, `outgoingRefs()`, `incomingRefs()` (CC edges)
     - `allContainers`, `containersByPath`, `rootContainers`
     - `countArticlesIn()`
     - `articleBreadcrumb()`, `containerBreadcrumb()` — these hardcode
       "Civil Code" + `/cc`. Move + keep hardcoded for now; Phase 2 will
       add LRS parallels.
     - `articleBook()` — CC-specific; move.
     - `markdownPath()`, `articleMarkdownExists()`,
       `listMarkdownFilenames()` — CC-specific; move.
   - Keep in `corpus.ts` (now shared):
     - `TMP_DIR`, `readActiveTag()`, `ACTIVE_TAG`, `CORPUS_ROOT`
     - `readJSON()`, `readJSONL()`, `readCSV()` — file readers
     - `release` (release URL info — applies to whole bundle)
     - Common types: `ActsCitation`, `ArticleStatus` (rename to `Status`
       since it's universal), `HierStep` (already in slug.ts)
   - Rewrite the existing CC manifest export. The shape is CC-only; rename
     `Manifest` → `CcManifest` and `manifest` → `ccManifest`.

3. **Update all importers** of moved symbols:
   - `src/pages/cc/[...slug].astro` — change imports from `../../lib/corpus.ts`
     to `../../lib/cc.ts`.
   - `src/pages/cc/index.astro` — same.
   - `src/pages/cc/[article].md.ts`, `[article].json.ts` — same.
   - `src/components/CrossRefs.astro`, `PrevNext.astro` — same.
   - `src/pages/feed.xml.ts`, `sitemap.xml.ts`, `404.astro`,
     `data.astro`, `index.astro` — same.

4. **Verify:** `npm run build` produces the same `dist/cc/*` output. Diff
   the file list before / after — should be identical. Pagefind index
   should still cover 3,623 pages.

**Files touched:**

- New: `src/lib/cc.ts` (~250 lines, mostly moved from corpus.ts)
- Modified: `src/lib/corpus.ts` (slim it down to shared bits)
- Modified: `src/lib/slug.ts` (parameterize prefix)
- Modified: ~10 import statements across pages/components

**Tests / sanity checks:**

- `npm run build` succeeds, 4041 pages produced.
- `find dist/cc -type f | sort > after.txt; diff before.txt after.txt`
  shows no changes.
- Pagefind output identical (`dist/_pagefind/index/...` same file count).

---

### Phase 2 — Add LRS data loading + types (still no `/rs` routes)

**Goal:** All LRS data is loaded and queryable from TypeScript. Build still
produces only `/cc/...` pages — no new routes. After this phase, you can
write `import { sections } from '../lib/rs.ts'` and have working data; you
just can't visit the pages yet.

**Changes:**

1. **`src/lib/rs.ts`** (new, parallel to `cc.ts`):

   ```ts
   import { CORPUS_ROOT } from './corpus.ts';
   import { join } from 'node:path';
   import { readFileSync } from 'node:fs';

   const RS_ROOT = join(CORPUS_ROOT, 'rs');

   export interface Section {
     urn: string;
     title_number: string;
     section_number: string;
     citation: string;          // "R.S. 14:30"
     heading: string | null;
     text: string | null;
     status: 'active' | 'repealed' | 'reserved' | 'blank';
     hierarchy_path: HierStep[];
     breadcrumb: string;
     acts_citations: ActsCitation[];
     acts_citations_raw: string | null;
     source_url: string | null;
     website_law_id: number | null;
     scrape_timestamp: string | null;
     source_html_hash: string | null;
     schema_version: string;
   }

   export interface RsTreeNode {
     level: string;
     number: string;
     name: string;
     section_range: [string, string];
     is_repealed: boolean;
     is_reserved: boolean;
     children: RsTreeNode[];
     sections?: string[];      // section numbers (scoped to title)
   }

   export interface RsTree {
     schema_version: string;
     generated_at: string;
     roots: RsTreeNode[];      // 65 roots, one per Title
   }

   export interface RsManifest {
     schema_version: string;
     generated_at: string;
     corpus: 'rs';
     totals: {
       containers: number;
       sections_emitted: number;
       by_status: Record<string, number>;
     };
     // ... (see data/rs/manifest.json for the full shape, includes
     //   completeness.by_title)
   }
   ```

2. **Loaders** (mirror `cc.ts` structure):
   - `sections: Section[]` from `rs/sections.jsonl`
   - `sectionsByKey: Map<string, Section>` where key = `${title}:${section}`
   - `rsTree: RsTree` from `rs/tree.json`
   - `rsManifest: RsManifest` from `rs/manifest.json`

3. **LRS-specific sort order**: section numbers are scoped to titles, so
   global ordering needs `(title_int, section_key_tuple)`. Implement
   `compareLrsSections(a, b)` using `articleSortKey()` from `slug.ts` (which
   handles "11.1" between "11" and "12" correctly) plus title comparison.

4. **LRS containers**: walk `rsTree.roots` and build a flat
   `rsAllContainers: RsContainer[]` parallel to CC's `allContainers`.
   `RsContainer` needs:
   - `pathSegs: string[]` — e.g. `['title-14', 'chapter-1', 'part-ii']`
   - `url: string` — `/rs/${pathSegs.join('/')}`
   - `ancestors: HierStep[]`
   - `self: HierStep`
   - `sectionNumbers: string[]` (scoped to title)
   - `titleNumber: string` (which Title is this container in — needed
     because section numbers aren't globally unique)
   - `children: RsContainer[]`

5. **LRS breadcrumb helpers**:
   - `sectionBreadcrumb(s: Section): CrumbLink[]` — start with
     `{label: 'Revised Statutes', href: '/rs'}`, then each step in
     `hierarchy_path`.
   - `containerBreadcrumb(c: RsContainer)` (already exists in cc.ts under
     same name; rename the CC one to `ccContainerBreadcrumb`, name LRS one
     `rsContainerBreadcrumb`, or namespace them).

6. **LRS citation edges**: `rs/citation_edges.csv` has the same shape as
   CC's. Build `outgoingRefs`/`incomingRefs` keyed by `${title}:${section}`.

7. **LRS markdown lookup**:
   - `markdownPath(title: string, section: string): string | null` —
     resolves to `tmp/corpus/usufruct-<TAG>/rs/markdown/title-${title}/${section}.md`.

8. **`src/lib/cite.ts`**: Add `lrsCite(s: Section)` returning Bluebook
   "La. Rev. Stat. Ann. § 14:30 (2026)", permalink URL, BibTeX entry. The
   year still comes from `manifest.generated_at` (use the LRS manifest's
   `generated_at`).

**Verify:** Same as Phase 1 — no user-visible change. Write a tiny throwaway
script in `scripts/` that imports `sections` and prints the count — confirms
the loader works. Delete the script before committing.

**Files touched:**

- New: `src/lib/rs.ts` (~300 lines)
- Modified: `src/lib/cite.ts` (add LRS citation form)
- Modified: `src/lib/corpus.ts` if necessary (might need a couple more
  shared bits)

---

### Phase 3 — Add `/rs/` routes

**Goal:** LRS pages are routable. `npm run build` produces 45,774 section
pages + ~5,529 container pages. **CF Pages deploy will start failing here**
because total file count exceeds the 20,000 cap (Phase 4 resolves).

**Changes:**

1. **`src/pages/rs/[...slug].astro`** (new):
   - Catch-all, parallel to `src/pages/cc/[...slug].astro`.
   - `getStaticPaths`:
     - For each `Section`: push `{params: {slug:
       'title-${title}/section-${section}'}, props: {kind: 'section',
       title, section}}`.
     - For each `RsContainer`: push `{params: {slug:
       c.pathSegs.join('/')}, props: {kind: 'container', containerKey:
       c.pathSegs.join('/')}}`.
   - Render logic: parallel to CC catch-all. Use `Breadcrumb`,
     `ArticleBody`, `StatusPill`, `History`, `CrossRefs`, `PrevNext`,
     `CiteDialog`, `Provenance` exactly as CC does — they're already
     primitive-prop'd.
   - Status pill uses the same `Status` type.
   - Citation text in `ArticleBody` header should show `s.citation`
     (`"R.S. 14:30"`) where CC shows `"Article {a.article_number}"`.

2. **`src/pages/rs/index.astro`** (new):
   - Parallel to `src/pages/cc/index.astro`.
   - Lists 65 root containers (Titles 1–56 with letter-suffixed sub-titles
     and renumbered ones).
   - Shows `rsManifest.totals` and per-title completeness.

3. **`src/pages/rs/[...slug].md.ts`** (new):
   - Catch-all download endpoint. URL example:
     `/rs/title-14/section-30.md`.
   - Resolves to `tmp/corpus/usufruct-<TAG>/rs/markdown/title-14/30.md`.
   - Returns the raw markdown as `text/markdown`.

4. **`src/pages/rs/[...slug].json.ts`** (new):
   - Same shape, returns `tmp/corpus/usufruct-<TAG>/rs/sections/rs_14_30.json`
     as `application/json`.

5. **`src/pages/feed.xml.ts`**, **`sitemap.xml.ts`**: include LRS pages.
   For feed: probably skip per-section entries and add an entry for the
   LRS corpus release itself. For sitemap: include all `/rs/...` URLs.

6. **`src/pages/404.astro`**: extend the nearby-articles logic to also
   handle nearby LRS sections (parse URL — if `/rs/title-N/section-X`, look
   up neighbors in `rs.ts`).

7. **`src/components/PrevNext.astro`**: detect corpus from current URL or
   accept a `corpus: 'cc' | 'rs'` prop. Recommendation: prop-based to keep
   the component pure.

8. **`src/components/CrossRefs.astro`**: same — accept `corpus` prop to
   know which corpus to link into.

**Verify:**

- `npm run build` succeeds.
- `find dist/rs -type f | wc -l` ≈ 45,774 section pages + 5,529 container
  pages + LRS index = ~51,000 HTML files.
- Total `dist/` file count ≈ 65,000 (CC 18,692 + LRS ~46,500).
- Spot-check: `dist/rs/title-14/section-30/index.html` exists and renders
  the First Degree Murder section correctly.
- Pagefind indexing: should pick up LRS pages automatically (any `<article
  data-pagefind-body>` element). Check `dist/_pagefind/index.json` shows
  ~49,000 indexed pages.

**Known break here**: CF Pages will refuse to deploy. Local build still
works; you just can't ship until Phase 4.

---

### Phase 4 — Cloudflare R2 + Worker hosting cutover

**Goal:** Move hosting from Cloudflare Pages to Cloudflare R2 + Worker.
Theusufruct.com starts serving 65,000+ files including all LRS pages.

This phase is OPERATIONAL more than coding — most of it is in the
Cloudflare dashboard plus a small Worker script and a GitHub Actions
workflow.

**Plan was sketched in prior session; recapping the components:**

1. **Create R2 bucket** `theusufruct-prod`. (Open sub-decision: shared
   bucket for prod+preview, or separate. Default: shared, single
   namespace.)

2. **Worker script** (~50 LOC) at `theusufruct.com/*` route:
   - Strip trailing slash → look up `<path>/index.html`
   - Bare `/path` → try `path` first, then `path/index.html`
   - 404 → return `dist/404.html`
   - Set Content-Type from extension
   - Cache-Control rules (per prior planning):
     - HTML, `/cc/*`, `/rs/*`: `max-age=60, stale-while-revalidate=86400`
     - `_astro/*` (hashed bundles): `immutable, max-age=31536000`
     - `_pagefind/*`: `max-age=3600`
     - `.json`/`.md`: `max-age=300, stale-while-revalidate=86400`
     - sitemap/robots/feed: `max-age=86400`
     - favicons: `max-age=2592000`

3. **GitHub Actions workflow** `theusufruct-site/.github/workflows/deploy.yml`:
   - On `push` to `main` (and on `repository_dispatch` from the corpus
     repo — see Phase 5).
   - Run `npm ci && npm run build` (this fetches latest corpus zip, runs
     Astro build, runs Pagefind, syncs to public).
   - `aws s3 sync dist/ s3://theusufruct-prod/ --endpoint-url=<R2-endpoint>
     --delete` (R2 is S3-compatible). Secrets: `R2_ACCESS_KEY_ID`,
     `R2_SECRET_ACCESS_KEY`, `CF_ACCOUNT_ID`.
   - Trigger CF cache purge: `curl -X POST ... /purge_cache`. Secret:
     `CF_API_TOKEN` with `Zone.Cache Purge` scope, `CF_ZONE_ID`.

4. **Cutover sequence** (do NOT cut over until Worker is verified):
   - Build R2 bucket, deploy Worker to `*.workers.dev` route first.
   - Test thoroughly: load /cc/2315, /rs/title-14/section-30, /search,
     404s, JSON/MD downloads, Pagefind search.
   - Switch the production route `theusufruct.com/*` from Pages to the
     Worker. DNS unchanged.
   - Confirm theusufruct.com works.
   - Delete the CF Pages project. (Reversible up to this point.)

5. **Bot management**: turn on CF's free bot-fight rules so we don't get
   scraped to death.

**Verify:**

- `curl -I https://theusufruct.com/cc/2315` returns 200, correct
  Content-Type, correct Cache-Control.
- `curl -I https://theusufruct.com/rs/title-14/section-30` returns 200.
- Pagefind search at `/search` finds both CC and LRS results.
- 404 page renders for unknown paths.

---

### Phase 5 — Polish

Order within Phase 5 doesn't matter — each item is independent.

1. **Pagefind corpus filter** — Pagefind supports filters via
   `data-pagefind-filter` attributes. Add `data-pagefind-filter="corpus:cc"`
   to CC pages and `corpus:rs` to LRS pages. The default UI exposes
   filters automatically.

2. **Cross-repo trigger workflow** — in this corpus repo
   (`bitwulf/Usufruct`), add `.github/workflows/notify-site.yml`:
   - On `release: published`
   - POST `repository_dispatch` to `bitwulf/theusufruct-site` with event
     type `corpus-released` and the new tag in the payload.
   - Secret in this repo: `SITE_REPO_PAT` (a PAT with `Contents: write`
     scope on the site repo).

3. **Home page update** — `src/pages/index.astro`: add a second CTA
   "Read the Statutes →" pointing to `/rs`. Update prose to mention both
   corpora.

4. **About / Data / Roadmap pages** — update copy to reflect dual-corpus.

5. **Corpus picker in nav** — add a small toggle in the site header
   (Base.astro) between CC and LRS, especially on the search and home
   pages.

6. **404 nearest-neighbor logic** — for `/rs/title-N/section-X` 404s,
   find the nearest existing section in that title and link to it
   (parallel to CC's existing behavior).

---

## 8. Open sub-decisions

These don't block Phase 1. Park them and resolve before the relevant phase
starts.

- **Phase 3, LRS feed entries**: include per-section entries (45K!) or just a
  per-release entry? Default: per-release only. Per-section would bloat the
  Atom feed unusably.

- **Phase 4, R2 bucket strategy**: shared prod+preview namespace vs. two
  buckets. Default: shared, simpler.

- **Phase 4, Pagefind pre-compression**: pre-build `.br`/`.gz` of the
  Pagefind index and serve via Worker, vs. rely on CF's auto-compression.
  Default: rely on auto-compression; revisit if Pagefind index loading is
  slow.

- **Phase 5, Bluebook citation format for LRS**: confirm the canonical
  Bluebook short form is "La. Rev. Stat. Ann. § 14:30" or
  "La. Stat. Ann. § 14:30". Reference *The Bluebook: A Uniform System of
  Citation* (21st ed.). Pick one before locking the cite popover copy.

- **Phase 5, corpus picker UI placement**: header, sidebar, or homepage
  only. Defer to whoever does the visual design pass.

---

## 9. Things NOT to do

- **Do not change `fetch-corpus.sh`.** The bundled-zip approach is
  specifically designed to be invisible to it. The script just downloads,
  verifies, unpacks. LRS data is in the subdir; the script doesn't care.

- **Do not unify the CC and LRS manifests into a single top-level
  manifest.** The release verifier (`scripts/verify_release.sh` in
  `bitwulf/Usufruct`) cross-checks `manifest.totals.articles_emitted`
  against `articles.jsonl` line count. Changing the CC manifest shape
  would break that. LRS gets its own `rs/manifest.json` (already does);
  load both separately.

- **Do not collapse `Article` and `Section` into a single type.** Their
  identity fields differ (`article_number` vs `title_number`+`section_number`)
  and the difference is load-bearing in URLs, citations, and breadcrumbs.
  Keep them distinct but make the components that render them generic.

- **Do not switch URL layout midway through.** The
  `/rs/title-N/section-X` decision is locked. If someone questions it
  later, treat it as a new conversation (with the user) — do not
  unilaterally migrate URLs once Phase 3 has shipped.

- **Do not generate `chunks.jsonl` consumption code.** That file is not in
  the release. If you need it later, request a new corpus release that
  includes it.

- **Do not push from `/Users/d0j3/Dev/Usufruct/theusufruct-site/`** unless
  you've verified that directory has remote write access to
  `bitwulf/theusufruct-site`. It was originally cloned as read-only for
  analysis. The user's canonical site checkout may live elsewhere.

---

## 10. How to start (Phase 1 first step)

In the site repo (`bitwulf/theusufruct-site`):

1. Create a feature branch: `git checkout -b lrs-phase-1-corpus-agnostic`.

2. Run the build once to capture baseline:
   ```sh
   npm install     # if needed
   npm run build
   find dist -type f | sort > /tmp/dist-before.txt
   ```

3. Do the Phase 1 refactor per §7.

4. Rebuild and diff:
   ```sh
   npm run build
   find dist -type f | sort > /tmp/dist-after.txt
   diff /tmp/dist-before.txt /tmp/dist-after.txt
   ```
   Expect zero differences.

5. Commit + PR + merge. Then onward to Phase 2.

If you're a fresh Claude session reading this: read these files in this
order before touching anything:
- This plan (you just did).
- `/Users/d0j3/Dev/Usufruct/theusufruct-site/BRIEF.md` — site goals,
  audience, non-negotiables.
- `/Users/d0j3/Dev/Usufruct/theusufruct-site/src/lib/corpus.ts` — the
  thing you're refactoring.
- `/Users/d0j3/Dev/Usufruct/theusufruct-site/src/lib/slug.ts` — the URL
  helpers.
- `/Users/d0j3/Dev/Usufruct/theusufruct-site/src/pages/cc/[...slug].astro`
  — the route file that consumes everything.
- A sample LRS section:
  `head -1 /Users/d0j3/Dev/Usufruct/data/rs/sections.jsonl | python3 -m json.tool`
- A sample CC article (compare):
  `head -1 /Users/d0j3/Dev/Usufruct/data/articles.jsonl | python3 -m json.tool`

Don't read every file in `src/` upfront — most don't need to change in
Phase 1, and the import-update list in §7 tells you exactly which to touch.
