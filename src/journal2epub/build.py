"""Volume build orchestration.

The pipeline is resumable at every stage: discovery is committed once, each
article's outcome is committed as soon as it is known, and the HTTP cache makes
re-reading anything already fetched free. Killing a build halfway and restarting
picks up where it stopped.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import __version__
from .config import JournalConfig, Theme, load_journal, load_theme
from .model import Article, Resolution
from .net import Fetcher, FetchError, OfflineError
from .render.epub import EditionArticle, EpubBuilder, MissingArticle
from .render.math import MathRenderer
from .sources.base import ArticleStub
from .sources.pmc import PmcJatsSource
from .state import BuildState

log = logging.getLogger(__name__)

SOURCES = {"pmc_jats": PmcJatsSource}


@runtime_checkable
class Progress(Protocol):
    """How a build reports what it is doing.

    A cold volume build runs for ten to twenty minutes, most of it fetching
    figures. Without this it is indistinguishable from a hang. The protocol
    lives here so `build.py` stays free of any particular UI.
    """

    def phase(self, label: str, total: int | None = None) -> None:
        """Begin a named stage, with an item count where one is known."""

    def advance(self, n: int = 1) -> None:
        """Report progress within the current phase."""

    def note(self, message: str) -> None:
        """Something worth surfacing without interrupting the phase."""

    def close(self) -> None:
        """Finish; leave the terminal tidy."""


class NullProgress:
    """Default, for library use and tests: says nothing."""

    def phase(self, label: str, total: int | None = None) -> None:
        pass

    def advance(self, n: int = 1) -> None:
        pass

    def note(self, message: str) -> None:
        pass

    def close(self) -> None:
        pass


@dataclass
class BuildOptions:
    journal_key: str
    volume: str
    out: Path
    issue: str = ""
    cache_dir: Path = Path(".cache/journal2epub")
    work_dir: Path = Path(".build")
    contact: str = ""
    offline: bool = False
    limit: int = 0
    retry_failed: bool = False
    fresh: bool = False
    max_image_edge: int = 1600


@dataclass
class BuildResult:
    epub: Path | None
    report: Path
    included: int
    missing: int
    registry_count: int
    ok: bool
    counts: dict = field(default_factory=dict)


def build_volume(opts: BuildOptions, progress: Progress | None = None) -> BuildResult:
    progress = progress or NullProgress()
    journal = load_journal(opts.journal_key)
    theme = load_theme(journal.theme)
    slug = f"{opts.journal_key}-v{opts.volume}" + (f"i{opts.issue}" if opts.issue else "")
    work = Path(opts.work_dir) / slug
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / "state.sqlite"
    if opts.fresh and state_path.exists():
        state_path.unlink()

    fetcher = Fetcher(opts.cache_dir, contact=opts.contact, version=__version__,
                      offline=opts.offline)
    math = MathRenderer(cache_dir=Path(opts.cache_dir) / "math")
    state = BuildState(state_path, journal=opts.journal_key, volume=opts.volume,
                       issue=opts.issue, tool_version=__version__)

    try:
        source = SOURCES[journal.source](fetcher, contact=opts.contact)
    except KeyError:
        raise SystemExit(f"unknown source adapter {journal.source!r}")

    with fetcher, state:
        # -- discovery (once) ------------------------------------------
        if not state.discovered:
            progress.phase(f"Discovering {journal.title} "
                           f"volume {opts.volume}"
                           + (f" issue {opts.issue}" if opts.issue else ""))
            log.info("discovering %s volume %s%s", journal.title, opts.volume,
                     f" issue {opts.issue}" if opts.issue else "")
            stubs = source.discover(journal, opts.volume, opts.issue)
            src_count = source.cross_check(journal, opts.volume, opts.issue)
            state.record_discovery(stubs, registry_count=len(stubs), source_count=src_count)
        info = state.build_info()
        log.info("volume has %s articles per registry (source index: %s)",
                 info.get("registry_count"), info.get("source_count"))

        if opts.retry_failed:
            n = state.reset(only_failed=True)
            if n:
                log.info("re-queued %d previously failed articles", n)

        # -- resolve every article -------------------------------------
        pending = state.pending()
        # Count what an earlier run already finished *before* --limit truncates
        # the queue, or a limited fresh build claims to be resuming work that
        # was never done.
        already = info.get("registry_count", 0) - len(pending)
        if opts.limit:
            pending = pending[:opts.limit]
        if pending:
            progress.phase("Resolving articles", total=len(pending))
            if already > 0:
                progress.note(f"resuming: {already} already resolved")
        for i, rec in enumerate(pending, 1):
            stub = ArticleStub(**rec.stub)
            try:
                out = source.fetch(stub, journal)
            except (FetchError, OfflineError) as e:
                state.record_outcome(rec.doi, Resolution.FETCH_FAILED, note=str(e))
                continue
            except Exception as e:  # noqa: BLE001
                log.exception("unexpected failure on %s", rec.doi)
                state.record_outcome(rec.doi, Resolution.PARSE_FAILED,
                                     note=f"{type(e).__name__}: {e}")
                continue
            prov = dataclasses.asdict(out.article.provenance) if out.article else {}
            if prov.get("resolution"):
                prov["resolution"] = str(prov["resolution"])
            unh = [dataclasses.asdict(u) for u in (out.article.unhandled if out.article else [])]
            state.record_outcome(rec.doi, out.resolution, note=out.note,
                                 provenance=prov, unhandled=unh)
            progress.advance()
            if i % 20 == 0 or i == len(pending):
                log.info("resolved %d/%d", i, len(pending))

        # -- re-read everything resolved, render, package ---------------
        records = state.all_records()
        if opts.limit:
            # A development flag. Cap what goes in the edition, and keep the
            # untouched remainder out of the "missing" list — they were never
            # attempted, so calling them missing would be a lie.
            ok = [r for r in records if r.ok][:opts.limit]
            keep = {r.doi for r in ok}
            records = [r for r in records if r.doi in keep]

        articles: list[tuple[Article, ArticleStub]] = []
        missing: list[MissingArticle] = []
        progress.phase("Reading articles", total=len(records))
        for rec in records:
            progress.advance()
            if not rec.ok:
                if rec.resolution == "pending":
                    continue
                missing.append(MissingArticle(doi=rec.doi, title=rec.title,
                                              resolution=rec.resolution, note=rec.note))
                continue
            stub = ArticleStub(**rec.stub)
            out = source.fetch(stub, journal)
            if out.article is None:
                missing.append(MissingArticle(doi=rec.doi, title=rec.title,
                                              resolution=out.resolution.value,
                                              note=out.note))
                continue
            articles.append((out.article, stub))

        if not articles:
            raise SystemExit("no articles could be resolved; refusing to write an empty edition")

        # -- maths, in one batch ---------------------------------------
        exprs: list[tuple[str, bool]] = []
        for art, _ in articles:
            exprs.extend(_collect_math(art))
        if exprs:
            progress.phase("Typesetting mathematics", total=len(exprs))
            math.prepare(exprs, on_progress=progress.advance)
            log.info("maths: %s", math.stats)

        # -- assets ------------------------------------------------------
        builder = EpubBuilder(journal=journal, theme=theme, volume=opts.volume,
                              issue=opts.issue, math=math, registry_count=info.get("registry_count", 0),
                              source_count=info.get("source_count", -1),
                              build_id=_build_id(journal, opts.volume, opts.issue, articles))
        # Lay the volume out in parts, the way the issue itself is organised:
        # Research, then Data Notes, then Technical Notes, and so on. Reading
        # order, the contents page and the navigation document all agree.
        placed = []
        for i, (art, stub) in enumerate(articles):
            rule = journal.section_for(art.front.article_type, art.front.subjects)
            placed.append((rule.order, rule.name, i, art))
        placed.sort(key=lambda t: (t[0], t[1], t[2]))

        progress.phase("Fetching figures and rendering", total=len(placed))
        for order, (_ord, part, _i, art) in enumerate(placed):
            asset_bytes = _fetch_assets(fetcher, state, art)
            progress.advance()
            builder.add_article(art, part=part, order=order, asset_bytes=asset_bytes)
        builder.missing = missing

        progress.phase("Writing EPUB")
        epub_path = Path(opts.out)
        builder.write(epub_path)
        progress.close()

        report_path = epub_path.with_suffix(".report.json")
        write_report(report_path, journal, opts, state, builder, fetcher, math)

    return BuildResult(epub=epub_path, report=report_path,
                       included=len(builder.articles), missing=len(missing),
                       registry_count=info.get("registry_count", 0),
                       ok=True, counts=state.counts() if not state.db else {})


def _collect_math(art: Article):
    """Every expression the renderer will need — including the ones in the
    abstract, the appendices and the notes, not just the body.

    Takes whichever form the publisher deposited: TeX where there is TeX,
    MathML otherwise. Publishers supply one or the other, rarely both."""
    from .model import DisplayMath, InlineMath, all_blocks, walk_blocks, walk_inlines
    from .render.math import Expr

    def expr(node, display: bool) -> Expr | None:
        if node.source:
            return Expr(source=node.source, kind="tex", display=display)
        if node.mathml:
            return Expr(source=node.mathml, kind="mathml", display=display)
        return None

    blocks = all_blocks(art)
    out: list[Expr] = []
    for n in walk_inlines(blocks):
        if isinstance(n, InlineMath):
            e = expr(n, False)
            if e:
                out.append(e)
    for b in walk_blocks(blocks):
        if isinstance(b, DisplayMath):
            e = expr(b, True)
            if e:
                out.append(e)
    return out


def _fetch_assets(fetcher: Fetcher, state: BuildState, art: Article) -> dict[str, bytes]:
    """Download every embeddable asset, recording per-asset outcomes."""
    out: dict[str, bytes] = {}
    for aid, a in art.assets.items():
        if not a.source_url or a.role == "supplement":
            a.embedded = False
            continue
        if not (a.mimetype or "").startswith("image/"):
            a.embedded = False
            continue
        try:
            entry = fetcher.get(a.source_url)
        except (FetchError, OfflineError) as e:
            a.embedded = False
            state.record_asset(a.source_url, art.front.doi, a.filename, "failed", note=str(e))
            log.warning("asset unavailable %s: %s", a.filename, e)
            continue
        out[aid] = entry.body
        state.record_asset(a.source_url, art.front.doi, a.filename, "ok", len(entry.body))
    return out


def _build_id(journal: JournalConfig, volume: str, issue: str, articles) -> str:
    """Deterministic identity for the edition: same inputs, same id."""
    h = hashlib.sha256()
    h.update(f"{journal.key}\x00{volume}\x00{issue}\x00{__version__}".encode())
    for art, _ in sorted(articles, key=lambda t: t[0].front.doi):
        h.update(art.front.doi.encode())
        h.update((art.provenance.checksum or "").encode())
    digest = h.hexdigest()
    # Shape it as a UUID so it can serve as the package identifier.
    return f"{digest[:8]}-{digest[8:12]}-5{digest[13:16]}-a{digest[17:20]}-{digest[20:32]}"


def write_report(path: Path, journal: JournalConfig, opts: BuildOptions,
                 state: BuildState, builder: EpubBuilder, fetcher: Fetcher,
                 math: MathRenderer) -> None:
    """Machine-readable account of every article attempted."""
    records = state.all_records()
    by_doi = {ea.article.front.doi: ea for ea in builder.articles}
    unhandled_total: dict[str, int] = {}
    entries = []
    for r in records:
        ea = by_doi.get(r.doi)
        for u in r.unhandled:
            unhandled_total[u.get("tag", "?")] = unhandled_total.get(u.get("tag", "?"), 0) + 1
        e = {
            "doi": r.doi,
            "title": r.title or (ea.article.front.title_text if ea else ""),
            "order": r.order,
            "pmcid": r.pmcid,
            "pmid": r.pmid,
            "resolution": r.resolution,
            "included": ea is not None,
            "note": r.note,
            "provenance": r.provenance,
            "unhandled_elements": r.unhandled,
        }
        if ea:
            e["part"] = ea.part
            e["file"] = ea.filename
            e["article_type"] = ea.article.front.article_type
            e["license"] = {
                "code": ea.article.front.license.code,
                "url": ea.article.front.license.url,
            }
            e["counts"] = {
                "figures": ea.stats.figures,
                "figures_missing": ea.stats.figures_missing,
                "tables": ea.stats.tables,
                "wide_tables": ea.stats.wide_tables,
                "math_typeset": ea.stats.math_ok,
                "math_fallback": ea.stats.math_fallback,
                "references": len(ea.article.references),
                "supplements": ea.stats.supplements,
                "links_degraded": ea.stats.dangling_links,
            }
        entries.append(e)

    info = state.build_info()
    counts = state.counts()
    doc = {
        "schema": "journal2epub/report/1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": {"name": "journal2epub", "version": __version__},
        "build_id": builder.build_id,
        "journal": {"key": journal.key, "title": journal.title, "issn": journal.issn,
                    "publisher": journal.publisher},
        "volume": opts.volume,
        "issue": opts.issue or None,
        "limited_to": opts.limit or None,
        "registry": {
            "source": "crossref",
            "count": info.get("registry_count", 0),
            "cross_check": {"source": "pmc-esearch", "count": info.get("source_count", -1)},
        },
        "totals": {
            "attempted": len(records),
            "included": len(builder.articles),
            "not_included": len(builder.missing),
            "by_resolution": counts,
        },
        "cache": dict(fetcher.stats),
        "math": dict(math.stats),
        "asset_notes": builder.asset_notes[:500],
        "unhandled_elements": unhandled_total,
        "articles": entries,
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    log.info("wrote %s", path)
