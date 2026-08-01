"""Command line interface."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import click

from . import __version__
from .build import BuildOptions, build_volume
from .config import available, load_journal, load_theme

DEFAULT_CACHE = Path(os.environ.get("JOURNAL2EPUB_CACHE", ".cache/journal2epub"))


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING if verbose == 0 else logging.INFO if verbose == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)-7s %(message)s", stream=sys.stderr)
    # httpx logs a line per request at INFO. On a volume build that is thousands
    # of lines and buries everything worth reading.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class TerminalProgress:
    """Live progress on stderr.

    On a terminal this is one redrawn line per phase. Piped to a file or a CI
    log it becomes occasional plain lines instead, because a few thousand
    carriage returns in a log file help nobody.
    """

    def __init__(self, stream=None, tty: bool | None = None) -> None:
        self.stream = stream or sys.stderr
        self.tty = self.stream.isatty() if tty is None else tty
        self.label = ""
        self.total: int | None = None
        self.done = 0
        self.started = 0.0
        self._last_draw = 0.0
        self._last_logged_pct = -1

    # -- Progress protocol ------------------------------------------------
    def phase(self, label: str, total: int | None = None) -> None:
        self._finish_line()
        self.label, self.total, self.done = label, total, 0
        self.started = self._last_draw = time.monotonic()
        self._last_logged_pct = -1
        if self.total is None:
            self._write(f"{label}…" + ("\n" if not self.tty else ""))
        else:
            self._draw(force=True)

    def advance(self, n: int = 1) -> None:
        self.done += n
        self._draw()

    def note(self, message: str) -> None:
        self._clear()
        self._write(f"  {message}\n")
        self._draw(force=True)

    def close(self) -> None:
        self._finish_line()

    # -- rendering --------------------------------------------------------
    def _draw(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_draw < 0.1:
            return          # redrawing faster than anyone can read wastes IO
        self._last_draw = now
        if self.total is None:
            return
        pct = int(100 * self.done / self.total) if self.total else 100

        if not self.tty:
            # Not a terminal: a line every 10%, no control characters.
            if pct // 10 > self._last_logged_pct // 10 or self.done >= self.total:
                self._last_logged_pct = pct
                self._write(f"{self.label}: {self.done}/{self.total} ({pct}%)\n")
            return

        width = 24
        filled = int(width * self.done / self.total) if self.total else width
        bar = "█" * filled + "·" * (width - filled)
        elapsed = now - self.started
        eta = ""
        if self.done and self.done < self.total and elapsed > 2:
            remaining = elapsed / self.done * (self.total - self.done)
            eta = f"  ~{_duration(remaining)} left"
        self._clear()
        self._write(f"  {self.label:<32} {bar} {self.done:>4}/{self.total}"
                    f" {pct:>3}%{eta}")

    def _finish_line(self) -> None:
        """Every phase ends with a completion line.

        Without this the redraw throttle can swallow the last update, so a
        phase appears to stop at 96% and the next one starts — which reads
        exactly like something went wrong."""
        if not self.label:
            return
        took = _duration(time.monotonic() - self.started)
        # Report what actually happened rather than asserting 100%: a phase can
        # legitimately end with fewer items than it started with.
        count = f" ({self.done}/{self.total})" if self.total else ""
        if self.tty:
            self._clear()
            self._write(f"  ✓ {self.label}{count} in {took}\n")
        else:
            self._write(f"{self.label}: done{count} in {took}\n")
        self.label = ""

    def _clear(self) -> None:
        if self.tty:
            self._write("\r\033[2K")

    def _write(self, s: str) -> None:
        try:
            self.stream.write(s)
            self.stream.flush()
        except (ValueError, OSError):
            pass


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="journal2epub")
def main() -> None:
    """Build offline reading editions of open-access journals."""


@main.command()
@click.argument("journal")
@click.option("--volume", "-V", required=True, help="Volume number to build.")
@click.option("--issue", "-I", default="",
              help="Issue within the volume. Journals that publish discrete "
                   "issues (PLOS) are built an issue at a time; those numbered "
                   "only by volume (GigaScience) omit this.")
@click.option("--out", "-o", type=click.Path(path_type=Path),
              help="Output .epub path. Defaults to <journal>-v<volume>.epub")
@click.option("--contact", envvar="JOURNAL2EPUB_CONTACT", default="",
              help="Contact address sent to the data providers, as their terms ask.")
@click.option("--cache", type=click.Path(path_type=Path), default=DEFAULT_CACHE,
              show_default=True, help="Durable HTTP and maths cache.")
@click.option("--work", type=click.Path(path_type=Path), default=Path(".build"),
              show_default=True, help="Resumable build state.")
@click.option("--offline", is_flag=True,
              help="Serve everything from cache; fail rather than touch the network.")
@click.option("--limit", type=int, default=0, help="Only resolve the first N articles.")
@click.option("--retry-failed", is_flag=True, help="Re-queue articles that failed before.")
@click.option("--fresh", is_flag=True, help="Discard build state and start over.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress output.")
@click.option("-v", "--verbose", count=True)
def build(journal: str, volume: str, issue: str, out: Path | None, contact: str,
          cache: Path, work: Path, offline: bool, limit: int, retry_failed: bool,
          fresh: bool, quiet: bool, verbose: int) -> None:
    """Build one volume — or one issue — of JOURNAL into a single EPUB."""
    _setup_logging(verbose)
    if not contact:
        click.echo(
            "warning: no contact address set. The data providers ask clients to "
            "identify themselves; pass --contact or set JOURNAL2EPUB_CONTACT.",
            err=True)
    suffix = f"v{volume}" + (f"i{issue}" if issue else "")
    out = out or Path(f"{journal}-{suffix}.epub")
    from .build import NullProgress
    progress = NullProgress() if quiet else TerminalProgress()
    res = build_volume(BuildOptions(
        journal_key=journal, volume=volume, issue=issue, out=out,
        cache_dir=cache, work_dir=work,
        contact=contact, offline=offline, limit=limit, retry_failed=retry_failed,
        fresh=fresh), progress=progress)

    click.echo(f"\n{res.epub}")
    click.echo(f"  included : {res.included} of {res.registry_count}")
    if res.missing:
        click.secho(f"  missing  : {res.missing} — listed in the book and in "
                    f"{res.report.name}", fg="yellow")
    click.echo(f"  report   : {res.report}")


@main.command("list")
def list_() -> None:
    """List the journals and themes that are configured."""
    keys = available("journals")
    width = max((len(k) for k in keys), default=0)
    click.echo("journals:")
    for k in keys:
        j = load_journal(k)
        click.echo(f"  {k:<{width}}  {j.title} ({j.issn}) — theme: {j.theme}")
    click.echo("themes:")
    for k in available("themes"):
        click.echo(f"  {k}")


@main.command()
@click.option("--cache", type=click.Path(path_type=Path), default=DEFAULT_CACHE,
              show_default=True)
def doctor(cache: Path) -> None:
    """Check that everything a build needs is present."""
    from .render.math import MathRenderer

    ok = True

    def line(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        click.secho(f"  {'ok  ' if good else 'FAIL'}  {label}",
                    fg="green" if good else "red", nl=False)
        click.echo(f"   {detail}" if detail else "")

    click.echo(f"journal2epub {__version__}")
    line("python", sys.version_info >= (3, 11), sys.version.split()[0])

    journals = available("journals")
    line("journals configured", bool(journals), ", ".join(journals) or "none")
    themes = available("themes")
    line("themes configured", bool(themes), ", ".join(themes) or "none")

    bad_themes = []
    for k in themes:
        try:
            load_theme(k)
        except ValueError as e:
            bad_themes.append(f"{k}: {e}".split("\n")[0])
    line("themes pass contrast", not bad_themes, "; ".join(bad_themes))

    usable, why = MathRenderer(cache_dir=Path(cache) / "math").diagnose()
    line("maths renderer", usable, why)

    contact = os.environ.get("JOURNAL2EPUB_CONTACT", "")
    line("contact address", bool(contact),
         contact or "unset — set JOURNAL2EPUB_CONTACT so the data providers "
                    "can identify this client, as their terms ask")

    n = len(list(Path(cache).rglob("*.body"))) if Path(cache).exists() else 0
    click.echo(f"  info  cache: {n} cached responses at {cache}")

    if not usable:
        click.echo("\nA build will still run without the maths renderer, but "
                   "every expression will be shown as its source instead of "
                   "typeset, and the report will say so.")
    raise SystemExit(0 if ok else 1)


@main.command()
@click.argument("report", type=click.Path(exists=True, path_type=Path))
def summary(report: Path) -> None:
    """Summarise a build report."""
    d = json.loads(report.read_text())
    t = d["totals"]
    where = f"volume {d['volume']}" + (f", issue {d['issue']}" if d.get('issue') else "")
    click.echo(f"{d['journal']['title']} {where}  (build {d['build_id']})")
    click.echo(f"  registry lists : {d['registry']['count']}"
               f"   cross-check: {d['registry']['cross_check']['count']}")
    click.echo(f"  included       : {t['included']}")
    click.echo(f"  not included   : {t['not_included']}")
    for k, v in sorted(t["by_resolution"].items()):
        click.echo(f"      {k:20} {v}")
    if d.get("unhandled_elements"):
        click.secho("  unhandled elements:", fg="yellow")
        for k, v in sorted(d["unhandled_elements"].items(), key=lambda kv: -kv[1]):
            click.echo(f"      {k:24} {v}")
    m = d.get("math", {})
    if m:
        click.echo(f"  maths          : {m}")
    agg = {}
    for a in d["articles"]:
        for k, v in (a.get("counts") or {}).items():
            agg[k] = agg.get(k, 0) + v
    if agg:
        click.echo("  content:")
        for k, v in sorted(agg.items()):
            click.echo(f"      {k:20} {v}")


if __name__ == "__main__":
    main()
