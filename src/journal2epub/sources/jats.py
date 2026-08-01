"""JATS XML -> internal model.

JATS is large and permissive; the internal model is small and closed. This
module owns the whole mapping. Anything it meets and chooses not to model is
appended to `Article.unhandled` with its tag and the article it came from,
because that log is the best available signal about what the model still lacks.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from lxml import etree

from ..model import (
    Abstract, Affiliation, Article, ArticleFront, Asset, Author, Block, CodeBlock,
    DefinitionList, DisplayMath, Emphasis, ExternalLink, Figure, Footnote,
    FootnoteRef, Inline, InlineGraphic, InlineMath, InternalLink, JournalRef,
    License, LineBreak, ListBlock, ListItem, Paragraph, Preformatted, PubDate,
    Quote, Reference, Section, Statement, Supplement, Table, TableCell, TableRow,
    Text, Unhandled, inline_text, walk_inlines,
)

log = logging.getLogger(__name__)

XLINK = "http://www.w3.org/1999/xlink"
MML = "http://www.w3.org/1998/Math/MathML"
ALI = "http://www.niso.org/schemas/ali/1.0/"
HREF = f"{{{XLINK}}}href"

# Inline JATS elements -> model emphasis kinds.
EMPH = {
    "italic": "italic", "bold": "bold", "sup": "sup", "sub": "sub",
    "sc": "smallcaps", "monospace": "mono", "underline": "underline",
    "strike": "strike", "roman": "italic",
}

# xref/@ref-type -> internal link kind
REF_KIND = {
    "bibr": "bibr", "fig": "fig", "table": "table", "supplementary-material": "supp",
    "fn": "fn", "sec": "sec", "aff": "aff", "corresp": "corresp",
    "disp-formula": "disp-formula", "boxed-text": "other", "app": "sec",
    "table-fn": "fn", "other": "other",
}

# Inline elements we intentionally pass through without their own construct.
INLINE_TRANSPARENT = {
    "styled-content", "named-content", "abbrev", "milestone-start", "milestone-end",
    "target", "x", "alt-text", "private-char", "phrase", "person-group",
    # Address and contact parts, as found in correspondence notes: the
    # publisher's punctuation between them is already in the tails.
    "addr-line", "country", "city", "state", "postal-code", "fax", "phone",
    "institution-id", "addr-line-1", "addr-line-2",
}

# The parts of a <mixed-citation>. Their text is already in reading order, so
# the reference renders as the publisher punctuated it; the structured values
# are read separately in `references()`. Transparent, and never "unhandled".
CITATION_PARTS = {
    "name", "string-name", "surname", "given-names", "prefix", "suffix", "collab",
    "article-title", "chapter-title", "data-title", "part-title", "trans-title",
    "trans-source", "source", "year", "month", "day", "season", "volume",
    "issue", "fpage", "lpage", "page-range", "elocation-id", "pub-id",
    "publisher-name", "publisher-loc", "edition", "series", "isbn", "issn",
    "conf-name", "conf-loc", "conf-date", "etal", "comment", "annotation",
    "access-date", "date-in-citation", "patent", "gov", "std", "size",
    "institution", "institution-wrap", "role", "version", "supplement",
}

# Block-level JATS elements. Used to decide whether an unmodelled container
# should be walked as blocks or read as a single paragraph of inline content.
BLOCK_TAGS = {
    "sec", "p", "fig", "fig-group", "table-wrap", "table-wrap-group", "list",
    "def-list", "disp-quote", "disp-formula", "disp-formula-group", "code",
    "preformat", "supplementary-material", "boxed-text", "statement",
    "verse-group", "array", "ack", "app", "app-group", "glossary", "notes",
    "ref-list", "fn-group", "bio", "sig-block", "abstract", "trans-abstract",
}

# Elements handled by their parent, so they must not be reported as unhandled.
CONSUMED = {
    "label", "caption", "title", "graphic", "alternatives", "attrib",
    "tex-math", "mml:math", "list-item", "term", "def", "def-item",
    "tr", "td", "th", "thead", "tbody", "tfoot", "col", "colgroup", "table",
    "object-id", "long-desc", "permissions", "processing-meta",
}


def _ln(el) -> str:
    """Local name, namespace stripped."""
    t = el.tag
    if not isinstance(t, str):
        return "#comment"
    return t.rsplit("}", 1)[-1]


def _txt(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


class JatsParser:
    """One instance per article."""

    def __init__(self, article_id: str = "") -> None:
        self.article_id = article_id
        self.unhandled: list[Unhandled] = []
        self.assets: dict[str, Asset] = {}
        self._asset_base = ""
        self._seen_unhandled: set[tuple[str, str]] = set()

    # -- diagnostics -----------------------------------------------------
    def _unhandled(self, el, path: str, note: str = "") -> None:
        tag = _ln(el)
        if tag in CONSUMED or tag == "#comment":
            return
        key = (tag, path)
        if key in self._seen_unhandled:
            return
        self._seen_unhandled.add(key)
        sample = re.sub(r"\s+", " ", (etree.tostring(el, encoding="unicode")[:180]))
        self.unhandled.append(Unhandled(tag=tag, article_id=self.article_id,
                                        path=path, note=note, sample=sample))

    # ==================================================================
    # Inline
    # ==================================================================
    def inlines(self, el, path: str = "") -> list[Inline]:
        out: list[Inline] = []
        if el.text:
            out.append(Text(_strip_artifacts(el.text)))
        for ch in el:
            out.extend(self._inline_node(ch, path))
            if ch.tail:
                out.append(Text(_strip_artifacts(ch.tail)))
        return _merge_text(out)

    def _inline_node(self, ch, path: str) -> list[Inline]:
        tag = _ln(ch)
        if tag == "#comment":
            return []
        if tag in EMPH:
            return [Emphasis(kind=EMPH[tag], children=self.inlines(ch, path))]
        if tag == "xref":
            rid = (ch.get("rid") or "").split()[0] if ch.get("rid") else ""
            kind = REF_KIND.get(ch.get("ref-type", ""), "other")
            kids = self.inlines(ch, path)
            if not rid:
                return kids
            return [InternalLink(target_id=rid, kind=kind, children=kids)]
        if tag in ("ext-link", "uri"):
            # Clean the source's typesetting artefacts here, so the model holds
            # the real URL; percent-encoding it is the renderer's job.
            href = _strip_artifacts(ch.get(HREF) or _txt(ch))
            kids = self.inlines(ch, path) or [Text(href)]
            return [ExternalLink(href=href, children=kids)]
        if tag == "inline-formula":
            return [self._math_inline(ch)]
        if tag == "inline-graphic":
            a = self._asset(ch.get(HREF), role="inline")
            return [InlineGraphic(asset_id=a.id, alt=ch.get("alt-text", ""))] if a else []
        if tag == "break":
            return [LineBreak()]
        if tag == "fn":
            # Footnote written inline; the caller lifts it into the note list.
            fid = ch.get("id") or ""
            return [FootnoteRef(target_id=fid, marker=_txt(ch.find("label")) or "*")]
        if tag in INLINE_TRANSPARENT or tag in CITATION_PARTS:
            return self.inlines(ch, path)
        if tag == "email":
            addr = _txt(ch)
            return [ExternalLink(href=f"mailto:{addr}", children=[Text(addr)])]
        if tag == "related-article":
            # An empty pointer element; its target is metadata, not prose.
            return []
        if tag in ("p", "list", "list-item", "def-list"):
            # Block content in a context that only takes inline content — a
            # bulleted list inside a table cell, say. Flatten it onto lines
            # rather than lose it.
            return self._flatten_block_inline(ch, path)
        # Structural things sometimes appear mid-paragraph; let the block
        # walker deal with them and keep only their text here.
        self._unhandled(ch, f"{path}/inline")
        return self.inlines(ch, path)

    def _flatten_block_inline(self, el, path: str) -> list[Inline]:
        """Render block content as inline, separating parts with line breaks."""
        tag = _ln(el)
        if tag in ("list", "def-list"):
            out: list[Inline] = []
            for i, item in enumerate(el):
                if _ln(item) not in ("list-item", "def-item"):
                    continue
                if i:
                    out.append(LineBreak())
                out.extend(self._flatten_block_inline(item, path))
            return out
        parts: list[Inline] = []
        kids = [c for c in el if _ln(c) in ("p", "list", "def", "term")]
        if kids:
            for i, c in enumerate(kids):
                if i:
                    parts.append(LineBreak())
                parts.extend(self._flatten_block_inline(c, path))
            return parts
        return self.inlines(el, path)

    def _math_inline(self, el) -> InlineMath:
        mathml, source, kind = self._math_parts(el)
        return InlineMath(mathml=mathml, source=source, source_kind=kind,
                          alt=source or _txt(el))

    def _math_parts(self, el) -> tuple[str | None, str | None, str]:
        """Pull MathML and the original source form out of a formula element."""
        mml = el.find(f".//{{{MML}}}math")
        tex = el.find(".//tex-math")
        mathml = None
        if mml is not None:
            mathml = etree.tostring(mml, encoding="unicode")
        source, kind = None, "none"
        if tex is not None and (tex.text or "").strip():
            source, kind = _clean_tex(tex.text), "tex"
        elif mathml:
            source, kind = None, "mathml"
        return mathml, source, kind

    # ==================================================================
    # Blocks
    # ==================================================================
    def blocks(self, parent, path: str = "", level: int = 1) -> list[Block]:
        out: list[Block] = []
        for el in parent:
            b = self._block_node(el, path, level)
            if b:
                out.extend(b)
        return out

    def _block_node(self, el, path: str, level: int) -> list[Block]:
        tag = _ln(el)
        if tag == "#comment":
            return []
        p = f"{path}/{tag}"

        if tag == "sec":
            title_el = el.find("title")
            kids = [c for c in el if c is not title_el]
            return [Section(
                title=self.inlines(title_el, p) if title_el is not None else [],
                children=self.blocks(kids, p, level + 1),
                id=el.get("id"), level=level, sec_type=el.get("sec-type"),
            )]
        if tag == "p":
            return self._paragraph(el, p)
        if tag == "fig":
            return [self._figure(el, p)]
        if tag == "fig-group":
            return [self._figure(f, p) for f in el.findall("fig")]
        if tag == "table-wrap":
            return [self._table(el, p)]
        if tag == "table-wrap-group":
            return [self._table(t, p) for t in el.findall("table-wrap")]
        if tag == "list":
            return [self._list(el, p, level)]
        if tag == "def-list":
            return [self._def_list(el, p, level)]
        if tag == "disp-quote":
            attrib = el.find("attrib")
            return [Quote(
                children=self.blocks([c for c in el if c is not attrib], p, level),
                attribution=self.inlines(attrib, p) if attrib is not None else [],
            )]
        if tag == "disp-formula":
            mathml, source, kind = self._math_parts(el)
            return [DisplayMath(mathml=mathml, source=source, source_kind=kind,
                                id=el.get("id"), label=_txt(el.find("label")),
                                alt=source or _txt(el))]
        if tag == "disp-formula-group":
            return self.blocks(el, p, level)
        if tag == "supplementary-material":
            return [self._supplement(el, p, level)]
        if tag in ("boxed-text", "statement"):
            title_el = el.find("title")
            kids = [c for c in el if c is not title_el and _ln(c) != "label"]
            return [Statement(
                id=el.get("id"), label=_txt(el.find("label")),
                title=self.inlines(title_el, p) if title_el is not None else [],
                children=self.blocks(kids, p, level + 1), kind=tag,
            )]
        if tag in ("code", "preformat"):
            text = el.text or "".join(el.itertext())
            if tag == "code":
                return [CodeBlock(text=text, language=el.get("language"))]
            return [Preformatted(text=text)]
        if tag == "verse-group":
            return [Quote(children=self.blocks(el, p, level))]
        if tag in ("graphic", "media"):
            # A bare graphic outside a figure: keep it, unlabelled.
            a = self._asset(el.get(HREF), role="figure")
            if a and a.mimetype and a.mimetype.startswith("image/"):
                return [Figure(id=el.get("id") or a.id, asset_id=a.id,
                               alt=el.get("alt-text", ""))]
            return []
        if tag == "array":
            tbl = el.find("table")
            if tbl is not None:
                return [self._table(el, p)]
        if tag == "corresp":
            # Correspondence block in author notes: inline content, own anchor.
            inl = self.inlines(el, p)
            return [Paragraph(children=inl, id=el.get("id"))] if inline_text(inl).strip() else []
        if tag in ("label", "caption", "title", "object-id", "attrib",
                   "alternatives", "permissions", "processing-meta"):
            return []
        if tag in ("fn", "fn-group", "ack", "notes", "glossary", "app-group", "app",
                   "ref-list", "bio", "sig-block", "front-stub"):
            # Handled by the back-matter and footnote passes.
            return []

        self._unhandled(el, path or "body")
        # Salvage rather than drop: walk as a container only when it really
        # holds blocks, otherwise read it as one paragraph of inline content.
        if any(_ln(c) in BLOCK_TAGS for c in el):
            return self.blocks(el, p, level)
        t = self.inlines(el, p)
        return [Paragraph(children=t)] if inline_text(t).strip() else []

    def _paragraph(self, el, path: str) -> list[Block]:
        """A JATS <p> may legally contain block-level floats. Split them out so
        the model never nests a figure inside a paragraph."""
        BLOCKISH = {"fig", "table-wrap", "list", "disp-quote", "disp-formula",
                    "supplementary-material", "boxed-text", "def-list", "code",
                    "preformat", "fig-group", "table-wrap-group", "statement",
                    "disp-formula-group", "verse-group"}
        if not any(_ln(c) in BLOCKISH for c in el):
            inl = self.inlines(el, path)
            return [Paragraph(children=inl, id=el.get("id"))] if inline_text(inl).strip() else []

        out: list[Block] = []
        buf: list[Inline] = []
        if el.text:
            buf.append(Text(el.text))

        def flush():
            if inline_text(buf).strip():
                out.append(Paragraph(children=_merge_text(list(buf))))
            buf.clear()

        for ch in el:
            if _ln(ch) in BLOCKISH:
                flush()
                out.extend(self._block_node(ch, path, 1))
                if ch.tail:
                    buf.append(Text(ch.tail))
            else:
                buf.extend(self._inline_node(ch, path))
                if ch.tail:
                    buf.append(Text(ch.tail))
        flush()
        return out

    def _figure(self, el, path: str) -> Figure:
        gr = el.findall(".//graphic")
        assets = [self._asset(g.get(HREF), role="figure") for g in gr]
        assets = [a for a in assets if a]
        cap = el.find("caption")
        alt = _txt(el.find("alt-text")) or _txt(el.find(".//alt-text"))
        return Figure(
            id=el.get("id") or (assets[0].id if assets else "fig"),
            label=_txt(el.find("label")),
            caption=self.blocks(cap, path, 4) if cap is not None else [],
            asset_id=assets[0].id if assets else None,
            extra_asset_ids=[a.id for a in assets[1:]],
            alt=alt,
        )

    def _table(self, el, path: str) -> Table:
        cap = el.find("caption")
        tbl = el.find("table") if el.find("table") is not None else el.find(".//table")
        rows: list[TableRow] = []
        if tbl is not None:
            for grp in tbl:
                gname = _ln(grp)
                if gname in ("thead", "tbody", "tfoot"):
                    for tr in grp.findall("tr"):
                        rows.append(self._row(tr, path, gname == "thead"))
                elif gname == "tr":
                    rows.append(self._row(grp, path, False))
        fn = el.find("table-wrap-foot")
        graphic = None
        if tbl is None:
            g = el.find(".//graphic")
            a = self._asset(g.get(HREF), role="table") if g is not None else None
            graphic = a.id if a else None
        return Table(
            id=el.get("id") or "tbl",
            label=_txt(el.find("label")),
            caption=self.blocks(cap, path, 4) if cap is not None else [],
            rows=rows,
            footnotes=self.blocks(fn, path, 4) if fn is not None else [],
            graphic_asset_id=graphic,
        )

    def _row(self, tr, path: str, header: bool) -> TableRow:
        cells = []
        for c in tr:
            n = _ln(c)
            if n not in ("td", "th"):
                continue
            cells.append(TableCell(
                children=self.inlines(c, path),
                header=(n == "th") or header,
                rowspan=int(c.get("rowspan") or 1),
                colspan=int(c.get("colspan") or 1),
                align=c.get("align"),
            ))
        return TableRow(cells=cells, in_header=header)

    def _list(self, el, path: str, level: int) -> ListBlock:
        lt = el.get("list-type") or ""
        ordered = lt in ("order", "alpha-lower", "alpha-upper", "roman-lower", "roman-upper")
        items = []
        for li in el.findall("list-item"):
            items.append(ListItem(children=self.blocks(li, path, level),
                                  marker=_txt(li.find("label")) or None))
        return ListBlock(items=items, ordered=ordered, style=lt or None)

    def _def_list(self, el, path: str, level: int) -> DefinitionList:
        items = []
        for di in el.findall("def-item"):
            term, dd = di.find("term"), di.find("def")
            items.append((
                self.inlines(term, path) if term is not None else [],
                self.blocks(dd, path, level) if dd is not None else [],
            ))
        return DefinitionList(items=items)

    def _supplement(self, el, path: str, level: int) -> Supplement:
        media = el.find("media") if el.find("media") is not None else el
        href = media.get(HREF) or el.get(HREF)
        cap = el.find("caption")
        mt = media.get("mimetype") or ""
        sub = media.get("mime-subtype") or ""
        return Supplement(
            id=el.get("id") or "supp",
            label=_txt(el.find("label")),
            caption=self.blocks(cap, path, 4) if cap is not None else [],
            href=href,
            mimetype=f"{mt}/{sub}" if mt and sub else None,
            filename=href,
        )

    # ==================================================================
    # Assets
    # ==================================================================
    def _asset(self, href: str | None, role: str = "figure") -> Asset | None:
        if not href:
            return None
        name = href.rsplit("/", 1)[-1]
        aid = re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "asset"
        if aid in self.assets:
            return self.assets[aid]
        a = Asset(id=aid, source_url="", filename=name,
                  mimetype=_guess_mime(name), role=role)
        self.assets[aid] = a
        return a

    # ==================================================================
    # Front matter
    # ==================================================================
    def front(self, root) -> ArticleFront:
        am = root.find("front/article-meta")
        jm = root.find("front/journal-meta")
        f = ArticleFront()
        if am is None:
            return f

        f.article_type = root.get("article-type") or "research-article"
        tg = am.find("title-group")
        if tg is not None:
            t = tg.find("article-title")
            if t is not None:
                f.title = self.inlines(t, "front/title")
            st = tg.find("subtitle")
            if st is not None:
                f.subtitle = self.inlines(st, "front/subtitle")

        # journal
        if jm is not None:
            f.journal = JournalRef(
                title=_txt(jm.find("journal-title-group/journal-title")) or _txt(jm.find("journal-title")),
                abbrev=_txt(jm.find("journal-id[@journal-id-type='nlm-ta']")),
                issn=_txt(jm.find("issn[@pub-type='epub']")) or _txt(jm.find("issn")),
                publisher=_txt(jm.find("publisher/publisher-name")),
            )

        # Affiliations carrying an id, which contributors reference by xref.
        for aff in am.iter("aff"):
            if aff.get("id"):
                f.affiliations.append(Affiliation(
                    id=aff.get("id"),
                    label=_txt(aff.find("label")),
                    text=_aff_text(aff),
                ))

        # contributors
        for c in am.iter("contrib"):
            if (c.get("contrib-type") or "author") != "author":
                continue
            f.authors.append(self._author(c))

        _normalise_affiliations(f)

        # abstracts
        for ab in am.findall("abstract"):
            kind = ab.get("abstract-type") or "default"
            title = _txt(ab.find("title")) or ("Abstract" if kind == "default" else kind.replace("-", " ").title())
            kids = [c for c in ab if _ln(c) != "title"]
            f.abstracts.append(Abstract(blocks=self.blocks(kids, "front/abstract", 3),
                                        kind=kind, title=title))

        f.keywords = [_txt(k) for k in am.iter("kwd") if _txt(k)]
        for sg in am.iter("subj-group"):
            for s in sg.findall("subject"):
                if _txt(s):
                    f.subjects.append(_txt(s))

        f.volume = _txt(am.find("volume"))
        f.issue = _txt(am.find("issue"))
        f.fpage = _txt(am.find("fpage"))
        f.lpage = _txt(am.find("lpage"))
        f.elocation = _txt(am.find("elocation-id"))
        f.doi = _txt(am.find("article-id[@pub-id-type='doi']"))
        f.pmid = _txt(am.find("article-id[@pub-id-type='pmid']"))
        pmc = _txt(am.find("article-id[@pub-id-type='pmc']"))
        f.pmcid = f"PMC{pmc}" if pmc and not pmc.startswith("PMC") else pmc

        for d in am.iter("pub-date"):
            f.dates.append(PubDate(
                year=_txt(d.find("year")), month=_txt(d.find("month")),
                day=_txt(d.find("day")),
                kind=d.get("pub-type") or d.get("date-type") or "pub",
            ))

        f.license = self._license(am)
        an = am.find("author-notes")
        if an is not None:
            # Footnotes here ("contributed equally", present address) are part
            # of the notes, not of the article's numbered footnote list.
            notes: list[Block] = []
            for el in an:
                tag = _ln(el)
                if tag in ("label", "title"):
                    continue
                if tag == "fn":
                    notes.extend(self.blocks(
                        [c for c in el if _ln(c) != "label"], "front/author-notes", 4))
                else:
                    notes.extend(self._block_node(el, "front/author-notes", 4))
            f.author_notes = notes
        return f

    def _author(self, c) -> Author:
        a = Author()
        n = c.find("name")
        if n is not None:
            a.given = _txt(n.find("given-names"))
            a.surname = _txt(n.find("surname"))
            a.suffix = _txt(n.find("suffix"))
        else:
            sn = c.find("string-name")
            if sn is not None:
                a.given = _txt(sn.find("given-names"))
                a.surname = _txt(sn.find("surname")) or _txt(sn)
        col = c.find("collab")
        if col is not None:
            a.collab = _txt(col)
        cid = c.find("contrib-id[@contrib-id-type='orcid']")
        if cid is not None:
            a.orcid = _txt(cid).rsplit("/", 1)[-1]
        for x in c.findall("xref"):
            rt = x.get("ref-type")
            if rt == "aff" and x.get("rid"):
                a.affiliation_ids.extend(x.get("rid").split())
            elif rt == "corresp":
                a.corresponding = True
        if c.get("corresp") == "yes":
            a.corresponding = True
        em = c.find(".//email")
        if em is not None:
            a.email = _txt(em)
        # An affiliation nested directly inside the contributor. Where it has
        # no id — which is this publisher's usual shape — its text is the only
        # handle on it, so carry that and let normalisation dedupe.
        for aff in c.findall("aff"):
            if aff.get("id"):
                a.affiliation_ids.append(aff.get("id"))
            else:
                t = _aff_text(aff)
                if t:
                    a.affiliation_texts.append(t)
        return a

    def _license(self, am) -> License:
        lic = License()
        perms = am.find("permissions")
        if perms is None:
            return lic
        lic.copyright_statement = _txt(perms.find("copyright-statement"))
        lic.copyright_year = _txt(perms.find("copyright-year"))
        el = perms.find("license")
        if el is None:
            return lic
        ref = el.find(f"{{{ALI}}}license_ref")
        if ref is not None and (ref.text or "").strip():
            lic.url = ref.text.strip()
        elif el.get(HREF):
            lic.url = el.get(HREF)
        else:
            e = el.find(".//ext-link")
            if e is not None and e.get(HREF):
                lic.url = e.get(HREF)
        lp = el.find("license-p")
        lic.statement = _txt(lp) if lp is not None else _txt(el)
        if lic.url:
            lic.code = _cc_code(lic.url)
        return lic

    # ==================================================================
    # Back matter
    # ==================================================================
    def references(self, root) -> list[Reference]:
        out: list[Reference] = []
        for i, ref in enumerate(root.iter("ref"), start=1):
            cit = None
            for want in ("mixed-citation", "element-citation", "nlm-citation", "citation"):
                cit = ref.find(want)
                if cit is not None:
                    break
            r = Reference(id=ref.get("id") or f"ref{i}", index=i,
                          label=_txt(ref.find("label")))
            if cit is None:
                r.text = self.inlines(ref, "back/ref")
                out.append(r)
                continue
            r.citation_type = cit.get("publication-type") or ""
            r.text = self.inlines(cit, "back/ref")
            r.title = _txt(cit.find("article-title")) or _txt(cit.find("chapter-title"))
            r.source = _txt(cit.find("source"))
            r.year = _txt(cit.find("year"))
            r.volume = _txt(cit.find("volume"))
            r.fpage = _txt(cit.find("fpage"))
            r.doi = _txt(cit.find("pub-id[@pub-id-type='doi']"))
            r.pmid = _txt(cit.find("pub-id[@pub-id-type='pmid']"))
            el = cit.find("ext-link")
            r.url = _strip_artifacts((el.get(HREF) if el is not None else "") or "")
            for nm in cit.iter("name"):
                s, g = _txt(nm.find("surname")), _txt(nm.find("given-names"))
                if s:
                    r.authors.append(f"{s} {g}".strip())
            for sn in cit.iter("string-name"):
                if _txt(sn):
                    r.authors.append(_txt(sn))
            out.append(r)
        return out

    def footnotes(self, root) -> list[Footnote]:
        out = []
        for fn in root.iter("fn"):
            if not fn.get("id"):
                continue
            out.append(Footnote(
                id=fn.get("id"), label=_txt(fn.find("label")),
                children=self.blocks([c for c in fn if _ln(c) != "label"], "back/fn", 4),
                kind=fn.get("fn-type") or "fn",
            ))
        return out

    def back_sections(self, root) -> list[Section]:
        """Acknowledgements, appendices, glossaries, notes — kept, in order."""
        back = root.find("back")
        if back is None:
            return []
        out: list[Section] = []
        titles = {"ack": "Acknowledgements", "glossary": "Glossary",
                  "notes": "Notes", "app": "Appendix", "bio": "About the authors"}
        for el in back:
            tag = _ln(el)
            if tag in ("ref-list", "fn-group"):
                continue
            if tag == "app-group":
                for app in el.findall("app"):
                    out.append(self._back_sec(app, titles["app"]))
                continue
            if tag in titles:
                out.append(self._back_sec(el, titles[tag]))
            elif tag == "sec":
                out.append(self._back_sec(el, ""))
            else:
                self._unhandled(el, "back")
        return [s for s in out if s.children]

    def _back_sec(self, el, default_title: str) -> Section:
        t = el.find("title")
        kids = [c for c in el if c is not t and _ln(c) != "label"]
        return Section(
            title=self.inlines(t, "back") if t is not None else ([Text(default_title)] if default_title else []),
            children=self.blocks(kids, "back", 2),
            id=el.get("id"), level=1, sec_type=_ln(el),
        )

    # ==================================================================
    # Whole article
    # ==================================================================
    def parse(self, xml: bytes | str) -> Article:
        if isinstance(xml, str):
            xml = xml.encode("utf-8")
        parser = etree.XMLParser(recover=True, resolve_entities=False,
                                 load_dtd=False, no_network=True, huge_tree=True)
        root = etree.fromstring(xml, parser=parser)
        if root is None:
            raise ValueError("could not parse XML")
        if _ln(root) != "article":
            found = root.find(".//article")
            if found is None:
                raise ValueError(f"no <article> element (root was {_ln(root)})")
            root = found

        art = Article()
        art.front = self.front(root)
        self.article_id = art.front.pmcid or art.front.doi or self.article_id

        body = root.find("body")
        art.body = self.blocks(body, "body", 1) if body is not None else []
        art.references = self.references(root)
        art.footnotes = self.footnotes(root)
        art.back = self.back_sections(root)
        art.assets = self.assets
        art.unhandled = self.unhandled
        for u in art.unhandled:
            u.article_id = self.article_id

        _link_citations(art)
        return art


# ==========================================================================
# helpers
# ==========================================================================


# A hyphenation-point marker this publisher's conversion leaves behind, in
# attribute values and in text alike. It stands in for the hyphen it replaced,
# so `smart<plxhyp>PLXHYP</plxhyp>api.info` is really `smart-api.info`.
_PLXHYP = re.compile(r"<\s*plxhyp[^>]*>.*?</\s*plxhyp\s*>", re.I | re.S)


def _strip_artifacts(s: str) -> str:
    return _PLXHYP.sub("-", s) if s and "plxhyp" in s.lower() else s


def _merge_text(nodes: list[Inline]) -> list[Inline]:
    """Coalesce adjacent Text nodes; drop empties."""
    out: list[Inline] = []
    for n in nodes:
        if isinstance(n, Text):
            if not n.value:
                continue
            if out and isinstance(out[-1], Text):
                out[-1] = Text(out[-1].value + n.value)
                continue
        out.append(n)
    return out


def _link_citations(art: Article) -> None:
    """Record, for each reference, the ids of the places that cite it, so the
    renderer can offer back-links from the bibliography into the text."""
    by_id = {r.id: r for r in art.references}
    for node in walk_inlines(art.body):
        if isinstance(node, InternalLink) and node.kind == "bibr":
            r = by_id.get(node.target_id)
            if r is None:
                continue
            anchor = f"cite-{node.target_id}-{len(r.cited_by) + 1}"
            r.cited_by.append(anchor)
            node.anchor = anchor


def _normalise_affiliations(f: ArticleFront) -> None:
    """Guarantee every affiliation has an id and every author references it.

    Sources vary: some list affiliations once with ids and point contributors
    at them by xref; others repeat the affiliation inside each contributor with
    no id at all. Downstream should not have to care, so both shapes are
    reduced here to one deduplicated list with stable ids.
    """
    by_id = {a.id: a for a in f.affiliations if a.id}
    by_text: dict[str, str] = {a.text: a.id for a in f.affiliations if a.id and a.text}
    n = 0
    for au in f.authors:
        resolved: list[str] = []
        for rid in au.affiliation_ids:
            if rid in by_id:
                resolved.append(rid)
        for text in au.affiliation_texts:
            aid = by_text.get(text)
            if aid is None:
                n += 1
                aid = f"aff-auto-{n}"
                by_text[text] = aid
                aff = Affiliation(id=aid, label="", text=text)
                by_id[aid] = aff
                f.affiliations.append(aff)
            resolved.append(aid)
        # Preserve order, drop duplicates.
        seen: set[str] = set()
        au.affiliation_ids = [r for r in resolved if not (r in seen or seen.add(r))]
        au.affiliation_texts = []

    # Drop affiliations nothing points at only when something else does; if no
    # author resolved any, keep them all rather than silently lose the data.
    referenced = {r for au in f.authors for r in au.affiliation_ids}
    if referenced:
        f.affiliations = [a for a in f.affiliations if a.id in referenced]


def _aff_text(aff) -> str:
    """Affiliation text with its label stripped (the label is rendered as a marker)."""
    parts = []
    for node in aff.iter():
        if node is aff:
            if node.text:
                parts.append(node.text)
            continue
        if _ln(node) in ("label", "sup"):
            if node.tail:
                parts.append(node.tail)
            continue
        if node.text:
            parts.append(node.text)
        if node.tail:
            parts.append(node.tail)
    text = " ".join("".join(parts).split())
    # The publisher's own punctuation sits in the tails, so joining can leave a
    # space in front of it: "Communications , University".
    text = re.sub(r"\s+([,;.])", r"\1", text)
    return text.strip(" ,;")


def _guess_mime(name: str) -> str | None:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "gif": "image/gif", "svg": "image/svg+xml", "tif": "image/tiff",
        "tiff": "image/tiff", "webp": "image/webp", "pdf": "application/pdf",
        "zip": "application/zip", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "csv": "text/csv", "txt": "text/plain", "mp4": "video/mp4",
    }.get(ext)


def _cc_code(url: str) -> str | None:
    m = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z-]+)", url or "")
    if not m:
        return None
    s = m.group(1)
    return "CC0" if s == "zero" else f"CC {s.upper()}"


def _clean_tex(s: str) -> str:
    """Extract the expression from a `tex-math` payload.

    This publisher deposits each formula as a complete LaTeX document — a
    `\\documentclass` preamble, a pile of `\\usepackage` lines, then the
    expression inside `\\begin{document}`. Only the document body is the maths.
    """
    s = (s or "").strip()
    m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", s, re.S)
    if m:
        s = m.group(1).strip()
        # This publisher's conversion emits a stray `}{}` immediately after
        # \begin{document} on some formulae. Left in place it unbalances the
        # expression and the whole thing fails to typeset.
        s = re.sub(r"^\s*\}\{\}\s*", "", s)
        # Any other leading unmatched closing brace is the same class of fault.
        while s.startswith("}"):
            s = s[1:].lstrip()
    else:
        # No document wrapper: drop any stray preamble lines.
        s = re.sub(r"^\s*\\(documentclass|usepackage|setlength|newcommand)\b.*$",
                   "", s, flags=re.M).strip()
    # Strip outer inline/display delimiters, leaving environments intact.
    s = re.sub(r"^\\\[(.*)\\\]$", r"\1", s, flags=re.S).strip()
    s = re.sub(r"^\\\((.*)\\\)$", r"\1", s, flags=re.S).strip()
    s = re.sub(r"^\$\$(.*)\$\$$", r"\1", s, flags=re.S).strip()
    s = re.sub(r"^\$(.*)\$$", r"\1", s, flags=re.S).strip()
    return s
