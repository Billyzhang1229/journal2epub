"""The internal document model.

Source adapters map into this; the renderer maps out of it. It is deliberately
small and closed: the renderer handles every construct here exhaustively, and
anything a source encounters that does not fit is recorded in `Article.unhandled`
rather than dropped in silence.

Nothing publisher-specific or JATS-specific belongs in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Sequence

# --------------------------------------------------------------------------
# Inline content
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Text:
    value: str


EmphKind = Literal["italic", "bold", "sup", "sub", "smallcaps", "mono", "underline", "strike"]


@dataclass(slots=True)
class Emphasis:
    kind: EmphKind
    children: list["Inline"] = field(default_factory=list)


@dataclass(slots=True)
class InternalLink:
    """Link to another anchor in the same edition (a ref, figure, table, note).

    `anchor` is this citation's own id, so the bibliography can link back to
    the exact place in the text that cited it."""
    target_id: str
    kind: Literal["bibr", "fig", "table", "supp", "fn", "sec", "aff", "corresp", "disp-formula", "other"]
    children: list["Inline"] = field(default_factory=list)
    anchor: str | None = None


@dataclass(slots=True)
class ExternalLink:
    href: str
    children: list["Inline"] = field(default_factory=list)


@dataclass(slots=True)
class InlineMath:
    """Maths inside a line. `mathml` is presentation MathML; `source` is the
    original form (TeX where the publisher supplied it) kept for accessibility."""
    mathml: str | None
    source: str | None
    source_kind: Literal["tex", "mathml", "none"] = "none"
    alt: str = ""


@dataclass(slots=True)
class InlineGraphic:
    asset_id: str
    alt: str = ""


@dataclass(slots=True)
class LineBreak:
    pass


@dataclass(slots=True)
class FootnoteRef:
    target_id: str
    marker: str


Inline = (
    Text | Emphasis | InternalLink | ExternalLink | InlineMath
    | InlineGraphic | LineBreak | FootnoteRef
)

# --------------------------------------------------------------------------
# Block content
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Paragraph:
    children: list[Inline] = field(default_factory=list)
    id: str | None = None


@dataclass(slots=True)
class Section:
    title: list[Inline] = field(default_factory=list)
    children: list["Block"] = field(default_factory=list)
    id: str | None = None
    level: int = 1
    sec_type: str | None = None


@dataclass(slots=True)
class Figure:
    id: str
    label: str = ""
    caption: list["Block"] = field(default_factory=list)
    asset_id: str | None = None
    alt: str = ""
    # A figure may carry several graphics (multi-panel deposits).
    extra_asset_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TableCell:
    children: list[Inline] = field(default_factory=list)
    header: bool = False
    rowspan: int = 1
    colspan: int = 1
    align: str | None = None


@dataclass(slots=True)
class TableRow:
    cells: list[TableCell] = field(default_factory=list)
    in_header: bool = False


@dataclass(slots=True)
class Table:
    id: str
    label: str = ""
    caption: list["Block"] = field(default_factory=list)
    rows: list[TableRow] = field(default_factory=list)
    footnotes: list["Block"] = field(default_factory=list)
    # Set when the source only offered the table as a picture.
    graphic_asset_id: str | None = None

    @property
    def column_count(self) -> int:
        return max((sum(c.colspan for c in r.cells) for r in self.rows), default=0)


@dataclass(slots=True)
class ListItem:
    children: list["Block"] = field(default_factory=list)
    marker: str | None = None


@dataclass(slots=True)
class ListBlock:
    items: list[ListItem] = field(default_factory=list)
    ordered: bool = False
    style: str | None = None


@dataclass(slots=True)
class DefinitionList:
    items: list[tuple[list[Inline], list["Block"]]] = field(default_factory=list)


@dataclass(slots=True)
class Quote:
    children: list["Block"] = field(default_factory=list)
    attribution: list[Inline] = field(default_factory=list)


@dataclass(slots=True)
class CodeBlock:
    text: str = ""
    language: str | None = None


@dataclass(slots=True)
class Preformatted:
    text: str = ""


@dataclass(slots=True)
class DisplayMath:
    mathml: str | None
    source: str | None
    source_kind: Literal["tex", "mathml", "none"] = "none"
    id: str | None = None
    label: str = ""
    alt: str = ""


@dataclass(slots=True)
class Supplement:
    """A supplementary file. The bytes are usually not embedded (they can be
    huge and are often formats no reader handles); the entry exists so that
    cross-references resolve and the reader is told what exists upstream."""
    id: str
    label: str = ""
    caption: list["Block"] = field(default_factory=list)
    href: str | None = None
    mimetype: str | None = None
    filename: str | None = None


@dataclass(slots=True)
class Statement:
    """Boxed / set-off material: theorem, algorithm, key-point box."""
    id: str | None = None
    label: str = ""
    title: list[Inline] = field(default_factory=list)
    children: list["Block"] = field(default_factory=list)
    kind: str = "boxed-text"


Block = (
    Paragraph | Section | Figure | Table | ListBlock | DefinitionList | Quote
    | CodeBlock | Preformatted | DisplayMath | Supplement | Statement
)

# --------------------------------------------------------------------------
# Front matter
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Affiliation:
    id: str | None
    label: str = ""
    text: str = ""


@dataclass(slots=True)
class Author:
    given: str = ""
    surname: str = ""
    suffix: str = ""
    collab: str = ""
    orcid: str | None = None
    affiliation_ids: list[str] = field(default_factory=list)
    corresponding: bool = False
    email: str | None = None
    # Affiliation text carried directly on the contributor, for the common
    # case where the source nests <aff> inside <contrib> with no id to
    # reference. The parser normalises these into `ArticleFront.affiliations`.
    affiliation_texts: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        if self.collab:
            return self.collab
        return " ".join(p for p in (self.given, self.surname, self.suffix) if p).strip()

    @property
    def sort_key(self) -> str:
        return (self.surname or self.collab).lower()


@dataclass(slots=True)
class Abstract:
    blocks: list[Block] = field(default_factory=list)
    kind: str = "default"          # default | graphical | teaser | executive-summary
    title: str = "Abstract"


@dataclass(slots=True)
class License:
    url: str | None = None
    code: str | None = None                 # e.g. "CC BY"
    statement: str = ""                     # human-readable licence paragraph
    copyright_statement: str = ""
    copyright_year: str = ""

    @property
    def is_open(self) -> bool:
        return bool(self.url or (self.code or "").upper().startswith("CC"))


@dataclass(slots=True)
class PubDate:
    year: str = ""
    month: str = ""
    day: str = ""
    kind: str = "pub"

    @property
    def iso(self) -> str:
        if not self.year:
            return ""
        if not self.month:
            return self.year
        if not self.day:
            return f"{self.year}-{int(self.month):02d}"
        return f"{self.year}-{int(self.month):02d}-{int(self.day):02d}"

    def human(self) -> str:
        months = ("January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December")
        if self.month and self.month.isdigit() and 1 <= int(self.month) <= 12:
            m = months[int(self.month) - 1]
            return f"{m} {self.day}, {self.year}".replace(" ,", ",") if self.day else f"{m} {self.year}"
        return self.year


@dataclass(slots=True)
class JournalRef:
    title: str = ""
    abbrev: str = ""
    issn: str = ""
    publisher: str = ""


@dataclass(slots=True)
class ArticleFront:
    title: list[Inline] = field(default_factory=list)
    subtitle: list[Inline] = field(default_factory=list)
    authors: list[Author] = field(default_factory=list)
    affiliations: list[Affiliation] = field(default_factory=list)
    abstracts: list[Abstract] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    journal: JournalRef = field(default_factory=JournalRef)
    article_type: str = "research-article"
    subjects: list[str] = field(default_factory=list)
    volume: str = ""
    issue: str = ""
    fpage: str = ""
    lpage: str = ""
    elocation: str = ""
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    dates: list[PubDate] = field(default_factory=list)
    license: License = field(default_factory=License)
    author_notes: list[Block] = field(default_factory=list)

    @property
    def title_text(self) -> str:
        return inline_text(self.title)

    @property
    def pub_date(self) -> PubDate | None:
        for want in ("epub", "pub", "ppub", "collection"):
            for d in self.dates:
                if d.kind == want and d.year:
                    return d
        return self.dates[0] if self.dates else None

    def citation(self) -> str:
        """Attribution line: authors, title, journal, volume, locator, DOI."""
        names = [a.display for a in self.authors]
        if len(names) > 8:
            who = ", ".join(names[:8]) + ", et al."
        else:
            who = ", ".join(names)
        bits = [b for b in (who, f"“{self.title_text}”") if b]
        jrnl = self.journal.title or self.journal.abbrev
        tail = jrnl
        d = self.pub_date
        if d and d.year:
            tail += f" {d.year}"
        if self.volume:
            tail += f";{self.volume}"
        if self.issue:
            tail += f"({self.issue})"
        loc = self.elocation or (f"{self.fpage}-{self.lpage}" if self.lpage else self.fpage)
        if loc:
            tail += f":{loc}"
        bits.append(tail)
        out = ". ".join(b for b in bits if b)
        if self.doi:
            out += f". doi:{self.doi}"
        return out


# --------------------------------------------------------------------------
# References
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Reference:
    id: str
    index: int = 0
    label: str = ""
    text: list[Inline] = field(default_factory=list)
    doi: str = ""
    pmid: str = ""
    url: str = ""
    # Structured parts, when the source gave them.
    authors: list[str] = field(default_factory=list)
    title: str = ""
    source: str = ""
    year: str = ""
    volume: str = ""
    fpage: str = ""
    citation_type: str = ""
    # Ids of the places in the text that cite this reference (back-links).
    cited_by: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Footnote:
    id: str
    label: str = ""
    children: list[Block] = field(default_factory=list)
    kind: str = "fn"


# --------------------------------------------------------------------------
# Assets and provenance
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Asset:
    """A binary the edition may embed. `source_url` is where it came from;
    `path` is filled in once fetched."""
    id: str
    source_url: str
    filename: str
    mimetype: str | None = None
    path: str | None = None
    width: int | None = None
    height: int | None = None
    embedded: bool = True
    role: Literal["figure", "inline", "table", "supplement", "cover"] = "figure"


@dataclass(slots=True)
class Unhandled:
    """Something the adapter met and chose not to model."""
    tag: str
    article_id: str
    path: str = ""
    note: str = ""
    sample: str = ""


class Resolution(str, Enum):
    OK = "ok"
    NO_FULLTEXT = "no-fulltext"
    NOT_IN_OA_SUBSET = "not-in-oa-subset"
    NO_PMCID = "no-pmcid"
    PARSE_FAILED = "parse-failed"
    FETCH_FAILED = "fetch-failed"
    RETRACTED = "retracted"
    EXCLUDED = "excluded"


@dataclass(slots=True)
class Provenance:
    """Where this article came from and how, for the build report."""
    adapter: str = ""
    source_url: str = ""
    doi: str = ""
    pmcid: str = ""
    pmid: str = ""
    version: str = ""
    retrieved: str = ""
    checksum: str = ""
    resolution: Resolution = Resolution.OK
    note: str = ""


@dataclass(slots=True)
class Article:
    front: ArticleFront = field(default_factory=ArticleFront)
    body: list[Block] = field(default_factory=list)
    back: list[Section] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    footnotes: list[Footnote] = field(default_factory=list)
    assets: dict[str, Asset] = field(default_factory=dict)
    provenance: Provenance = field(default_factory=Provenance)
    unhandled: list[Unhandled] = field(default_factory=list)

    @property
    def slug(self) -> str:
        base = self.front.pmcid or self.front.doi.replace("/", "_") or "article"
        return base.replace(".", "_")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def inline_text(nodes: Sequence[Inline]) -> str:
    """Flatten inline content to plain text (titles, alt text, metadata)."""
    out: list[str] = []
    for n in nodes:
        match n:
            case Text():
                out.append(n.value)
            case Emphasis() | InternalLink() | ExternalLink():
                out.append(inline_text(n.children))
            case InlineMath():
                out.append(n.alt or n.source or "")
            case InlineGraphic():
                out.append(n.alt)
            case LineBreak():
                out.append(" ")
            case FootnoteRef():
                out.append(n.marker)
    return "".join(out)


def block_text(blocks: Sequence[Block]) -> str:
    """Flatten block content to plain text (descriptions, search, alt)."""
    out: list[str] = []
    for b in blocks:
        match b:
            case Paragraph():
                out.append(inline_text(b.children))
            case Section():
                out.append(inline_text(b.title))
                out.append(block_text(b.children))
            case Figure():
                out.append(b.label)
                out.append(block_text(b.caption))
            case Table():
                out.append(b.label)
                out.append(block_text(b.caption))
            case ListBlock():
                out.extend(block_text(i.children) for i in b.items)
            case DefinitionList():
                for term, desc in b.items:
                    out.append(inline_text(term))
                    out.append(block_text(desc))
            case Quote():
                out.append(block_text(b.children))
            case CodeBlock() | Preformatted():
                out.append(b.text)
            case DisplayMath():
                out.append(b.alt or b.source or "")
            case Supplement():
                out.append(b.label)
                out.append(block_text(b.caption))
            case Statement():
                out.append(inline_text(b.title))
                out.append(block_text(b.children))
    return " ".join(x for x in out if x)


def all_blocks(article: "Article") -> list[Block]:
    """Every block tree the renderer will render, in one list.

    Anything that walks an article for content — collecting maths to typeset,
    counting figures, gathering assets — must use this rather than `body`
    alone, or it will quietly miss the abstract, the back matter and the notes.
    """
    out: list[Block] = list(article.body)
    for ab in article.front.abstracts:
        out.extend(ab.blocks)
    out.extend(article.front.author_notes)
    out.extend(article.back)
    for fn in article.footnotes:
        out.extend(fn.children)
    return out


def walk_blocks(blocks: Sequence[Block]):
    """Depth-first traversal over every block in a tree."""
    for b in blocks:
        yield b
        match b:
            case Section() | Quote() | Statement():
                yield from walk_blocks(b.children)
            case Figure() | Supplement():
                yield from walk_blocks(b.caption)
            case Table():
                yield from walk_blocks(b.caption)
                yield from walk_blocks(b.footnotes)
            case ListBlock():
                for it in b.items:
                    yield from walk_blocks(it.children)
            case DefinitionList():
                for _, desc in b.items:
                    yield from walk_blocks(desc)


def walk_inlines(blocks: Sequence[Block]):
    """Every inline node anywhere in a block tree."""
    def rec(nodes):
        for n in nodes:
            yield n
            if isinstance(n, (Emphasis, InternalLink, ExternalLink)):
                yield from rec(n.children)

    for b in walk_blocks(blocks):
        match b:
            case Paragraph():
                yield from rec(b.children)
            case Section() | Statement():
                yield from rec(b.title)
            case Quote():
                yield from rec(b.attribution)
            case Table():
                for row in b.rows:
                    for cell in row.cells:
                        yield from rec(cell.children)
            case DefinitionList():
                for term, _ in b.items:
                    yield from rec(term)
