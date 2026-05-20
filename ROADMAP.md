# Roadmap

Usufruct ships the Louisiana **Civil Code** today. This document sketches the
expansion path. The next major corpus is the **Louisiana Revised Statutes
(LRS)**; the remaining ancillary codes follow after.

This is a high-level direction document, not a detailed spec. Decisions marked
**(open)** still need to be made before implementation starts.

## Order

1. Louisiana Revised Statutes (LRS) — next
2. Louisiana Children's Code
3. Code of Civil Procedure
4. Code of Criminal Procedure
5. Code of Evidence

The Civil Code stays first-class throughout. None of the additions remove or
break the existing CC corpus.

---

## Phase A — Louisiana Revised Statutes

### Why this is harder than the Civil Code

| Dimension | Civil Code | Revised Statutes |
| --- | --- | --- |
| Volume | ~2,500 articles | ~25,000+ sections (≈10×) |
| Hierarchy source | LSU CCLS TOC (one fetch) | legis.la.gov only — no curated external TOC |
| Citation form | `art. 2315` (one number) | `R.S. 14:30` (Title:Section compound) |
| Scrape time @ 1 req/s | ~42 min | ~7 hours per full run |
| Repeal density | Moderate | High — many Titles have large repealed ranges |

### Per-phase sketch

**Phase 1 — Structural skeleton.** Derive Title → (Subtitle) → Chapter → Part →
Subpart → Section hierarchy directly from legis.la.gov TOC pages. There is no
LSU equivalent. Each of the 56 Titles is a separate sub-tree. → `data/rs/hierarchy.json`.

**Phase 2 — Section ID discovery.** Walk the legis TOC into
`{(title, section): website_law_id}`. → `data/rs/section_index.json`.

**Phase 3 — Section scrape.** Same cache + SHA-256 + parse loop as CC, but at
~10× the volume. Repealed and reserved sections get first-class records, same
as CC blanks. → `data/rs/sections/*.json` + `data/rs/sections.jsonl`.

**Phase 4 — Derived artifacts.** `tree.json`, `chunks.jsonl`, `markdown/`,
`citation_edges.csv`. Citation extraction must handle the `R.S. T:S[.sub]`
pattern as well as cross-corpus references (LRS → CC and CC → LRS).

### Cross-cutting decisions

- **Schema unification (open).** Either (a) generalize `Article` into a
  `Provision` record with a `provision_type` discriminator, or (b) introduce a
  parallel `RSSection` model. (a) is cleaner downstream but touches every CC
  consumer; (b) is additive and lower-risk. Recommend (b) for the first
  release, with (a) as a follow-up once both corpora are stable.
- **URN scheme.** `urn:us-la:rs:{title}:{section}` — e.g. `urn:us-la:rs:14:30`,
  `urn:us-la:rs:9:2800.1`. Mirrors the existing `urn:us-la:civcode:art:{n}`.
- **Data layout.** Split: `data/civcode/` for the existing CC artifacts (with a
  back-compat shim for the current flat layout during transition) and
  `data/rs/` for LRS. A combined top-level `manifest.json` lists both corpora.
- **CLI.** Introduce corpus subcommands: `usufruct civcode phase1`,
  `usufruct rs phase1`, etc. The bare `usufruct phase1` keeps current behavior
  (CC) for one release, then deprecates.
- **Releases.** Continue per-snapshot GitHub Releases. Each release ships both
  corpora as separate zips so consumers can pull just what they need.
- **Limitations carry over.** No revision comments, no annotations, no
  jurisprudence links, no historical versions, no semantic enrichment. Same
  rules, larger corpus.

### First slice

Implementing all 56 Titles in one go is the wrong shape. Start with two Titles
that exercise the full pipeline and have the highest downstream value:

1. **Title 9 — Civil Code Ancillaries.** Directly companions the CC. Includes
   `9:2800` (limits on state tort liability) and other provisions routinely
   cited alongside CC articles. Smallest natural unit that proves cross-corpus
   citation extraction works.
2. **Title 14 — Criminal Law.** Self-contained, high-traffic, well-defined
   structure. Good stress-test for repeal handling.

Pinned regression fixtures should include at minimum: `9:2800`, `9:2800.1`,
`14:30` (first-degree murder), `14:30.1`, `14:67` (theft), and a known
repealed section to lock down the repeal path.

### Open questions

- Does the legis.la.gov LRS TOC expose stable container IDs we can cache, or
  does the structure shift between page loads? (Needs a 1-hour spike.)
- Are there "blank by design" gaps in LRS like CC 16–23, or are gaps always
  repealed? Affects whether the `blank` status carries over.
- How to handle sections that are continuously amended within a single act
  (e.g., omnibus criminal-law bills) — does `acts_citations_raw` parsing need
  to grow, or is the current shape sufficient?
- Chunk strategy: some LRS sections (e.g., insurance provisions in Title 22)
  are far longer than any CC article. One chunk per section, or split by
  subsection?

---

## Phase B — Ancillary codes

After LRS lands and the cross-corpus plumbing is proven, the remaining codes
follow in this order: **Children's Code**, **Code of Civil Procedure**,
**Code of Criminal Procedure**, **Code of Evidence**. Each is structurally
similar to CC (article-numbered, smaller than LRS) and should reuse the
generalized `Provision` model from the LRS follow-up work, not require a new
schema each.

No timeline commitments. The CC corpus is the priority; further codes ship
when LRS is stable and there's bandwidth.
