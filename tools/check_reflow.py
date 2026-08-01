#!/usr/bin/env python3
"""Assert that no page of a built edition scrolls horizontally.

This is the regression check for docs/adr/0004: a single unbreakable token — a
long URL, an accession number, a supplementary filename — anywhere in an article
will otherwise widen every page of it, and the failure is invisible at desktop
width. It only shows up at the widths e-ink readers actually use.

    uv run python tools/check_reflow.py build/gigascience-v12.epub

Needs a headless Chrome; uses Playwright if available. Exits non-zero on any
page-level overflow, so it can gate a release.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# Widths chosen to bracket real reading systems, narrowest first.
WIDTHS = [
    (360, 640, "phone / small e-ink"),
    (400, 600, "6in e-ink (Kobo Clara class)"),
    (618, 824, "6.8in e-ink (Kindle Paperwhite class)"),
    (768, 1024, "tablet portrait"),
]

PROBE = """
(vw) => {
  const de = document.documentElement;
  // Elements inside a scroll region are meant to exceed their container; it is
  // the container that must clip. Only page-level overflow is a failure.
  const offenders = [...document.querySelectorAll('body *')]
    .filter(e => !e.closest('.table-scroll'))
    .filter(e => e.getBoundingClientRect().width > vw + 1)
    .map(e => (e.tagName + '.' + (e.className || '')).slice(0, 40)
              + ' :: ' + (e.textContent || '').trim().slice(0, 50));
  return {
    overflow: de.scrollWidth - vw,
    offenders: [...new Set(offenders)].slice(0, 5),
  };
}
"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    epub = Path(sys.argv[1])
    if not epub.exists():
        print(f"no such file: {epub}")
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed; install it with:\n"
              "  uv pip install playwright && python -m playwright install chromium")
        return 3

    tmp = Path(tempfile.mkdtemp(prefix="reflow-"))
    try:
        with zipfile.ZipFile(epub) as z:
            z.extractall(tmp)
        root = tmp / "OEBPS"
        pages = sorted(root.glob("*.xhtml"))
        print(f"{epub.name}: checking {len(pages)} pages at {len(WIDTHS)} widths")

        failures = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for w, h, label in WIDTHS:
                page = browser.new_page(viewport={"width": w, "height": h})
                worst = 0
                for f in pages:
                    page.goto(f.as_uri(), wait_until="load")
                    r = page.evaluate(PROBE, w)
                    if r["overflow"] > 0:
                        failures.append((label, w, f.name, r["overflow"], r["offenders"]))
                        worst = max(worst, r["overflow"])
                page.close()
                mark = "FAIL" if worst else "ok  "
                print(f"  {mark} {w:>4}px  {label}"
                      + (f"   worst overflow {worst}px" if worst else ""))
            browser.close()

        if failures:
            print(f"\n{len(failures)} page/width combinations scroll horizontally:")
            for label, w, name, over, offenders in failures[:20]:
                print(f"  {name} at {w}px: +{over}px")
                for o in offenders:
                    print(f"      {o}")
            return 1
        print("\nno page scrolls horizontally at any tested width")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
