# 5. What adding a second journal actually cost

Status: accepted
Date: 2026-08-01

## Context

The architecture claimed that adding a journal that deposits JATS in PMC should
be "two data files and no code". PLOS Computational Biology volume 22 issue 5
was built to test that claim honestly rather than assert it.

## What held

The internal model, the source adapter, the renderer, the packager, the theme
system, the caching layer, the resumable state and both validators all worked
unchanged. The parser met **zero unmodelled elements** across the issue — 79
articles, 570 figures, 155 tables, 4,427 references — after one addition to the
citation-parts set (`<name>`, which PLOS uses where GigaScience uses
`<string-name>`).

Two data files did most of the work: a journal descriptor and a theme.

## What did not

Four things needed code, and each was a real gap rather than a PLOS quirk.

**1. Issue granularity.** PLOS publishes discrete monthly issues; volume 22 has
521 articles and no reader wants that as one book. GigaScience numbers only by
volume and has no issues at all. `--issue` now threads through discovery,
cross-check, state and labelling; journals without issues simply omit it.

**2. Cursor paging was silently broken.** Crossref's deep paging re-sends *one*
stateful scroll token while the server advances its own state, so every page
after the first has an identical URL. The durable HTTP cache is keyed by URL, so
from page two onwards it replayed a single cached page forever. Enumeration
returned 94 stubs containing 17 unique DOIs.

This never showed up on GigaScience because 1,573 works fit in two pages, and
the counts matched PMC exactly, which looked like proof of correctness. It only
surfaced on a journal with 12,850 works. `Fetcher.get` now takes a `cache_key`
so callers can distinguish requests that share a URL, and `iter_works`
deduplicates and stops on a page that adds nothing new, so a stalled cursor can
never again masquerade as data.

**3. Maths source form is publisher-specific.** GigaScience deposits TeX and no
MathML; PLOS deposits MathML and no TeX. The pipeline handled only TeX, so all
6,532 PLOS expressions degraded to bare text. Worse, the `no source` path did
not increment the fallback counter, so the report said `math_typeset: 0,
math_fallback: 0` — it claimed a success it had not had. Both are fixed: MathJax
now takes either input form, and every fallback is counted.

**4. A theme could ship failing accessibility.** The first PLOS accent, matched
to the journal's own web orange, scored 4.46:1 against white — under the 4.5:1
WCAG AA threshold by a hundredth, producing 14,783 serious violations, one per
citation marker. Nothing in the pipeline checked. `load_theme` now computes
contrast for every text-bearing colour pair and refuses a theme that fails.

## Consequences

The "one adapter, no rewrite" claim survives, but "no code" was too strong: a
new journal can expose a **latent defect** in shared machinery that the first
journal's shape happened to hide. Three of the four fixes above are corrections
to general code, not accommodations for PLOS, and all three make GigaScience's
builds more trustworthy too.

The lesson worth keeping: a second source is not only a compatibility test, it
is the cheapest available audit of the first one.
