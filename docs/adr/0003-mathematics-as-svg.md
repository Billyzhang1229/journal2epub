# 3. Mathematics is rendered to inline SVG, labelled with its TeX source

Status: accepted

## Context

EPUB 3 supports MathML natively, so the obvious choice is to pass the
publisher's MathML straight through.

Two facts make that wrong here.

**Publishers deposit one form or the other, and which one is not predictable.**
GigaScience's JATS contains `<tex-math>` and zero `mml:math`. PLOS Computational
Biology contains `mml:math` and zero `<tex-math>` — exactly the opposite. A
pipeline built for either alone silently loses every expression in the other.

GigaScience's TeX also arrives wrapped in a complete LaTeX document
(`\documentclass`, a stack of `\usepackage` lines, then the expression inside
`\begin{document}`), and on some formulae the conversion leaves a stray `}{}`
immediately after `\begin{document}` that unbalances it. Both have to be
handled to recover the maths.

**MathML support on readers is thin.** It is uneven across e-ink firmware in
particular, and where it is missing the maths degrades to unstyled, reordered
text — worse than useless in an expression.

## Decision

Accept **either** source form and render both to **inline SVG** with MathJax,
run offline at build time. MathJax has input processors for TeX and for MathML;
using both means one output path downstream regardless of publisher.

* SVG renders identically everywhere, including on e-ink.
* MathJax sizes it in `ex` units, so the maths scales with whatever font size
  the reader chooses instead of being pinned to a pixel size.
* Each `<svg>` carries `role="img"`, an `aria-label`, and a `<title>` child.
  For TeX sources the label is the original TeX; for MathML sources it is the
  expression's text content, because reading markup aloud helps nobody.
* Rendered SVG is cached by content hash, so a rebuild is deterministic and does
  not need node installed at all.

## Consequences

* A cold build needs node and `mathjax-full`; a warm rebuild needs neither.
* Where an expression cannot be typeset, it is shown as its TeX source in a
  monospace fallback and counted in the build report — loud, never blank.
* Ids inside each SVG are namespaced per expression, because MathJax reuses
  glyph ids that would otherwise collide when many expressions share a page.

* An expression with no usable source in *either* form still counts as a
  fallback. An uncounted fallback is worse than a visible one: it makes the
  build report claim success it did not have.

Across GigaScience volume 12: 1,082 TeX expressions, all typeset, zero
fallbacks. Across PLOS Computational Biology volume 22 issue 5: 6,532 MathML
expressions, all typeset, zero fallbacks.
