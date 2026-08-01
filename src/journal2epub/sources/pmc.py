"""PubMed Central adapter: structured full text via the PMC Open Access dataset.

Acquisition path, all sanctioned machine-access services:

  Crossref            enumerate the volume (registry of record)
  PMC ID Converter    DOI -> PMCID
  PMC OA on AWS       article JATS XML, article metadata JSON, figures,
                      supplementary files (bucket `pmc-oa-opendata`, us-east-1,
                      world-readable, no credentials)

The legacy OA Web Service (`oa.fcgi`) and the PMC FTP tree are deliberately not
used: NLM is retiring both, the FTP package links they hand out already fail,
and the replacement is the S3 dataset used here. See
docs/adr/0002-pmc-oa-on-aws.md.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from ..config import JournalConfig
from ..model import Article, Provenance, Resolution
from ..net import Fetcher, FetchError, OfflineError
from .base import ArticleStub, FetchOutcome
from .crossref import enumerate_volume
from .jats import JatsParser

log = logging.getLogger(__name__)

S3 = "https://pmc-oa-opendata.s3.amazonaws.com"
IDCONV = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
IDCONV_BATCH = 200


class PmcJatsSource:
    """Primary source adapter."""

    name = "pmc_jats"

    def __init__(self, fetcher: Fetcher, contact: str = "", tool: str = "journal2epub") -> None:
        self.f = fetcher
        self.contact = contact
        self.tool = tool
        self._idmap: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------
    def discover(self, journal: JournalConfig, volume: str,
                 issue: str = "") -> list[ArticleStub]:
        stubs = enumerate_volume(self.f, journal.issn, volume, issue)
        if journal.exclude_types:
            stubs = [s for s in stubs if s.article_type not in journal.exclude_types]
        self._resolve_ids([s.doi for s in stubs if s.doi])
        for s in stubs:
            rec = self._idmap.get(s.doi.lower(), {})
            s.pmcid = rec.get("pmcid", "") or ""
            s.pmid = str(rec.get("pmid", "") or "")
        return stubs

    def cross_check(self, journal: JournalConfig, volume: str, issue: str = "") -> int:
        """How many articles PMC itself indexes for this volume (or issue). Used
        only as a sanity signal in the report — Crossref remains the
        enumeration source."""
        ta = journal.nlm_ta or journal.title
        term = f'"{ta}"[Journal] AND {volume}[volume]'
        if issue:
            term += f" AND {issue}[issue]"
        url = (f"{EUTILS}/esearch.fcgi?db=pmc&retmode=json&retmax=0"
               f"&term={_q(term)}&tool={self.tool}&email={_q(self.contact)}")
        try:
            return int(self.f.get_json(url)["esearchresult"]["count"])
        except (FetchError, OfflineError, KeyError, ValueError):
            return -1

    def _resolve_ids(self, dois: list[str]) -> None:
        """DOI -> PMCID/PMID in batches, cached per batch."""
        todo = [d for d in dois if d.lower() not in self._idmap]
        for i in range(0, len(todo), IDCONV_BATCH):
            batch = todo[i:i + IDCONV_BATCH]
            url = (f"{IDCONV}?ids={_q(','.join(batch))}&format=json"
                   f"&tool={self.tool}&email={_q(self.contact)}")
            try:
                data = self.f.get_json(url)
            except (FetchError, OfflineError) as e:
                log.warning("id conversion failed for %d dois: %s", len(batch), e)
                continue
            for rec in data.get("records", []):
                key = (rec.get("doi") or rec.get("requested-id") or "").lower()
                if key:
                    self._idmap[key] = rec
            # Anything the service did not answer for stays unresolved, and is
            # reported as such rather than quietly dropped.
            for d in batch:
                self._idmap.setdefault(d.lower(), {})

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------
    def fetch(self, stub: ArticleStub, journal: JournalConfig) -> FetchOutcome:
        pmcid = stub.pmcid
        if not pmcid:
            self._resolve_ids([stub.doi])
            pmcid = (self._idmap.get(stub.doi.lower(), {}) or {}).get("pmcid", "")
        if not pmcid:
            return FetchOutcome(stub, Resolution.NO_PMCID,
                                note="no PMCID for this DOI in the PMC ID converter")

        try:
            listing = self._list_prefix(pmcid)
        except (FetchError, OfflineError) as e:
            return FetchOutcome(stub, Resolution.FETCH_FAILED, note=str(e))
        if not listing:
            return FetchOutcome(stub, Resolution.NOT_IN_OA_SUBSET,
                                note=f"{pmcid} has no objects in the PMC OA dataset")

        version = max(listing)
        keys = listing[version]
        prefix = f"{pmcid}.{version}"

        meta = {}
        if f"{prefix}.json" in keys:
            try:
                meta = json.loads(self.f.get(f"{S3}/{prefix}/{prefix}.json").body)
            except (FetchError, OfflineError, ValueError) as e:
                log.warning("%s: metadata json unavailable (%s)", pmcid, e)

        if meta.get("is_retracted"):
            return FetchOutcome(stub, Resolution.RETRACTED,
                                note="marked retracted in PMC metadata")
        if meta and not meta.get("is_pmc_openaccess", True):
            return FetchOutcome(stub, Resolution.NOT_IN_OA_SUBSET,
                                note="not in the PMC Open Access Subset")

        xml_key = f"{prefix}.xml"
        if xml_key not in keys:
            return FetchOutcome(stub, Resolution.NO_FULLTEXT,
                                note=f"no JATS XML object under {prefix}/")
        xml_url = f"{S3}/{prefix}/{xml_key}"
        try:
            entry = self.f.get(xml_url)
        except (FetchError, OfflineError) as e:
            return FetchOutcome(stub, Resolution.FETCH_FAILED, note=str(e))

        parser = JatsParser(article_id=pmcid)
        try:
            art = parser.parse(entry.body)
        except Exception as e:  # noqa: BLE001 - one bad article must not stop a volume
            log.exception("%s: parse failed", pmcid)
            return FetchOutcome(stub, Resolution.PARSE_FAILED, note=f"{type(e).__name__}: {e}")

        self._bind_assets(art, prefix, keys)
        self._fill_from_stub(art, stub, meta, pmcid)
        art.provenance = Provenance(
            adapter=self.name, source_url=xml_url, doi=art.front.doi or stub.doi,
            pmcid=pmcid, pmid=stub.pmid or str(meta.get("pmid") or ""),
            version=str(version), retrieved=entry.fetched, checksum=entry.sha256,
            resolution=Resolution.OK,
        )
        return FetchOutcome(stub, Resolution.OK, article=art)

    # ------------------------------------------------------------------
    def _list_prefix(self, pmcid: str) -> dict[int, set[str]]:
        """Object keys under this PMCID, grouped by article version."""
        url = f"{S3}/?list-type=2&prefix={pmcid}."
        body = self.f.get(url).text
        out: dict[int, set[str]] = {}
        for key in re.findall(r"<Key>([^<]*)</Key>", body):
            m = re.match(rf"^{re.escape(pmcid)}\.(\d+)/(.+)$", key)
            if m:
                out.setdefault(int(m.group(1)), set()).add(m.group(2))
        return out

    def _bind_assets(self, art: Article, prefix: str, keys: set[str]) -> None:
        """Point every asset the parser found at its object in the dataset, and
        mark the ones that are not actually available."""
        for a in art.assets.values():
            if a.filename in keys:
                a.source_url = f"{S3}/{prefix}/{a.filename}"
                continue
            # JATS sometimes cites a graphic without its extension.
            cand = [k for k in keys if k.rsplit(".", 1)[0] == a.filename]
            if cand:
                best = sorted(cand, key=_image_pref)[0]
                a.source_url = f"{S3}/{prefix}/{best}"
                a.filename = best
                a.mimetype = a.mimetype or _mime_of(best)
            else:
                a.embedded = False

    def _fill_from_stub(self, art: Article, stub: ArticleStub, meta: dict, pmcid: str) -> None:
        f = art.front
        f.doi = f.doi or stub.doi or (meta.get("doi") or "")
        f.pmcid = f.pmcid or pmcid
        f.pmid = f.pmid or str(meta.get("pmid") or stub.pmid or "")
        f.volume = f.volume or stub.volume
        f.issue = f.issue or stub.issue
        if not f.license.code and meta.get("license_code"):
            f.license.code = meta["license_code"]


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s or "", safe="")


def _image_pref(name: str) -> tuple[int, str]:
    order = {"jpg": 0, "jpeg": 0, "png": 1, "gif": 2, "webp": 3, "tif": 4, "tiff": 4}
    ext = name.rsplit(".", 1)[-1].lower()
    return (order.get(ext, 9), name)


def _mime_of(name: str) -> str | None:
    from .jats import _guess_mime
    return _guess_mime(name)
