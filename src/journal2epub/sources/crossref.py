"""Volume enumeration via Crossref.

Crossref is the registry of record for what a journal actually published, which
makes it the right place to decide what *should* be in a volume. The difference
between this list and what full text can be found for is precisely what the
build report has to account for.

Note on filtering: Crossref's `from-pub-date` / `until-pub-date` filters key off
the `issued` date, which for this publisher is the online-first date and often
falls in the calendar year *before* the volume. Filtering GigaScience volume 12
by 2023 dates returns 7 of 113 articles. Volume membership must therefore come
from the `volume` field, not from dates.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Iterator

from ..net import Fetcher
from .base import ArticleStub

log = logging.getLogger(__name__)

API = "https://api.crossref.org"
SELECT = "DOI,title,volume,issue,page,type,issued,published,subject,author"
PAGE = 1000


MAX_PAGES = 200


def _works_page(fetcher: Fetcher, issn: str, cursor: str, page: int) -> dict:
    q = urllib.parse.urlencode({"rows": PAGE, "cursor": cursor, "select": SELECT})
    url = f"{API}/journals/{issn}/works?{q}"
    # Crossref's deep paging re-sends one stateful scroll token and advances
    # server-side state, so every page after the first has an identical URL.
    # The page number keeps the cache entries distinct; without it the cache
    # replays a single page and enumeration silently returns duplicates.
    return fetcher.get_json(url, cache_key=f"{url}#page={page}")["message"]


def iter_works(fetcher: Fetcher, issn: str) -> Iterator[dict]:
    """Every work Crossref holds for this ISSN, via cursor paging.

    Deduplicates by DOI and stops on a page that adds nothing new, so a cursor
    that fails to advance can never turn into an infinite loop or a duplicated
    enumeration.
    """
    cursor, page = "*", 0
    seen: set[str] = set()
    total = None
    while page < MAX_PAGES:
        msg = _works_page(fetcher, issn, cursor, page)
        items = msg.get("items", [])
        if total is None:
            total = msg.get("total-results", 0)
        if not items:
            break

        fresh = 0
        for w in items:
            doi = (w.get("DOI") or "").lower()
            if doi and doi in seen:
                continue
            if doi:
                seen.add(doi)
            fresh += 1
            yield w

        if fresh == 0:
            log.warning("crossref paging stopped at page %d: no new records "
                        "(cursor is not advancing)", page)
            break
        page += 1
        cursor = msg.get("next-cursor") or ""
        if not cursor or len(items) < PAGE or len(seen) >= (total or 0):
            break

    if total and len(seen) < total:
        log.warning("crossref: enumerated %d of %d works for %s",
                    len(seen), total, issn)


def enumerate_volume(fetcher: Fetcher, issn: str, volume: str,
                     issue: str = "") -> list[ArticleStub]:
    """Articles Crossref assigns to `volume`, optionally narrowed to one issue.

    Journals that run to hundreds of articles a volume (PLOS Computational
    Biology publishes ~520) are read an issue at a time; journals that publish
    continuously and number only by volume (GigaScience) leave `issue` empty."""
    want_vol, want_iss = str(volume).strip(), str(issue or "").strip()
    stubs: list[ArticleStub] = []
    for w in iter_works(fetcher, issn):
        if str(w.get("volume", "")).strip() != want_vol:
            continue
        if want_iss and str(w.get("issue", "") or "").strip() != want_iss:
            continue
        issued = w.get("issued", {}).get("date-parts", [[None]])[0]
        published = w.get("published", {}).get("date-parts", [[None]])[0] or issued
        stubs.append(ArticleStub(
            doi=w.get("DOI", ""),
            title=(w.get("title") or [""])[0],
            volume=str(w.get("volume", "")),
            issue=str(w.get("issue", "") or ""),
            pages=w.get("page", "") or "",
            article_type=w.get("type", ""),
            published="-".join(str(p) for p in published if p is not None),
            extra={"subject": w.get("subject", [])},
        ))

    # Stable, deterministic order: publication date, then the DOI's article
    # suffix, which for this publisher increases with acceptance order.
    stubs.sort(key=lambda s: (s.published or "9999", s.doi))
    for i, s in enumerate(stubs):
        s.order = i
    log.info("crossref: %s volume %s%s -> %d articles", issn, volume,
             f" issue {want_iss}" if want_iss else "", len(stubs))
    return stubs


def journal_info(fetcher: Fetcher, issn: str) -> dict:
    return fetcher.get_json(f"{API}/journals/{issn}")["message"]
