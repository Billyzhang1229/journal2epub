# 4. Wide tables get a clipped scroll region; nothing may widen the page

Status: accepted

## Context

A reflowable EPUB hands control of font, size and margins to the reader. Wide
scientific tables cannot reflow: a 14-column results table has an irreducible
minimum width far beyond a 6-inch e-ink panel.

The failure mode to avoid is not an ugly table. It is a table that widens the
**page**, so that every line of prose in the article then requires horizontal
scrolling to read. That destroys the book.

## Decision

**The page body must never scroll horizontally.** Anything that cannot fit is
given its own clipped, scrollable region instead.

* Each table sits in a `.table-scroll` box with `overflow-x: auto` and
  `max-width: 100%`, so the table may be wider than the screen but its container
  never is.
* Type steps down with column count (`cols-md` ≥6, `cols-lg` ≥9, `cols-xl` ≥13),
  so moderately wide tables simply fit.
* Tables of 9 columns or more carry a short printed note telling the reader they
  can scroll sideways or turn the device to landscape. The strategy is stated to
  the reader rather than left to be discovered.
* The scroll box is `tabindex="0"` with `role="region"` and a unique
  `aria-label`, because content only reachable by scrolling must be reachable
  without a mouse.

## The subtler half: unbreakable text

Testing the built book at 360, 400 and 618 CSS pixels showed the page still
scrolling sideways by up to 256px — and the tables were not the cause. They were
clipping correctly. The culprits were single unbreakable tokens: a
`https://proceedings.neurips.cc/paper/2012/file/c399862d3b9d6…` URL rendering
531px wide inside a 334px parent, and supplementary filenames like
`giac119_Response_to_Reviewer_Comments_Original_Submission`.

Scientific prose is full of these — URLs, accession numbers, gene and tool
names, filenames. One of them anywhere in an article is enough to widen every
page of it.

So: `overflow-wrap: break-word` on the body, and `overflow-wrap: anywhere` on
links, code, reference entries, keywords and supplementary labels. `anywhere`
rather than `break-word` specifically because only `anywhere` also reduces the
element's min-content width, which is what stops it widening its container.

## Verification

`tools/check_reflow.py` renders every page of a built edition at each device
width and asserts zero page-level horizontal overflow. It is the regression test
for this decision; the bug above is exactly what it catches.
