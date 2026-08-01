#!/usr/bin/env python3
"""Development harness: parse a whole volume and report on what the model met.

Not part of the shipped tool. Its job is to make the unhandled-element log and
the article-type spread visible so the parser can be hardened against them.

    uv run python tools/parse_survey.py gigascience 12 [issue] [limit]
"""
from __future__ import annotations

import collections
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from journal2epub.config import load_journal
from journal2epub.model import (
    DisplayMath, Figure, InlineMath, Resolution, Supplement, Table,
    block_text, walk_blocks, walk_inlines,
)
from journal2epub.net import Fetcher
from journal2epub.sources.pmc import PmcJatsSource

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

CACHE = Path(os.environ.get("JOURNAL2EPUB_CACHE", ".cache/journal2epub"))
# The data providers ask clients to identify themselves. Same environment
# variable the CLI uses, so a survey run is as polite as a build.
CONTACT = os.environ.get("JOURNAL2EPUB_CONTACT", "")


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else "gigascience"
    volume = sys.argv[2] if len(sys.argv) > 2 else "12"
    issue = sys.argv[3] if len(sys.argv) > 3 else ""
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    if not CONTACT:
        print("warning: set JOURNAL2EPUB_CONTACT so the data providers can "
              "identify this client, as their terms ask.", file=sys.stderr)

    journal = load_journal(key)
    with Fetcher(CACHE, contact=CONTACT) as f:
        src = PmcJatsSource(f, contact=CONTACT)
        stubs = src.discover(journal, volume, issue)
        pmc_count = src.cross_check(journal, volume, issue)
        print(f"discovered {len(stubs)} articles in {journal.title} vol {volume}"
              f"{' iss ' + issue if issue else ''} (PMC indexes {pmc_count})")
        print(f"  with PMCID: {sum(1 for s in stubs if s.pmcid)}  "
              f"without: {sum(1 for s in stubs if not s.pmcid)}")

        if limit:
            stubs = stubs[:limit]

        res = collections.Counter()
        types = collections.Counter()
        subjects = collections.Counter()
        unhandled = collections.Counter()
        unhandled_where = collections.defaultdict(set)
        unhandled_sample: dict[str, str] = {}
        stats = collections.Counter()
        failures = []
        no_body = []

        for i, stub in enumerate(stubs, 1):
            out = src.fetch(stub, journal)
            res[out.resolution.value] += 1
            if out.resolution is not Resolution.OK:
                failures.append((stub.doi, out.resolution.value, out.note))
                print(f"  [{i}/{len(stubs)}] {stub.doi} -> {out.resolution.value}: {out.note}")
                continue
            a = out.article
            types[a.front.article_type] += 1
            for s in a.front.subjects:
                subjects[s] += 1
            for u in a.unhandled:
                unhandled[u.tag] += 1
                unhandled_where[u.tag].add(u.path)
                unhandled_sample.setdefault(u.tag, u.sample)

            blocks = list(walk_blocks(a.body))
            stats["figures"] += sum(1 for b in blocks if isinstance(b, Figure))
            stats["tables"] += sum(1 for b in blocks if isinstance(b, Table))
            stats["supplements"] += sum(1 for b in blocks if isinstance(b, Supplement))
            stats["display_math"] += sum(1 for b in blocks if isinstance(b, DisplayMath))
            stats["inline_math"] += sum(1 for n in walk_inlines(a.body) if isinstance(n, InlineMath))
            stats["refs"] += len(a.references)
            stats["assets"] += len(a.assets)
            stats["missing_assets"] += sum(1 for x in a.assets.values() if not x.embedded)
            if len(block_text(a.body)) < 500:
                no_body.append((a.front.doi, a.front.article_type, len(block_text(a.body))))
            if i % 25 == 0:
                print(f"  ...{i}/{len(stubs)}")

        print("\n=== resolution ===")
        for k, v in res.most_common():
            print(f"  {k:22} {v}")
        print("\n=== article types ===")
        for k, v in types.most_common():
            print(f"  {k:22} {v}")
        print("\n=== subjects (top 15) ===")
        for k, v in subjects.most_common(15):
            print(f"  {k:34} {v}")
        print("\n=== content ===")
        for k, v in sorted(stats.items()):
            print(f"  {k:18} {v}")
        print("\n=== UNHANDLED ELEMENTS ===")
        if not unhandled:
            print("  (none)")
        for tag, n in unhandled.most_common():
            where = ", ".join(sorted(unhandled_where[tag])[:3])
            print(f"  {tag:24} {n:5}  at {where}")
            print(f"      {unhandled_sample[tag][:150]}")
        if no_body:
            print(f"\n=== SUSPICIOUSLY SHORT BODIES ({len(no_body)}) ===")
            for doi, t, n in no_body[:15]:
                print(f"  {doi}  {t}  {n} chars")
        print(f"\ncache: {f.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
