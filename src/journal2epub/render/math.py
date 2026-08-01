"""Mathematics rendering.

Publishers differ in what they deposit: GigaScience supplies `<tex-math>` and
no MathML, PLOS supplies MathML and no TeX. Both are accepted and both render
to inline SVG, so there is one output path downstream:

  * MathML would be the natural EPUB 3 choice, but reader support is thin and
    uneven, and this has to survive e-ink readers.
  * SVG renders identically everywhere, and MathJax sizes it in `ex` units, so
    it scales with whatever font size the reader chooses instead of pinning the
    maths to a pixel size.
  * The original TeX travels with every expression as the accessible label, so
    the source form is never lost.

Rendered SVG is cached by content hash, so a warm rebuild is deterministic and
does not need node installed at all. If node or MathJax is missing on a cold
build, expressions fall back to their TeX source, marked, and every one of them
is counted in the build report — never silently blank.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Ships inside the package, not in the repo's tools/, so an installed copy can
# still find it. Resolving it relative to the source tree works when running
# from a checkout and silently breaks in site-packages, which degrades every
# expression to fallback text without saying why.
SCRIPT = Path(__file__).resolve().parent / "math2svg.mjs"
BATCH = 400


@dataclass(slots=True)
class Expr:
    """One expression to typeset, in whichever form the publisher deposited."""
    source: str
    kind: str = "tex"          # "tex" | "mathml"
    display: bool = False


@dataclass(slots=True)
class MathResult:
    svg: str = ""
    error: str = ""
    source: str = ""
    kind: str = "tex"

    @property
    def ok(self) -> bool:
        return bool(self.svg) and not self.error


@dataclass(slots=True)
class MathRenderer:
    cache_dir: Path
    node: str | None = None
    script: Path = SCRIPT
    _mem: dict[str, MathResult] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=lambda: {
        "rendered": 0, "cached": 0, "failed": 0, "unavailable": 0})

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.node is None:
            self.node = shutil.which("node")

    # -- locating MathJax -------------------------------------------------
    @staticmethod
    def node_search_path() -> str:
        """Where node should look for `mathjax-full`.

        The script ships inside the package, so node's own resolution walks up
        from site-packages and finds nothing. These candidates cover the usual
        installs: an explicit override, the working directory, and a checkout.
        """
        candidates = [
            os.environ.get("JOURNAL2EPUB_NODE_PATH", ""),
            os.environ.get("NODE_PATH", ""),
            str(Path.cwd() / "node_modules"),
            str(Path(__file__).resolve().parents[3] / "node_modules"),
        ]
        return os.pathsep.join(p for p in candidates if p)

    def diagnose(self) -> tuple[bool, str]:
        """(usable, explanation) — so failures name their own fix."""
        if not self.node:
            return False, ("node was not found on PATH. Install Node.js, or "
                           "rebuild later from a warm cache, which needs neither "
                           "node nor MathJax.")
        if not self.script.exists():
            return False, f"the maths renderer script is missing: {self.script}"
        probe = subprocess.run(
            [self.node, "-e", "require.resolve('mathjax-full/js/mathjax.js')"],
            capture_output=True, text=True,
            env={**os.environ, "NODE_PATH": self.node_search_path()})
        if probe.returncode != 0:
            return False, ("node cannot resolve 'mathjax-full'. Run "
                           "`npm install mathjax-full` in the directory you "
                           "build from, or set JOURNAL2EPUB_NODE_PATH to a "
                           "node_modules that contains it.")
        return True, "node and MathJax are available"

    # -- keys ------------------------------------------------------------
    @staticmethod
    def key(source: str, display: bool, kind: str = "tex") -> str:
        h = hashlib.sha256(
            f"{kind}\x00{int(display)}\x00{source}".encode()).hexdigest()
        return h[:40]

    def _path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    # -- rendering -------------------------------------------------------
    def available(self) -> bool:
        return bool(self.node) and self.script.exists()

    def prepare(self, exprs: list[Expr], on_progress=None) -> None:
        """Render every expression not already cached, in as few node runs as
        possible. Call once per build before rendering pages.

        `on_progress(n)` is called with the number of expressions accounted for
        so far, including the ones already cached, so a caller can show a bar
        that reaches the end rather than stalling at the cached count."""
        tick = on_progress or (lambda _n=1: None)
        todo: dict[str, Expr] = {}
        for e in exprs:
            if not e.source:
                continue
            k = self.key(e.source, e.display, e.kind)
            if k in self._mem or self._path(k).exists():
                tick(1)                      # already cached; still progress
                continue
            todo[k] = e
        if not todo:
            return
        usable, why = self.diagnose()
        if not usable:
            log.warning("%d maths expressions will be shown as their source "
                        "instead of typeset: %s", len(todo), why)
            self.stats["unavailable"] += len(todo)
            tick(len(todo))
            return

        items = list(todo.items())
        log.info("rendering %d maths expressions", len(items))
        for i in range(0, len(items), BATCH):
            batch = items[i:i + BATCH]
            self._run_batch(batch)
            tick(len(batch))

    def _run_batch(self, items: list[tuple[str, "Expr"]]) -> None:
        payload = "\n".join(
            json.dumps({"id": k, e.kind: e.source, "display": e.display})
            for k, e in items)
        try:
            proc = subprocess.run(
                [self.node, str(self.script)], input=payload, capture_output=True,
                text=True, timeout=600, check=False,
                env={**os.environ, "NODE_PATH": self.node_search_path()})
        except (OSError, subprocess.TimeoutExpired) as e:
            log.error("maths renderer failed to run: %s", e)
            self.stats["unavailable"] += len(items)
            return
        if proc.returncode != 0:
            log.error("maths renderer exited %s: %s", proc.returncode, proc.stderr[:400])
            self.stats["unavailable"] += len(items)
            return

        by_key = dict(items)
        seen: set[str] = set()
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = d.get("id")
            if not k or k not in by_key:
                continue
            seen.add(k)
            e = by_key[k]
            res = MathResult(svg=_tidy_svg(d.get("svg") or ""),
                             error=d.get("error") or "", source=e.source, kind=e.kind)
            self._store(k, res)
            self.stats["failed" if res.error else "rendered"] += 1
        # Anything the renderer did not answer for is a failure, recorded rather
        # than left to silently render as nothing.
        for k, e in by_key.items():
            if k not in seen:
                self._store(k, MathResult(error="no response from renderer",
                                          source=e.source, kind=e.kind))
                self.stats["failed"] += 1

    def _store(self, key: str, res: MathResult) -> None:
        self._mem[key] = res
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"svg": res.svg, "error": res.error,
                                 "source": res.source, "kind": res.kind}))

    def get(self, source: str, display: bool, kind: str = "tex") -> MathResult:
        k = self.key(source, display, kind)
        if k in self._mem:
            return self._mem[k]
        p = self._path(k)
        if p.exists():
            d = json.loads(p.read_text())
            res = MathResult(svg=d.get("svg", ""), error=d.get("error", ""),
                             source=d.get("source", source), kind=d.get("kind", kind))
            self._mem[k] = res
            self.stats["cached"] += 1
            return res
        return MathResult(error="not rendered", source=source, kind=kind)


def _tidy_svg(svg: str) -> str:
    """Make MathJax's SVG safe to inline in XHTML.

    MathJax emits a `<mjx-container>` wrapper and ids that would collide once
    several expressions share a page; strip the wrapper and namespace the ids.
    """
    if not svg:
        return ""
    m = re.search(r"<svg\b.*</svg>", svg, re.S)
    if not m:
        return ""
    svg = m.group(0)
    if "xmlns=" not in svg[:200]:
        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    return svg


def namespace_ids(svg: str, prefix: str) -> str:
    """Rewrite ids and their references so several expressions can share a page."""
    ids = set(re.findall(r'\bid="([^"]+)"', svg))
    if not ids:
        return svg
    for i in sorted(ids, key=len, reverse=True):
        new = f"{prefix}-{i}"
        svg = svg.replace(f'id="{i}"', f'id="{new}"')
        svg = svg.replace(f'xlink:href="#{i}"', f'xlink:href="#{new}"')
        svg = svg.replace(f'href="#{i}"', f'href="#{new}"')
    return svg
