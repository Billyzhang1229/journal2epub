"""Internal model -> XHTML.

One renderer, because there is one model. Adapters absorb the differences
between sources; nothing publisher-specific reaches this module.

Two invariants it enforces:
  * every internal link resolves to an anchor that exists on the page, or it is
    degraded to plain text — a dangling fragment is an EPUB validation error;
  * every id emitted is unique within its document.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from ..config import JournalConfig, Theme
from ..model import (
    Abstract, Article, Asset, Block, CodeBlock, DefinitionList, DisplayMath,
    Emphasis, ExternalLink, Figure, FootnoteRef, Inline, InlineGraphic,
    InlineMath, InternalLink, ListBlock, Paragraph, Preformatted, Quote,
    Section, Statement, Supplement, Table, Text, inline_text,
)
from .math import MathRenderer, namespace_ids

EMPH_TAG = {
    "italic": "em", "bold": "strong", "sup": "sup", "sub": "sub",
    "mono": "code", "underline": "u", "strike": "s",
}


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def attr(s: str) -> str:
    return html.escape(s or "", quote=True)


@dataclass
class RenderStats:
    math_ok: int = 0
    math_fallback: int = 0
    figures: int = 0
    figures_missing: int = 0
    tables: int = 0
    wide_tables: int = 0
    dangling_links: int = 0
    supplements: int = 0


@dataclass
class ArticleRenderer:
    """Renders one article to an XHTML body fragment."""
    article: Article
    journal: JournalConfig
    theme: Theme
    math: MathRenderer
    embedded_assets: dict[str, Asset] = field(default_factory=dict)
    stats: RenderStats = field(default_factory=RenderStats)
    _ids: set[str] = field(default_factory=set)
    _math_seq: int = 0
    _region_labels: set[str] = field(default_factory=set)

    def _region_label(self, preferred: str) -> str:
        """Landmark regions must be distinguishable from one another, so two
        tables with the same caption cannot share an accessible name."""
        base = " ".join((preferred or "Table").split())[:120] or "Table"
        name, n = base, 2
        while name in self._region_labels:
            name = f"{base} ({n})"
            n += 1
        self._region_labels.add(name)
        return name

    # -- ids -------------------------------------------------------------
    def _id(self, raw: str | None) -> str | None:
        """Emit a unique, syntactically valid id, remembering it for link checks."""
        if not raw:
            return None
        v = re.sub(r"[^A-Za-z0-9_.-]", "-", raw)
        if not v or not v[0].isalpha():
            v = "x-" + v
        base, n = v, 2
        while v in self._ids:
            v = f"{base}-{n}"
            n += 1
        self._ids.add(v)
        return v

    def _idattr(self, raw: str | None) -> str:
        v = self._id(raw)
        return f' id="{attr(v)}"' if v else ""

    # ==================================================================
    # inline
    # ==================================================================
    def inlines(self, nodes: list[Inline]) -> str:
        return "".join(self._inline(n) for n in nodes)

    def _inline(self, n: Inline) -> str:
        match n:
            case Text():
                return esc(n.value)
            case Emphasis():
                inner = self.inlines(n.children)
                if n.kind == "smallcaps":
                    return f'<span style="font-variant:small-caps">{inner}</span>'
                return f"<{EMPH_TAG[n.kind]}>{inner}</{EMPH_TAG[n.kind]}>"
            case InternalLink():
                inner = self.inlines(n.children)
                anchor = f' id="{attr(n.anchor)}"' if n.anchor else ""
                cls = ' class="cite"' if n.kind == "bibr" else ""
                return (f'<a href="#{attr(n.target_id)}"{anchor}{cls}'
                        f' data-kind="{attr(n.kind)}">{inner}</a>')
            case ExternalLink():
                inner = self.inlines(n.children) or esc(n.href)
                href = sanitize_url(n.href) if _safe_href(n.href) else ""
                if not href:
                    return inner
                return f'<a href="{attr(href)}">{inner}</a>'
            case InlineMath():
                return self._math(n.source or "", n.alt, display=False,
                                  mathml=n.mathml or "")
            case InlineGraphic():
                a = self.embedded_assets.get(n.asset_id)
                if not a:
                    return esc(n.alt)
                return (f'<img src="images/{attr(a.filename)}" alt="{attr(n.alt)}"'
                        f' class="inline-graphic"/>')
            case FootnoteRef():
                return f'<sup><a href="#{attr(n.target_id)}">{esc(n.marker)}</a></sup>'
            case _:
                return ""
        return ""

    def _math(self, tex: str, alt: str, display: bool, mathml: str = "") -> str:
        """Inline SVG, labelled with the expression's readable source form.

        Takes whichever form the publisher deposited — TeX where there is TeX,
        MathML otherwise. The accessible label is the TeX where available (it
        reads far better than markup) and the expression's text content when the
        source was MathML.
        """
        cls = "math-display" if display else "math-inline"
        source, kind = (tex, "tex") if tex else (mathml, "mathml")
        label = alt or tex
        if not source:
            # No usable source at all. Counted, not silently blank.
            self.stats.math_fallback += 1
            return f'<span class="{cls}"><code class="math-fallback">{esc(label)}</code></span>'
        res = self.math.get(source, display, kind)
        if res.ok:
            self._math_seq += 1
            svg = namespace_ids(res.svg, f"m{self._math_seq}")
            # The accessible name has to sit on the <svg> itself — a label on a
            # wrapping element leaves the graphic unnamed to a screen reader.
            svg = _label_svg(svg, label)
            self.stats.math_ok += 1
            return f'<span class="{cls}">{svg}</span>'
        # Loud, not blank: the reader still sees the expression's source.
        self.stats.math_fallback += 1
        return f'<span class="{cls}"><code class="math-fallback">{esc(label)}</code></span>'

    # ==================================================================
    # blocks
    # ==================================================================
    def blocks(self, blocks: list[Block], depth: int = 2) -> str:
        return "".join(self._block(b, depth) for b in blocks)

    def _block(self, b: Block, depth: int) -> str:
        match b:
            case Paragraph():
                inner = self.inlines(b.children)
                return f"<p{self._idattr(b.id)}>{inner}</p>" if inner.strip() else ""
            case Section():
                lvl = min(max(depth, 2), 6)
                head = ""
                if b.title:
                    t = self.inlines(b.title)
                    # A <title> holding only whitespace would become an empty
                    # heading: an accessibility fault and a stray rule on screen.
                    if _plain(t):
                        head = f"<h{lvl}>{t}</h{lvl}>"
                # Heading levels must follow the document outline, not raw
                # nesting. A section that emits no heading must not push its
                # children down a level, or the outline gains a gap.
                inner = self.blocks(b.children, depth + 1 if head else depth)
                if not head and not inner.strip():
                    return ""
                return f"<section{self._idattr(b.id)}>{head}{inner}</section>"
            case Figure():
                return self._figure(b)
            case Table():
                return self._table(b)
            case ListBlock():
                tag = "ol" if b.ordered else "ul"
                items = "".join(f"<li>{self.blocks(i.children, depth)}</li>" for i in b.items)
                return f"<{tag}>{items}</{tag}>"
            case DefinitionList():
                out = ["<dl>"]
                for term, desc in b.items:
                    out.append(f"<dt>{self.inlines(term)}</dt>")
                    out.append(f"<dd>{self.blocks(desc, depth)}</dd>")
                out.append("</dl>")
                return "".join(out)
            case Quote():
                att = ""
                if b.attribution:
                    att = f"<footer>{self.inlines(b.attribution)}</footer>"
                return f"<blockquote>{self.blocks(b.children, depth)}{att}</blockquote>"
            case CodeBlock():
                return f"<pre><code>{esc(b.text)}</code></pre>"
            case Preformatted():
                return f"<pre>{esc(b.text)}</pre>"
            case DisplayMath():
                inner = self._math(b.source or "", b.alt, display=True,
                                   mathml=b.mathml or "")
                lab = f'<span class="eq-label">{esc(b.label)}</span>' if b.label else ""
                return f'<div class="equation"{self._idattr(b.id)}>{inner}{lab}</div>'
            case Supplement():
                return self._supplement(b)
            case Statement():
                title = ""
                if b.label or b.title:
                    t = " ".join(x for x in (b.label, inline_text(b.title)) if x)
                    title = f'<p class="boxed-title">{esc(t)}</p>'
                # The box's own title is a paragraph, not a heading, so its
                # contents stay at the surrounding heading level.
                return (f'<aside class="boxed"{self._idattr(b.id)}>{title}'
                        f"{self.blocks(b.children, depth)}</aside>")
        return ""

    def _figure(self, f: Figure) -> str:
        self.stats.figures += 1
        a = self.embedded_assets.get(f.asset_id or "")
        cap_txt = _plain(self.blocks(f.caption, 4))
        alt = f.alt or cap_txt[:300] or f.label or "Figure"
        if a and a.embedded and a.path:
            img = f'<img src="images/{attr(a.filename)}" alt="{attr(alt)}"/>'
        else:
            self.stats.figures_missing += 1
            img = ('<p class="fig-missing">[Figure not available in the open-access '
                   "package for this article.]</p>")
        label = f'<span class="label">{esc(f.label)}</span> ' if f.label else ""
        caption = self.blocks(f.caption, 4)
        # The caption is always text, never baked into the image.
        cap = f"<figcaption>{label}{caption}</figcaption>" if (label or caption) else ""
        return f"<figure{self._idattr(f.id)}>{img}{cap}</figure>"

    def _table(self, t: Table) -> str:
        self.stats.tables += 1
        label = f'<span class="table-label">{esc(t.label)}</span> ' if t.label else ""
        caption = self.blocks(t.caption, 4)
        head = f'<div class="table-caption">{label}{caption}</div>' if (label or caption) else ""

        if not t.rows:
            a = self.embedded_assets.get(t.graphic_asset_id or "")
            if a and a.embedded:
                alt = _plain(caption)[:300] or t.label or "Table"
                body = (f'<div class="table-scroll" tabindex="0" role="region"'
                        f' aria-label="{attr(self._region_label(t.label))}">'
                        f'<img src="images/{attr(a.filename)}" alt="{attr(alt)}"/></div>')
            else:
                body = '<p class="fig-missing">[Table not available.]</p>'
            return f'<div class="table-wrap"{self._idattr(t.id)}>{head}{body}</div>'

        cols = t.column_count
        # Wide scientific tables cannot reflow; step the type down and give the
        # table its own scroll region so it never widens the page body.
        cls = "cols-sm"
        if cols >= 13:
            cls = "cols-xl"
        elif cols >= 9:
            cls = "cols-lg"
        elif cols >= 6:
            cls = "cols-md"
        note = ""
        if cols >= 9:
            self.stats.wide_tables += 1
            note = (f'<p class="wide-note">{cols} columns — scroll sideways, or turn '
                    "the device to landscape, to see the whole table.</p>")

        rows_head = [r for r in t.rows if r.in_header]
        rows_body = [r for r in t.rows if not r.in_header]
        parts = ["<table>"]
        if rows_head:
            parts.append("<thead>" + "".join(self._row(r) for r in rows_head) + "</thead>")
        parts.append("<tbody>" + "".join(self._row(r) for r in rows_body) + "</tbody>")
        parts.append("</table>")
        foot = ""
        if t.footnotes:
            foot = f'<div class="table-note">{self.blocks(t.footnotes, 4)}</div>'
        # A scrollable region must be keyboard-reachable, or the content that
        # only appears after scrolling is unreachable without a mouse.
        label = attr(self._region_label(t.label or _plain(head)))
        return (f'<div class="table-wrap {cls}"{self._idattr(t.id)}>{head}{note}'
                f'<div class="table-scroll" tabindex="0" role="region"'
                f' aria-label="{label}">{"".join(parts)}</div>{foot}</div>')

    def _row(self, r) -> str:
        cells = []
        for c in r.cells:
            content = self.inlines(c.children)
            # A blank cell — the empty corner above a stub column, typically —
            # is not a header. Marking it up as one leaves screen readers
            # announcing an unnamed header for every cell in its column.
            header = c.header and bool(_plain(content))
            tag = "th" if header else "td"
            a = ""
            if c.rowspan > 1:
                a += f' rowspan="{c.rowspan}"'
            if c.colspan > 1:
                a += f' colspan="{c.colspan}"'
            if c.align in ("left", "right", "center"):
                a += f' style="text-align:{c.align}"'
            if header:
                a += ' scope="col"'
            cells.append(f"<{tag}{a}>{content}</{tag}>")
        return "<tr>" + "".join(cells) + "</tr>"

    def _supplement(self, s: Supplement) -> str:
        self.stats.supplements += 1
        label = f'<span class="label">{esc(s.label)}</span> ' if s.label else ""
        cap = self.blocks(s.caption, 4)
        note = ('<p class="supp-note">Supplementary file, available with the article '
                "online; not included in this edition.</p>")
        return f'<div class="supplement"{self._idattr(s.id)}>{label}{cap}{note}</div>'

    # ==================================================================
    # whole article
    # ==================================================================
    def render(self) -> str:
        a, f = self.article, self.article.front
        parts = [self._opener(), self._abstracts(), self._keywords()]
        parts.append(self.blocks(a.body, 2))
        parts.append(self._back())
        parts.append(self._references())
        parts.append(self._footnotes())
        parts.append(self._attribution())
        doc = "".join(p for p in parts if p)
        return self._resolve_links(doc)

    def _opener(self) -> str:
        f = self.article.front
        label = self.journal.label_for(f.article_type)
        for s in f.subjects:
            if not s.startswith("AcademicSubjects/"):
                label = s
                break
        aff_by_id = {x.id: x for x in f.affiliations if x.id}
        marks: dict[str, int] = {}
        for i, aff in enumerate([x for x in f.affiliations if x.id], start=1):
            marks[aff.id] = i

        names = []
        for au in f.authors:
            sup = "".join(
                f'<sup>{marks[r]}</sup>' for r in au.affiliation_ids if r in marks)
            corr = '<sup title="corresponding author">*</sup>' if au.corresponding else ""
            orcid = ""
            if au.orcid:
                orcid = (f' <a class="orcid" href="https://orcid.org/'
                         f'{attr(sanitize_url(au.orcid))}">iD</a>')
            names.append(f"{esc(au.display)}{sup}{corr}{orcid}")
        byline = f'<p class="byline">{", ".join(names)}</p>' if names else ""

        affs = ""
        if marks:
            items = "".join(
                f"<li><sup>{n}</sup> {esc(aff_by_id[i].text)}</li>"
                for i, n in marks.items() if i in aff_by_id and aff_by_id[i].text)
            if items:
                affs = f'<ol class="affiliations">{items}</ol>'

        title = self.inlines(f.title) or esc(f.title_text) or "Untitled"
        sub = f"<p class=\"subtitle\">{self.inlines(f.subtitle)}</p>" if f.subtitle else ""
        return (f'<header class="opener">'
                f'<p class="article-type">{esc(label)}</p>'
                f"<h1>{title}</h1>{sub}"
                f'<hr class="opener-rule"/>{byline}{affs}</header>')

    def _abstracts(self) -> str:
        out = []
        for ab in self.article.front.abstracts:
            body = self.blocks(ab.blocks, 3)
            if not body.strip():
                continue
            out.append(f'<section class="abstract"{epub_type("abstract")}>'
                       f"<h2>{esc(ab.title)}</h2>{body}</section>")
        return "".join(out)

    def _keywords(self) -> str:
        kws = [k for k in self.article.front.keywords if k]
        if not kws:
            return ""
        return (f'<p class="keywords"><span class="kw-label">Keywords:</span> '
                f"{esc('; '.join(kws))}</p>")

    def _back(self) -> str:
        out = []
        for s in self.article.back:
            out.append(self._block(s, 2))
        notes = self.article.front.author_notes
        if notes:
            body = self.blocks(notes, 3)
            if body.strip():
                out.append(f'<section class="author-notes"><h2>Author notes</h2>{body}</section>')
        return "".join(out)

    def _references(self) -> str:
        refs = self.article.references
        if not refs:
            return ""
        items = []
        for r in refs:
            text = self.inlines(r.text) or esc(r.title or r.source or "")
            links = []
            if r.doi:
                links.append(f'<a href="https://doi.org/{attr(sanitize_url(r.doi))}">doi</a>')
            elif r.url and _safe_href(r.url):
                links.append(f'<a href="{attr(sanitize_url(r.url))}">link</a>')
            # Back-links: let the reader jump to where the citation was made.
            backs = ""
            if r.cited_by:
                spans = ", ".join(
                    f'<a href="#{attr(c)}">{i}</a>' for i, c in enumerate(r.cited_by, 1))
                backs = f' <span class="backlinks">[cited at {spans}]</span>'
            extra = f' <span class="ref-links">{" ".join(links)}</span>' if links else ""
            items.append(f'<li{self._idattr(r.id)}>{text}{extra}{backs}</li>')
        return (f'<section class="references"{epub_type("bibliography")}>'
                f"<h2>References</h2><ol>{''.join(items)}</ol></section>")

    def _footnotes(self) -> str:
        fns = [f for f in self.article.footnotes if f.children]
        if not fns:
            return ""
        items = []
        for fn in fns:
            body = self.blocks(fn.children, 4)
            if not body.strip():
                continue
            lab = f"<b>{esc(fn.label)}</b> " if fn.label else ""
            items.append(f'<li{self._idattr(fn.id)}>{lab}{body}</li>')
        if not items:
            return ""
        return (f'<section class="footnotes"{epub_type("footnotes")}>'
                f"<h2>Notes</h2><ol>{''.join(items)}</ol></section>")

    def _attribution(self) -> str:
        """Per-article attribution. These articles are openly licensed on the
        condition that each carries its authors, citation, identifier and
        licence, so this is injected automatically and is not optional."""
        f = self.article.front
        lines = [f'<p class="cite-line">{esc(f.citation())}</p>']
        if f.doi:
            lines.append(f'<p>Original article: '
                         f'<a href="https://doi.org/{attr(sanitize_url(f.doi))}">'
                         f"https://doi.org/{esc(f.doi)}</a></p>")
        ids = []
        if f.pmcid:
            ids.append(f"PMC: {esc(f.pmcid)}")
        if f.pmid:
            ids.append(f"PMID: {esc(f.pmid)}")
        if ids:
            lines.append(f"<p>{' · '.join(ids)}</p>")
        lic = f.license
        if lic.copyright_statement:
            lines.append(f"<p>{esc(lic.copyright_statement)}</p>")
        if lic.statement:
            lines.append(f"<p>{esc(lic.statement)}</p>")
        # The licence link is emitted whether or not a prose statement exists.
        # CC licences require the licence itself to be linked, and the deposited
        # statement does not reliably contain a resolvable link.
        if lic.url:
            lines.append(f'<p>Licence: <a href="{attr(sanitize_url(lic.url))}">'
                         f"{esc(lic.code or lic.url)}</a></p>")
        elif lic.code:
            lines.append(f"<p>Licence: {esc(lic.code)}</p>")
        return f'<section class="attribution">{"".join(lines)}</section>'

    # ==================================================================
    # link integrity
    # ==================================================================
    def _resolve_links(self, doc: str) -> str:
        """Degrade any internal link whose target is not on this page.

        JATS routinely cross-references material that is not in the deposit —
        a supplementary file, a table only present as a graphic, an appendix in
        another article. A dangling fragment is an EPUB validation error, so
        the link becomes plain text rather than a broken promise."""
        ids = set(re.findall(r'\bid="([^"]+)"', doc))
        n = 0

        def repl(m: re.Match) -> str:
            target, inner = m.group(1), m.group(2)
            if target in ids:
                return m.group(0)
            nonlocal n
            n += 1
            return inner

        doc = re.sub(r'<a href="#([^"]+)"[^>]*>(.*?)</a>', repl, doc, flags=re.S)
        self.stats.dangling_links += n
        return doc


def _plain(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _label_svg(svg: str, label: str) -> str:
    """Give an inline SVG an accessible name.

    The maths is a graphic, so it needs `role="img"` and a name on the `<svg>`
    element itself; a `<title>` child is added as well because some assistive
    technology prefers it. The name is the expression's original TeX.
    """
    m = re.match(r"<svg\b([^>]*)>", svg)
    if not m:
        return svg
    attrs = m.group(1)
    add = ""
    if "role=" not in attrs:
        add += ' role="img"'
    if "aria-label=" not in attrs:
        add += f' aria-label="{attr(label)}"'
    head = f"<svg{attrs}{add}>"
    title = f"<title>{esc(label)}</title>"
    return head + title + svg[m.end():]


# epub:type values that have a matching DPUB-ARIA role. Without the role,
# assistive technology cannot act on the structural semantics.
EPUB_TYPE_ROLE = {
    "abstract": "doc-abstract",
    "acknowledgments": "doc-acknowledgments",
    "afterword": "doc-afterword",
    "appendix": "doc-appendix",
    "bibliography": "doc-bibliography",
    "chapter": "doc-chapter",
    "colophon": "doc-colophon",
    "conclusion": "doc-conclusion",
    "cover": "doc-cover",
    "credits": "doc-credits",
    "dedication": "doc-dedication",
    "endnotes": "doc-endnotes",
    "epigraph": "doc-epigraph",
    "epilogue": "doc-epilogue",
    "errata": "doc-errata",
    "footnotes": "doc-endnotes",
    "foreword": "doc-foreword",
    "glossary": "doc-glossary",
    "index": "doc-index",
    "introduction": "doc-introduction",
    "noteref": "doc-noteref",
    "preface": "doc-preface",
    "prologue": "doc-prologue",
    "pullquote": "doc-pullquote",
    "qna": "doc-qna",
    "subtitle": "doc-subtitle",
    "toc": "doc-toc",
}


def epub_type(value: str) -> str:
    """Emit an epub:type together with its matching ARIA role."""
    role = EPUB_TYPE_ROLE.get(value)
    return f' epub:type="{attr(value)}"' + (f' role="{role}"' if role else "")


# Characters that may not appear literally in a URI (RFC 3986).
_ILLEGAL_IN_URI = set(' "<>[\\]^`{|}')


def sanitize_url(href: str) -> str:
    """Make a deposited URL into something that is actually a valid URI.

    Two real problems in this corpus:

      * Legacy DOIs from the pre-2005 registration era embed `<`, `>`, `[` and
        `]` — `10.1002/1615-9861(200209)2:9<1146::AID-PROT1146>3.0.CO;2-6` is a
        genuine, resolvable DOI. Those characters have to be percent-encoded to
        be legal in an href, and doi.org resolves the encoded form.
      * Typesetting artefacts leak into `xlink:href`: a hyphen inside a URL
        gets replaced by the publisher's hyphenation-point element, so the
        attribute reads `https://smart<plxhyp>PLXHYP</plxhyp>api.info/...`
        where the text content correctly reads `https://smart-api.info/...`.

    Existing percent-escapes are preserved rather than double-encoded.
    """
    h = (href or "").strip()
    if not h:
        return ""
    # Hyphenation-point markers stand in for the hyphen they replaced.
    h = re.sub(r"<\s*plxhyp[^>]*>.*?</\s*plxhyp\s*>", "-", h, flags=re.I | re.S)
    h = re.sub(r"\s+", "", h)

    out: list[str] = []
    i = 0
    while i < len(h):
        c = h[i]
        if c == "%":
            if re.match(r"%[0-9A-Fa-f]{2}", h[i:i + 3]):
                out.append(h[i:i + 3])
                i += 3
                continue
            out.append("%25")
        elif c in _ILLEGAL_IN_URI or ord(c) < 0x21 or ord(c) == 0x7F:
            out.append(f"%{ord(c):02X}")
        elif ord(c) > 0x7F:
            out.append("".join(f"%{b:02X}" for b in c.encode("utf-8")))
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _safe_href(href: str) -> bool:
    h = (href or "").strip().lower()
    return h.startswith(("http://", "https://", "mailto:", "ftp://"))
