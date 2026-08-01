# journal2epub

Turns a journal identifier and a volume number into a single EPUB that reads
like a genuine digital edition of that journal — cover, a volume table of
contents grouped into parts, article-type framing, figures and tables in place,
working citation links in both directions, and the journal's own visual
character.

```
build/gigascience-v12.epub
  included : 112 of 113
  missing  : 1 — listed in the book and in gigascience-v12.report.json
  report   : build/gigascience-v12.report.json
```

Editions are **unofficial and independently produced**. They carry no publisher
logo or trade dress, and every article carries its authors, original citation,
identifier and licence.

## Install

Needs **Python ≥3.11** and **Node.js** (Node only for typesetting mathematics —
see below).

```bash
git clone https://github.com/Billyzhang1229/journal2epub.git
cd journal2epub
uv sync                     # or: python -m venv .venv && .venv/bin/pip install -e .
npm install mathjax-full    # typesets the mathematics
```

Then check the install before spending time on a build:

```bash
uv run journal2epub doctor
```

```
journal2epub 0.1.0
  ok    python   3.13.9
  ok    journals configured   gigascience, plos-computational-biology
  ok    themes configured   gigascience, plos
  ok    themes pass contrast
  ok    maths renderer   node and MathJax are available
  ok    contact address   you@example.org
  info  cache: 0 cached responses at .cache/journal2epub
```

`doctor` exits non-zero if anything is wrong and tells you the fix.

<details>
<summary>Installing as a tool rather than from a checkout</summary>

`pip install .` and `uv tool install .` both work. The maths renderer ships
inside the package, but Node still has to be able to find MathJax, so either run
`npm install mathjax-full` in the directory you build from, or point at an
existing install:

```bash
export JOURNAL2EPUB_NODE_PATH=/path/to/node_modules
```

`journal2epub doctor` will tell you which of these you need.
</details>

## Use

Set a contact address once. The data providers ask clients to identify
themselves, and Crossref gives politely-identified clients a faster pool:

```bash
export JOURNAL2EPUB_CONTACT=you@example.org
```

Build a volume, or a single issue for journals that publish them:

```bash
journal2epub list                                     # what's configured
journal2epub build gigascience --volume 12
journal2epub build plos-computational-biology --volume 22 --issue 5
```

Each build writes two files — the edition and a machine-readable report
accounting for **every** article the registry lists, including the ones that
could not be included and why:

```bash
journal2epub summary gigascience-v12.report.json
```

A first build of a full volume downloads a few hundred megabytes and takes
roughly 10–20 minutes, most of it fetching figures. Everything is cached, so
building the same volume again takes about a minute, makes **zero** network
requests, and produces a byte-identical file. `--offline` refuses to touch the
network at all, which is how that claim is proved.

If a build is interrupted, run the same command again — it resumes from where
it stopped rather than starting over.

### Options worth knowing

| | |
|---|---|
| `--issue N` | for journals publishing discrete issues; omit for volume-only journals |
| `--offline` | serve only from cache, never touch the network |
| `--retry-failed` | re-queue articles that failed on an earlier run |
| `--fresh` | discard build state and start the volume over |
| `--limit N` | resolve only the first N articles (for a quick trial) |
| `--out PATH` | output path; defaults to `<journal>-v<volume>.epub` |

## How it gets the text

Through the sanctioned machine-access services, never the publisher's website:

| | |
|---|---|
| Crossref | enumerates what the volume should contain |
| PMC ID Converter | DOI → PMCID |
| PMC Open Access dataset on AWS | JATS full text, figures, article metadata |

Structured full text is the point. It already carries the section hierarchy,
author-to-affiliation binding, figure–caption pairing, table structure,
mathematics and parsed references that a scraper would discard and then have to
guess back. See [ADR 1](docs/adr/0001-structured-source-first.md).

Every response is cached, so a rebuild touches the network zero times and
produces a byte-identical file.

## Status

Built and verified against two journals from different publishers —
**GigaScience volumes 12 and 13** and **PLOS Computational Biology volume 22
issue 5** — 310 articles in all:

| Check | GigaScience v12 / v13 | PLOS Comp Biol v22 i5 |
|---|---|---|
| Articles | 112/113, 115/118 | 79/79 |
| `epubcheck` | 0 errors, 0 warnings | 0 errors, 0 warnings |
| `@daisy/ace` accessibility | 0 violations | 0 violations |
| Reflow, 360–768px | 0 pages scroll sideways | 0 pages scroll sideways |
| Unmodelled JATS elements | 0 | 0 |
| Mathematics | 1,082 TeX, all typeset | 6,532 MathML, all typeset |
| Warm rebuild | byte-identical, 0 requests | byte-identical, 0 requests |

60 tests passing. Not yet verified on physical e-ink hardware — see *Reader
testing* below.

Adding the second journal took two data files plus four code fixes, three of
which were latent defects in shared machinery that the first journal's shape had
hidden. That is written up honestly in
[ADR 5](docs/adr/0005-adapting-to-a-second-journal.md).

## Verifying a build

The three checks the project holds itself to. `epubcheck` and `@daisy/ace` are
external and only needed for this:

```bash
epubcheck build/gigascience-v12.epub                    # must be 0 errors
npx @daisy/ace -o ace-out build/gigascience-v12.epub    # must be 0 violations
uv run python tools/check_reflow.py build/gigascience-v12.epub
uv run pytest
```

`check_reflow.py` renders every page at four device widths and fails if any page
scrolls sideways; it needs `uv pip install playwright && playwright install
chromium`.

## Reader testing

Verified by rendering the built book in a Chromium engine — the same class of
engine most reading systems use — at 360, 400, 618 and 768 CSS pixels, checking
that no page scrolls horizontally, images fit, wide tables clip to their own
scroll region, and mathematics scales with the text.

**Not verified on physical e-ink hardware.** No e-reader software (Kindle
Previewer, Calibre, Thorium, Adobe Digital Editions) was installed on the build
machine and no e-ink device was reachable, so Kindle, Kobo and Adobe RMSDK
firmware behaviour is untested. The design avoids the usual causes of failure
there — no embedded fonts, no MathML, core EPUB image formats only, images
bounded to a 1600px long edge — but that is reasoning, not evidence.

## Adding a journal

Mostly two data files — a journal descriptor and a theme — for any journal
depositing JATS in the PMC Open Access Subset. Expect a new journal to also
surface one or two latent defects in shared code that the existing journals'
shape happened to hide; that is what happened adding PLOS, and it is worth the
audit. See [docs/adding-a-journal.md](docs/adding-a-journal.md) and
[ADR 5](docs/adr/0005-adapting-to-a-second-journal.md).

## Documentation

* [Architecture](docs/architecture.md)
* [ADR 1 — structured full text first, no scraping](docs/adr/0001-structured-source-first.md)
* [ADR 2 — PMC OA on AWS, not the retiring OA Web Service](docs/adr/0002-pmc-oa-on-aws.md)
* [ADR 3 — mathematics as SVG labelled with its TeX](docs/adr/0003-mathematics-as-svg.md)
* [ADR 4 — wide tables and horizontal reflow](docs/adr/0004-wide-tables-and-reflow.md)
* [ADR 5 — what adding a second journal actually cost](docs/adr/0005-adapting-to-a-second-journal.md)

## Licensing and attribution

The software is MIT licensed ([LICENSE](LICENSE)). **That does not extend to the
articles it packages** — each stays under its own open-access licence and its
own rights holders, and each is reproduced with its authors, citation, DOI and
licence terms attached automatically. Article data comes courtesy of the U.S.
National Library of Medicine, which does not endorse these editions; per NLM's
terms, no PMC wordmark or logo is used. See [NOTICE.md](NOTICE.md).
