# Architecture

```
  Crossref ─────────► discovery ──┐
  PMC ID Converter ──► DOI→PMCID  │
  PMC OA on AWS ─────► JATS + figures
                                  │
                          ┌───────▼────────┐
                          │ source adapter │   sources/pmc.py + sources/jats.py
                          └───────┬────────┘
                                  │  Article  (model.py)
                          ┌───────▼────────┐
                          │    renderer    │   render/xhtml.py + render/css.py
                          └───────┬────────┘
                                  │  XHTML + CSS + SVG
                          ┌───────▼────────┐
                          │    packager    │   render/epub.py
                          └───────┬────────┘
                                  ▼
                        volume.epub + volume.report.json
```

Everything crosses one boundary: the internal model. Adapters map *into* it,
the renderer maps *out of* it, and nothing publisher-specific passes through.

## The internal model (`model.py`)

Deliberately small and closed. The renderer handles every construct in it
exhaustively — which is only possible because the set is finite.

* **Inline**: text, emphasis, internal link, external link, inline maths,
  inline graphic, line break, footnote reference.
* **Block**: paragraph, section, figure, table, list, definition list, quote,
  code, preformatted, display maths, supplement, statement.
* **Front matter**: title, authors (with normalised affiliations, ORCID,
  corresponding flag), abstracts, keywords, identifiers, dates, licence.
* **References**, footnotes, assets, provenance, and an **unhandled log**.

JATS has hundreds of elements; this has about twenty constructs. The gap is the
point — and `Article.unhandled` records every element the adapter met and chose
not to model, with its tag and the article it came from, so the gap is visible
instead of silent. Reviewing that log is how the parser got hardened; it is
empty across GigaScience volumes 12 and 14.

## Source adapters (`sources/`)

An adapter does two things: `discover(journal, volume)` enumerates what the
volume *should* contain, and `fetch(stub, journal)` resolves one article to an
`Article` or explains why it could not.

Discovery must include articles the adapter cannot itself resolve. The report
accounts for everything attempted, so discovery narrowing to what is convenient
would make the accounting a lie.

Adding a source costs one adapter. See
[ADR 1](adr/0001-structured-source-first.md) for why the anticipated HTML
scraping adapter is deliberately unimplemented.

## Durability and resumability

* **HTTP cache** (`net.py`) — every response written to disk, keyed by URL.
  A warm rebuild makes zero network requests. `--offline` proves it by refusing
  to touch the network at all.
* **Build state** (`state.py`) — SQLite, committed per article as each outcome
  becomes known. Killing a build and restarting resumes; it does not start over.
  The parsed model is deliberately *not* persisted: the HTTP cache already makes
  re-parsing cheap and deterministic, and storing it would add a serialisation
  format to keep in step with the model.
* **Maths cache** (`render/math.py`) — rendered SVG keyed by content hash, so a
  rebuild needs neither node nor MathJax.

## Reproducibility

Same inputs, same bytes. Zip entries carry a fixed timestamp, iteration order is
sorted, the package identifier is derived from a hash of the article DOIs and
their source checksums, and `dcterms:modified` comes from the newest source
retrieval time rather than the wall clock. Verified: two clean builds from a
warm cache produce identical SHA-256.

## Theme and templates

Themes (`data/themes/*.toml`) hold colour, type stack, heading scale, label
treatment and rule weights. `render/css.py` turns one into a stylesheet.
Templates never name a colour or a typeface, so adding a journal means writing a
theme, never editing markup.

No fonts are embedded anywhere — no licence to honour, and nothing that fails to
load on a device with a fixed font list.

## What the build guarantees

* Every internal link resolves, or is degraded to plain text before it ships.
* Every id in a document is unique.
* No page scrolls horizontally at any tested device width.
* Every article carries its authors, citation, identifier and licence.
* Anything missing is named and explained, in the book and in the report.
