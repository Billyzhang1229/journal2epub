"""EPUB 3 assembly.

Produces a reading edition: cover, title page, a volume table of contents
grouped into parts, the articles, and a colophon that says plainly what this is
and where every part of it came from.

Two things are non-negotiable here:
  * an incomplete book is never produced quietly — anything missing gets its own
    named, explained entry in the front matter and in the report;
  * the edition states that it is unofficial and independently produced, and
    carries no publisher logo or trade dress.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..config import JournalConfig, Theme
from ..model import Article, Asset, Resolution
from .css import stylesheet
from .images import CORE_TYPES, prepare
from .math import MathRenderer
from .xhtml import ArticleRenderer, RenderStats, attr, esc

log = logging.getLogger(__name__)

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

NS = ('xmlns="http://www.w3.org/1999/xhtml" '
      'xmlns:epub="http://www.idpf.org/2007/ops"')


def page(title: str, body: str, lang: str = "en", extra_head: str = "") -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>\n'
            f"<!DOCTYPE html>\n"
            f'<html {NS} lang="{lang}" xml:lang="{lang}">\n'
            f"<head><meta charset=\"utf-8\"/><title>{esc(title)}</title>"
            f'<link rel="stylesheet" type="text/css" href="style.css"/>{extra_head}</head>\n'
            f"<body>{body}</body>\n</html>\n")


@dataclass
class EditionArticle:
    article: Article
    part: str
    order: int
    filename: str = ""
    html: str = ""
    stats: RenderStats = field(default_factory=RenderStats)


@dataclass
class MissingArticle:
    doi: str
    title: str
    resolution: str
    note: str


@dataclass
class EpubBuilder:
    journal: JournalConfig
    theme: Theme
    volume: str
    math: MathRenderer
    issue: str = ""
    lang: str = "en"
    articles: list[EditionArticle] = field(default_factory=list)
    missing: list[MissingArticle] = field(default_factory=list)
    assets: dict[str, tuple[bytes, str, str]] = field(default_factory=dict)  # name -> (data, mime, note)
    registry_count: int = 0
    source_count: int = -1
    build_id: str = ""
    asset_notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """How this edition names itself: journals that number only by volume
        say 'Volume 12'; those that publish discrete issues say
        'Volume 22, Issue 5'."""
        return f"Volume {self.volume}" + (f", Issue {self.issue}" if self.issue else "")

    @property
    def short_label(self) -> str:
        return f"v{self.volume}" + (f"i{self.issue}" if self.issue else "")

    # ------------------------------------------------------------------
    def add_article(self, art: Article, part: str, order: int,
                    asset_bytes: dict[str, bytes]) -> EditionArticle:
        """Render one article and take ownership of its images."""
        embedded: dict[str, Asset] = {}
        for aid, a in art.assets.items():
            if a.role == "supplement" or not a.embedded:
                continue
            raw = asset_bytes.get(aid)
            if raw is None:
                a.embedded = False
                continue
            out = prepare(raw, a.filename, a.mimetype)
            if out is None or out.mimetype not in CORE_TYPES:
                a.embedded = False
                self.asset_notes.append(f"{art.front.pmcid}: dropped {a.filename} (unusable image)")
                continue
            name = f"{art.slug}-{out.filename}"
            self.assets[name] = (out.data, out.mimetype, out.note)
            a.filename, a.mimetype, a.path = name, out.mimetype, name
            if out.note:
                self.asset_notes.append(f"{art.front.pmcid}: {a.filename} {out.note}")
            embedded[aid] = a

        r = ArticleRenderer(article=art, journal=self.journal, theme=self.theme,
                            math=self.math, embedded_assets=embedded)
        body = r.render()
        ea = EditionArticle(article=art, part=part, order=order,
                            filename=f"art-{order:04d}-{art.slug}.xhtml",
                            html=page(art.front.title_text or "Article",
                                      f'<section epub:type="chapter" role="doc-chapter">{body}</section>',
                                      self.lang),
                            stats=r.stats)
        self.articles.append(ea)
        return ea

    # ------------------------------------------------------------------
    # front matter
    # ------------------------------------------------------------------
    def _cover_svg(self) -> str:
        """Compose the cover.

        SVG text neither wraps nor shrinks: a title too wide for the canvas is
        simply clipped. `GigaScience` fits at full size and `PLOS Computational
        Biology` overflows by about 470px, so the title has to be wrapped and
        scaled to the space rather than set at a fixed size and hoped for.
        """
        t, j = self.theme, self.journal
        MARGIN, W, H = 110, 1400, 2100
        avail = W - MARGIN * 2

        title_lines, title_size = fit_text(
            j.title, avail, max_size=96, min_size=52, max_lines=3, spacing=6)
        line_h = round(title_size * 1.14)

        top = 320
        title_svg = "".join(
            f'\n  <text x="{MARGIN}" y="{top + i * line_h}" '
            f'font-family="{esc(t.sans_stack)}" font-size="{title_size}" '
            f'font-weight="700" letter-spacing="6" fill="{t.cover_fg}">{esc(line)}</text>'
            for i, line in enumerate(title_lines))
        rule_y = top + (len(title_lines) - 1) * line_h + 78

        # The volume label is short but not bounded: "Volume 100, Issue 12" at
        # 120px already nearly fills the width. Keep it on one line and let it
        # shrink — breaking "Volume 100, Issue / 12" reads badly.
        label_lines, label_size = fit_text(
            self.label, avail, max_size=120, min_size=56, max_lines=1, spacing=0)
        label_h = round(label_size * 1.12)
        label_top = rule_y + 130
        label_svg = "".join(
            f'\n  <text x="{MARGIN}" y="{label_top + i * label_h}" '
            f'font-family="{esc(t.sans_stack)}" font-size="{label_size}" '
            f'font-weight="300" fill="{t.cover_fg}">{esc(line)}</text>'
            for i, line in enumerate(label_lines))
        count_y = label_top + (len(label_lines) - 1) * label_h + 118

        n = len(self.articles)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet">
  <title>{esc(j.title)} — {esc(self.label)}</title>
  <rect width="{W}" height="{H}" fill="{t.cover_bg}"/>
  <rect x="0" y="0" width="{W}" height="26" fill="{t.cover_accent}"/>{title_svg}
  <line x1="{MARGIN}" y1="{rule_y}" x2="{W - MARGIN}" y2="{rule_y}"
        stroke="{t.cover_accent}" stroke-width="6"/>{label_svg}
  <text x="{MARGIN}" y="{count_y}" font-family="{esc(t.sans_stack)}" font-size="46"
        fill="{t.cover_accent}">{n} article{'s' if n != 1 else ''}</text>
  <text x="{MARGIN}" y="1900" font-family="{esc(t.sans_stack)}" font-size="38"
        fill="{t.cover_fg}" opacity="0.75">Offline reading edition</text>
  <text x="{MARGIN}" y="1960" font-family="{esc(t.sans_stack)}" font-size="30"
        fill="{t.cover_fg}" opacity="0.55">Unofficial · independently produced</text>
  <text x="{MARGIN}" y="2020" font-family="{esc(t.sans_stack)}" font-size="30"
        fill="{t.cover_fg}" opacity="0.55">Open-access articles under their own licences</text>
</svg>
"""

    def _titlepage(self) -> str:
        j = self.journal
        n = len(self.articles)
        miss = ""
        if self.missing:
            miss = (f'<p class="edition-note">This edition is incomplete: '
                    f"{len(self.missing)} of {self.registry_count} articles could not be "
                    f'included. They are listed in <a href="not-included.xhtml">'
                    f"Articles not included</a>.</p>")
        return page("Title page", f"""
<section class="volume-title" epub:type="titlepage">
  <p class="journal-name">{esc(j.title)}</p>
  <p class="volume-line">{esc(self.label)}</p>
  <p>{n} article{'s' if n != 1 else ''}</p>
  <hr class="opener-rule"/>
  <p class="edition-note">
    An offline reading edition, independently produced from open-access full
    text. It is <strong>not</strong> published by, endorsed by, or affiliated
    with {esc(j.publisher or 'the publisher')} or {esc(j.title)}.
  </p>
  {miss}
</section>""", self.lang)

    def _toc_page(self) -> str:
        parts: dict[str, list[EditionArticle]] = {}
        for ea in self.articles:
            parts.setdefault(ea.part, []).append(ea)
        order = {r.name: r.order for r in self.journal.sections}
        body = ['<section epub:type="toc" role="doc-toc"><h1>Contents</h1>']
        for part in sorted(parts, key=lambda p: (order.get(p, 999), p)):
            body.append(f'<h2 class="toc-part">{esc(part)}</h2><ol>')
            for ea in parts[part]:
                f = ea.article.front
                authors = ", ".join(a.display for a in f.authors[:4])
                if len(f.authors) > 4:
                    authors += " et al."
                body.append(
                    f'<li class="toc-entry"><a href="{attr(ea.filename)}">'
                    f'<span class="toc-title">{esc(f.title_text)}</span></a>'
                    f'<span class="toc-authors">{esc(authors)}</span></li>')
            body.append("</ol>")
        body.append("</section>")
        return page("Contents", "".join(body), self.lang)

    def _missing_page(self) -> str:
        """Anything missing is loud, named and explained."""
        if not self.missing:
            body = ("<h1>Articles not included</h1><p>None — every article the "
                    "registry lists for this volume is present.</p>")
            return page("Articles not included", body, self.lang)
        why = {
            Resolution.NOT_IN_OA_SUBSET.value:
                "Full text is in PubMed Central but outside the Open Access Subset, "
                "so it may not be redistributed in an edition like this one.",
            Resolution.NO_PMCID.value:
                "No PubMed Central record could be found for this DOI, so no "
                "structured full text was available.",
            Resolution.NO_FULLTEXT.value:
                "A PubMed Central record exists but carries no full-text XML.",
            Resolution.PARSE_FAILED.value:
                "Full text was retrieved but could not be parsed into this "
                "edition's document model.",
            Resolution.FETCH_FAILED.value:
                "Full text could not be retrieved from the source service.",
            Resolution.RETRACTED.value:
                "The article is marked retracted in PubMed Central and has been "
                "left out deliberately.",
            Resolution.EXCLUDED.value:
                "Excluded by this edition's configuration.",
        }
        groups: dict[str, list[MissingArticle]] = {}
        for m in self.missing:
            groups.setdefault(m.resolution, []).append(m)
        out = [f"<h1>Articles not included</h1>",
               f"<p>The registry lists {self.registry_count} articles in "
               f"{esc(self.journal.title)} {esc(self.label.lower())}. "
               f"{len(self.articles)} are included here. The remaining "
               f"{len(self.missing)} could not be, for the reasons below.</p>"]
        for res, items in sorted(groups.items()):
            out.append(f"<h2>{esc(res.replace('-', ' ').title())} "
                       f"({len(items)})</h2>")
            out.append(f'<p class="why">{esc(why.get(res, ""))}</p>')
            out.append('<ol class="missing-list">')
            for m in items:
                doi = (f' <a href="https://doi.org/{attr(m.doi)}">'
                       f"https://doi.org/{esc(m.doi)}</a>") if m.doi else ""
                note = f'<br/><span class="why">{esc(m.note)}</span>' if m.note else ""
                out.append(f"<li>{esc(m.title or m.doi)}{doi}{note}</li>")
            out.append("</ol>")
        return page("Articles not included", "".join(out), self.lang)

    def _colophon(self) -> str:
        j = self.journal
        totals = _sum_stats(self.articles)
        math_note = ""
        if totals.math_fallback:
            math_note = (f"<dt>Mathematics not rendered</dt><dd>{totals.math_fallback} "
                         "expressions are shown as their original TeX source because "
                         "they could not be typeset.</dd>")
        img_note = ""
        if totals.figures_missing:
            img_note = (f"<dt>Figures unavailable</dt><dd>{totals.figures_missing} figures "
                        "were referenced by the text but absent from the open-access "
                        "package.</dd>")
        return page("About this edition", f"""
<section class="colophon" epub:type="colophon" role="doc-colophon">
<h1>About this edition</h1>

<p>This is an <strong>unofficial, independently produced</strong> offline
reading edition of {esc(j.title)} {esc(self.label.lower())}. It is not
published by, endorsed by, or affiliated with {esc(j.publisher or 'the publisher')},
and it carries none of the publisher's logos or trade dress.</p>

<h2>Where the text came from</h2>
<p>Articles were built from structured full text (NISO JATS XML) deposited in
PubMed Central and distributed through the PMC Open Access Subset, together
with article metadata from Crossref. Nothing was scraped from the publisher's
website.</p>
<p>Courtesy of the U.S. National Library of Medicine, which is the source of
the PubMed Central data used here. NLM does not endorse this edition.</p>

<h2>Licensing</h2>
<p>Every article is reproduced under its own open-access licence, and carries
its authors, original citation, identifier and licence terms at the end of the
article. Copyright in each article remains with its holder. Where an article's
licence requires attribution, that attribution is the citation block printed
with it.</p>

<h2>What is in this file</h2>
<dl>
<dt>Articles included</dt><dd>{len(self.articles)} of {self.registry_count}
listed{'' if not self.missing else f'; {len(self.missing)} not included'}</dd>
<dt>Figures</dt><dd>{totals.figures}</dd>
<dt>Tables</dt><dd>{totals.tables}{f' ({totals.wide_tables} wide)' if totals.wide_tables else ''}</dd>
<dt>Mathematical expressions</dt><dd>{totals.math_ok} typeset</dd>
{math_note}{img_note}
<dt>Built with</dt><dd>journal2epub {esc(__version__)}</dd>
<dt>Build identifier</dt><dd><code>{esc(self.build_id)}</code></dd>
</dl>

<h2>Reading notes</h2>
<p>This is a reflowable edition: your reader controls the typeface, size and
margins. Wide tables are given their own horizontal scroll region — turning the
device to landscape usually helps. Mathematics is drawn as scalable vector
graphics so it grows with your chosen text size, and each expression carries its
original TeX as its accessible label.</p>
</section>""", self.lang)

    # ------------------------------------------------------------------
    # package
    # ------------------------------------------------------------------
    def _nav(self) -> str:
        parts: dict[str, list[EditionArticle]] = {}
        for ea in self.articles:
            parts.setdefault(ea.part, []).append(ea)
        order = {r.name: r.order for r in self.journal.sections}

        items = ['<li><a href="titlepage.xhtml">Title page</a></li>',
                 '<li><a href="contents.xhtml">Contents</a></li>']
        for part in sorted(parts, key=lambda p: (order.get(p, 999), p)):
            sub = "".join(
                f'<li><a href="{attr(ea.filename)}">{esc(ea.article.front.title_text)}</a></li>'
                for ea in parts[part])
            items.append(f"<li><span>{esc(part)}</span><ol>{sub}</ol></li>")
        items.append('<li><a href="not-included.xhtml">Articles not included</a></li>')
        items.append('<li><a href="colophon.xhtml">About this edition</a></li>')

        body = (f'<nav epub:type="toc" role="doc-toc" id="toc"><h1>Contents</h1>'
                f'<ol>{"".join(items)}</ol></nav>'
                f'<nav epub:type="landmarks" hidden="hidden"><h1>Landmarks</h1><ol>'
                f'<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>'
                f'<li><a epub:type="titlepage" href="titlepage.xhtml">Title page</a></li>'
                f'<li><a epub:type="toc" href="contents.xhtml">Contents</a></li>'
                f'<li><a epub:type="bodymatter" href="{attr(self.articles[0].filename)}">Start</a></li>'
                f"</ol></nav>" if self.articles else "")
        return page("Contents", body, self.lang)

    def _modified(self) -> str:
        """The most recent moment any source in this edition was retrieved.

        Using the wall clock here would make every rebuild differ in one byte
        and destroy reproducibility, so the timestamp comes from the inputs."""
        stamps = [ea.article.provenance.retrieved for ea in self.articles
                  if ea.article.provenance.retrieved]
        if stamps:
            return max(stamps)
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _opf(self, modified: str, uid: str) -> str:
        j = self.journal
        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="css" href="style.css" media-type="text/css"/>',
            '<item id="cover-image" href="cover.svg" media-type="image/svg+xml" properties="cover-image"/>',
            '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="contents" href="contents.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="notincluded" href="not-included.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="colophon" href="colophon.xhtml" media-type="application/xhtml+xml"/>',
        ]
        spine = ['<itemref idref="cover"/>', '<itemref idref="titlepage"/>',
                 '<itemref idref="contents"/>']
        for i, ea in enumerate(self.articles):
            props = ' properties="svg"' if 'svg' in ea.html[:200000] and '<svg' in ea.html else ""
            manifest.append(f'<item id="a{i}" href="{attr(ea.filename)}" '
                            f'media-type="application/xhtml+xml"{props}/>')
            spine.append(f'<itemref idref="a{i}"/>')
        spine.append('<itemref idref="notincluded"/>')
        spine.append('<itemref idref="colophon"/>')
        for i, (name, (_d, mime, _n)) in enumerate(sorted(self.assets.items())):
            manifest.append(f'<item id="img{i}" href="images/{attr(name)}" media-type="{mime}"/>')

        creators = []
        seen = set()
        for ea in self.articles:
            for a in ea.article.front.authors[:3]:
                d = a.display
                if d and d not in seen:
                    seen.add(d)
                    creators.append(d)
        contrib = "".join(f"<dc:contributor>{esc(c)}</dc:contributor>" for c in creators[:60])

        title = f"{j.title} — {self.label}"
        desc = (f"An unofficial, independently produced offline reading edition of "
                f"{j.title} {self.label.lower()}, built from open-access full text. "
                f"Not published by or affiliated with {j.publisher or 'the publisher'}.")
        return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0"
         unique-identifier="pub-id" xml:lang="{self.lang}"
         prefix="schema: http://schema.org/">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">urn:uuid:{uid}</dc:identifier>
    <dc:title id="t">{esc(title)}</dc:title>
    <meta refines="#t" property="title-type">main</meta>
    <dc:language>{self.lang}</dc:language>
    <dc:creator>{esc(j.title)}</dc:creator>
    <dc:publisher>journal2epub (independent)</dc:publisher>
    <dc:description>{esc(desc)}</dc:description>
    <dc:source>{esc(j.issn)}</dc:source>
    <dc:rights>Each article is reproduced under its own open-access licence; copyright remains with the rights holders. This compilation is unofficial.</dc:rights>
    {contrib}
    <meta property="dcterms:modified">{modified}</meta>
    <meta property="belongs-to-collection" id="c1">{esc(j.title)}</meta>
    <meta refines="#c1" property="collection-type">series</meta>
    <meta refines="#c1" property="group-position">{esc(self.volume)}</meta>

    <meta property="schema:accessMode">textual</meta>
    <meta property="schema:accessMode">visual</meta>
    <meta property="schema:accessModeSufficient">textual</meta>
    <meta property="schema:accessibilityFeature">structuralNavigation</meta>
    <meta property="schema:accessibilityFeature">tableOfContents</meta>
    <meta property="schema:accessibilityFeature">readingOrder</meta>
    <meta property="schema:accessibilityFeature">alternativeText</meta>
    <meta property="schema:accessibilityFeature">longDescription</meta>
    <meta property="schema:accessibilityHazard">none</meta>
    <meta property="schema:accessibilitySummary">Every figure carries a text
      caption and alternative text, and mathematics is provided as scalable
      vector graphics labelled with its original TeX source. Navigation is by
      table of contents and by article. Tables are marked up as real tables
      with header cells. There are no flashing or motion hazards.</meta>
  </metadata>
  <manifest>
    {"".join(manifest)}
  </manifest>
  <spine>
    {"".join(spine)}
  </spine>
</package>
"""

    # ------------------------------------------------------------------
    def write(self, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Derived from the content, not the wall clock, so that rebuilding from
        # a warm cache produces a byte-identical file.
        modified = self._modified()
        uid = self.build_id or str(uuid.uuid4())

        files: list[tuple[str, bytes]] = []
        add = lambda n, s: files.append((n, s.encode("utf-8") if isinstance(s, str) else s))

        add("META-INF/container.xml", CONTAINER)
        add("OEBPS/style.css", stylesheet(self.theme))
        add("OEBPS/cover.svg", self._cover_svg())
        add("OEBPS/cover.xhtml", page("Cover", (
            '<section epub:type="cover" style="text-align:center;margin:0;padding:0">'
            '<img src="cover.svg" role="doc-cover" alt="Cover of this edition"'
            ' style="max-width:100%;height:auto"/></section>'), self.lang))
        add("OEBPS/titlepage.xhtml", self._titlepage())
        add("OEBPS/contents.xhtml", self._toc_page())
        add("OEBPS/not-included.xhtml", self._missing_page())
        add("OEBPS/colophon.xhtml", self._colophon())
        add("OEBPS/nav.xhtml", self._nav())
        for ea in self.articles:
            add(f"OEBPS/{ea.filename}", ea.html)
        for name, (data, _m, _n) in self.assets.items():
            add(f"OEBPS/images/{name}", data)
        add("OEBPS/package.opf", self._opf(modified, uid))

        tmp = out_path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w") as z:
            # The mimetype entry must come first and be stored uncompressed.
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                       zipfile.ZIP_STORED)
            for name, data in files:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, data)
        tmp.replace(out_path)
        log.info("wrote %s (%.1f MB)", out_path, out_path.stat().st_size / 1e6)
        return out_path


# Per-character advance widths as a fraction of font size, for a bold
# grotesque. Approximate on purpose: no font is embedded and the reader's own
# face is unknown, so this only has to be close enough to keep text inside the
# canvas. It errs wide, which clips nothing.
_NARROW = set("iljtfIrJ.,;:'!|()[]{}/\\-·")
_WIDE = set("mwMW@%")


def text_width(text: str, size: float, spacing: float = 0.0) -> float:
    """Estimated rendered width of a single line, in user units."""
    total = 0.0
    for ch in text:
        if ch == " ":
            total += 0.30
        elif ch in _NARROW:
            total += 0.32
        elif ch in _WIDE:
            total += 0.90
        elif ch.isupper() or ch.isdigit():
            total += 0.68
        else:
            total += 0.57
    return total * size + spacing * max(0, len(text) - 1)


def wrap_text(text: str, size: float, spacing: float, max_width: float) -> list[str]:
    """Greedy word wrap against the estimated width."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or text_width(candidate, size, spacing) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def fit_text(text: str, max_width: float, max_size: int, min_size: int,
             max_lines: int, spacing: float = 0.0) -> tuple[list[str], int]:
    """Largest size at which `text` wraps into at most `max_lines` lines.

    Returns the wrapped lines and the size to set them at. If even `min_size`
    will not fit — a pathologically long single word — the lines are returned
    anyway at `min_size`, because a slightly wide cover beats no cover.
    """
    for size in range(int(max_size), int(min_size) - 1, -2):
        lines = wrap_text(text, size, spacing, max_width)
        if len(lines) <= max_lines and all(
                text_width(l, size, spacing) <= max_width for l in lines):
            return lines, size
    return wrap_text(text, min_size, spacing, max_width), int(min_size)


def _sum_stats(articles: list[EditionArticle]) -> RenderStats:
    t = RenderStats()
    for ea in articles:
        s = ea.stats
        t.math_ok += s.math_ok
        t.math_fallback += s.math_fallback
        t.figures += s.figures
        t.figures_missing += s.figures_missing
        t.tables += s.tables
        t.wide_tables += s.wide_tables
        t.dangling_links += s.dangling_links
        t.supplements += s.supplements
    return t
