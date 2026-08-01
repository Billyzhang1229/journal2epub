"""The source adapter contract.

A source adapter is responsible for two things and nothing else:

  1. enumerating the articles in a volume (`discover`), and
  2. turning one article's upstream representation into an `Article`
     from `journal2epub.model` (`fetch`).

Everything downstream — rendering, theming, packaging, validation — sees only
the internal model, so adding a source costs exactly one adapter.

A second adapter (rendered-HTML scraping) is anticipated by this interface but
deliberately not implemented: structured full text carries section hierarchy,
author/affiliation binding, figure-caption pairing, table structure, maths and
parsed references, all of which scraping would discard and then have to guess
back. See `docs/adr/0001-structured-source-first.md`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol, runtime_checkable

from ..model import Article, Resolution


@dataclass(slots=True)
class ArticleStub:
    """What discovery knows before full text is fetched."""
    doi: str = ""
    pmcid: str = ""
    pmid: str = ""
    title: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    article_type: str = ""
    published: str = ""
    order: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.doi or self.pmcid or self.title


@dataclass(slots=True)
class FetchOutcome:
    """Result of trying to resolve one article. `article` is None unless OK."""
    stub: ArticleStub
    resolution: Resolution
    article: Article | None = None
    note: str = ""


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    def discover(self, journal: "JournalConfig", volume: str,
                 issue: str = "") -> Iterable[ArticleStub]:
        """Enumerate every article the registry says belongs to this volume,
        or to one issue of it when `issue` is given.

        Must include articles this adapter cannot itself resolve — the build
        report accounts for everything attempted, so discovery must not
        silently narrow to what is convenient."""
        ...

    def fetch(self, stub: ArticleStub, journal: "JournalConfig") -> FetchOutcome:
        """Resolve one article to the internal model, or explain why not."""
        ...


# Imported late to avoid a cycle at module import time.
from ..config import JournalConfig  # noqa: E402
