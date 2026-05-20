# Contributing to Usufruct

Thanks for your interest. Usufruct is a small, focused project — a scraper and
data pipeline for the Louisiana Civil Code. This document covers the
mechanics. The high-level design and non-goals live in
[README.md](README.md); please read that first.

## Dev setup

```sh
git clone <your-fork-url>
cd usufruct
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                 # 60 tests, all offline, <20 seconds
```

The pipeline itself does not need to be run during development; the test
suite uses cached HTML fixtures under `tests/fixtures/` so nothing touches
the network.

## Running the pipeline locally

```sh
.venv/bin/usufruct phase1        # parse LSU TOC (cached)
.venv/bin/usufruct phase2        # parse legis TOC (cached)
.venv/bin/usufruct phase3        # ~42 min full scrape @ 1 req/s
.venv/bin/usufruct phase4        # derived artifacts (~2s)
.venv/bin/usufruct snapshot      # archive into snapshots/YYYY-MM-DD/
```

If you only need to test parser changes, `phase3 --limit N` stops after the
first N articles.

## Things to keep in mind

- **Be polite to legis.la.gov.** It is a state government site, not a CDN.
  The 1 req/sec rate limit is non-negotiable. If you need a faster local
  iteration loop, work against the cached fixtures, not the live site.
- **Article numbers are strings, never ints.** `"2315.1"` is its own article,
  not a sub-part of `"2315"`. The schema enforces this and tests cover it.
- **Blank, repealed, and reserved articles are first-class records.** Do not
  silently skip them — that creates phantom gaps that look like scraper bugs.
- **No revision comments.** We deliberately do not include LSLI-annotated
  content (revision comments, editor's notes, cross-reference links). That
  scope is enforced; PRs adding such content will be declined.

## Adding a new article fixture

When a parser change needs a regression test pinned to a specific article:

1. Download the article's HTML manually (`curl -A "usufruct dev" https://legis.la.gov/legis/Law.aspx?d=NNNNNN > tests/fixtures/articles/ccXXX.html`).
2. Add the article number → fixture stem mapping to `TEST_ARTICLES` in
   `tests/test_orchestrate.py`.
3. Write a focused test asserting the specific edge case the article
   exercises (multi-paragraph body, deep hierarchy, repealed range, etc.).
4. Run `pytest` — both the orchestrate tests and the Phase 4 tests reuse the
   fixture set, so a single fixture covers a lot of ground.

Do not commit large blobs of HTML for unrelated articles. Fixtures should
exist for a reason.

## Style

- **Black-style formatting**, 88-column lines. No formatter is enforced via
  pre-commit; just match the surrounding code.
- **Pydantic v2 models** for schema. New fields belong in
  `src/usufruct/model/schema.py`; bump `SCHEMA_VERSION` in
  `src/usufruct/__init__.py` if the change is backwards-incompatible.
- **Module-level docstrings** at the top of each `.py` file describing
  responsibility and any non-obvious behavior. Existing modules are the
  template.
- **Comments explain why, not what.** The code's job is to be readable; the
  comment's job is to explain a constraint, invariant, or surprise that
  isn't visible in the code.

## Pull requests

- Open an issue first if the change is non-trivial — easier to align on
  approach before code review.
- One logical change per PR. Refactors and feature changes should not share
  a PR.
- Tests required for any parser or schema change. The 13-article fixture set
  is the regression bar; if your change can pass with that set unchanged,
  reviewing is fast.
- Update [BUILDHISTORY.md](BUILDHISTORY.md) with a dated entry for anything
  that materially changes the corpus output (new fields, new validation
  rules, new derived artifacts).

## Reporting data bugs

If you find a specific article that the scraper handles incorrectly:

1. Note the article number and what looks wrong (missing heading, body
   truncated, wrong hierarchy assignment, etc.).
2. Open an issue with a link to the source page on legis.la.gov.
3. If you can attach the HTML, even better — that becomes the fixture for
   the fix.

Schema-shape bugs (a field missing, an enum value unexpected) are more
serious than text-quality bugs (an em-dash rendered as a hyphen). Flag the
former with priority.
