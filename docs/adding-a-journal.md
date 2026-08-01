# Adding a journal

For a journal that deposits JATS in the PMC Open Access Subset — most
open-access biomedical and life-science journals — this is two data files and no
code.

## 1. Verify the journal's identifiers

Never take these from memory. Check them.

```bash
# Title, publisher, and that the ISSN is the one you think it is
curl -s "https://api.crossref.org/journals/2047-217X" | python3 -m json.tool | head -20

# How many articles PMC indexes for a volume, and the exact journal
# abbreviation it files them under
curl -s 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pmc&retmode=json&retmax=0&term=%22Gigascience%22%5BJournal%5D+AND+12%5Bvolume%5D'
```

The `nlm_ta` value is PMC's own abbreviation (`Gigascience`, not
`GigaScience`) and is only used for the cross-check count in the report.

## 2. Write the journal descriptor

### Naming the key

The key is the filename and the word you type on the command line. Derive it
mechanically from the journal's full title: lowercase it, and replace each run
of non-alphanumeric characters with a single hyphen.

| Title | Key |
|---|---|
| GigaScience | `gigascience` |
| PLOS Computational Biology | `plos-computational-biology` |

Resist the temptation to invent a contraction. `ploscompbiol` is shorter and is
even PLOS's own URL slug, but a reader of the repository cannot derive it from
the title, and it makes the key set look arbitrary next to `gigascience`. The
key is only a lookup — it never reaches the reader — so it should cost nothing
to guess.

### The three names, which are all different

A journal descriptor carries three names because they genuinely differ, and
mixing them up is a good way to get a zero-result cross-check:

* `title` — the full name. This is what appears on the cover, the title page,
  the EPUB metadata and every article's citation line.
* `nlm_ta` — PMC's own abbreviation (`PLoS Comput Biol`, `Gigascience`). Used
  *only* for the esearch cross-check count; PMC will not match the full title.
* the key — the command-line handle, as above.

`src/journal2epub/data/journals/<key>.toml`:

```toml
[journal]
title = "GigaScience"
issn = "2047-217X"
publisher = "Oxford University Press"
nlm_ta = "Gigascience"
source = "pmc_jats"
theme = "gigascience"

[[section]]
name = "Research"
types = ["research-article"]
order = 10

[[section]]
name = "Data Notes"
subjects = ["Data Note", "Datanote"]
order = 20

[type_labels]
research-article = "Research"
```

`[[section]]` entries group the volume's contents into parts, which set both the
contents page and the reading order.

**Subject matches beat type matches.** Journals routinely tag Data Notes and
Technical Notes as `research-article` and distinguish them only by subject, so a
type rule would otherwise swallow them. List every spelling variant the journal
actually uses — run the survey below to find them.

## 3. Write a theme

`src/journal2epub/data/themes/<key>.toml`. Colour, type stack, heading scale,
label treatment, rule weights. Two things worth getting right:

* **Colours must survive greyscale.** E-ink renders them as grey; an accent that
  is distinguishable only by hue disappears.
* **Name only fonts readers actually have,** and end every stack with a generic
  family. Nothing is embedded, by design.

Aim for editorial identity, not a facsimile of the website. Reflowable EPUB
hands font, size and margins to the reader, so pixel fidelity is neither
possible nor worth chasing.

## 4. Survey before you build

```bash
uv run python tools/parse_survey.py <key> <volume>
```

This parses the whole volume without packaging anything, and prints the article
types, the subject spellings, content counts, and — most importantly — the
**unhandled element log**.

Do not move on while that log has entries. Each one is an element the parser met
and chose not to model; either add it to the model or add it to the transparent
or consumed sets in `sources/jats.py` with a reason.

## 5. Build and check

```bash
uv run journal2epub build <key> --volume <n> --contact you@example.org
epubcheck <key>-v<n>.epub                       # must be 0 errors
npx @daisy/ace -o ace-out <key>-v<n>.epub       # must be 0 serious violations
uv run python tools/check_reflow.py <key>-v<n>.epub   # must be 0 overflow
uv run journal2epub summary <key>-v<n>.report.json
```

## If the content is somewhere else

A journal not in PMC needs a new source adapter: implement `discover` and
`fetch` from `sources/base.py`, map into the internal model, and set
`source = "<name>"` in the descriptor. Nothing downstream changes.

Before writing a scraping adapter, read
[ADR 1](adr/0001-structured-source-first.md) — that decision is deliberate and
should not be reversed casually.
