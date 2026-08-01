# 1. Structured full text is the primary source; HTML scraping is not implemented

Status: accepted

## Context

There are two ways to get a journal article's full text: render the publisher's
article page and convert the HTML, or read the structured full text the
publisher deposits in an archive.

The rendered page is a presentation of the article. The structure that matters
for making a book — which headings nest inside which, which affiliation belongs
to which author, which caption belongs to which figure, which cells are header
cells, what a formula means, what each reference is composed of — exists in the
page only as styling conventions that have to be reverse-engineered, and that
change whenever the publisher's templates change.

Open-access journals deposit NISO JATS XML in PubMed Central. That XML carries
all of it explicitly.

## Decision

Structured full text (JATS) is the primary and only implemented acquisition
path. A rendered-HTML adapter is anticipated by the `SourceAdapter` interface
but deliberately not written.

## Consequences

Measured over GigaScience volumes 12 and 14 — 277 articles spanning every
article type the journal publishes — the parser reached **zero unmodelled
elements**, with section hierarchy, author-to-affiliation binding, figure
captions, table structure, 1,082 mathematical expressions and 7,371 references
all obtained directly rather than inferred.

The cost is coverage: an article that is not deposited in the PMC Open Access
Subset cannot be included. In volume 12 that was 1 article of 113; in volume 13,
3 of 118. Those articles are named and explained in the book and in the report
rather than quietly omitted, which is the honest trade.

If coverage ever has to improve, the answer is another structured source
(Europe PMC, an OAI-PMH endpoint, a publisher API), not scraping. Adding one
costs a single adapter, because everything downstream sees only the internal
model.

**Do not implement a scraping path without revisiting this decision explicitly.**
