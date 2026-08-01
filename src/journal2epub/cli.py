"""Command line interface."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import click

from . import __version__
from .build import BuildOptions, build_volume
from .config import available, load_journal, load_theme

DEFAULT_CACHE = Path(os.environ.get("JOURNAL2EPUB_CACHE", ".cache/journal2epub"))


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING if verbose == 0 else logging.INFO if verbose == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)-7s %(message)s", stream=sys.stderr)


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
@click.option("-v", "--verbose", count=True)
def build(journal: str, volume: str, issue: str, out: Path | None, contact: str,
          cache: Path, work: Path, offline: bool, limit: int, retry_failed: bool,
          fresh: bool, verbose: int) -> None:
    """Build one volume — or one issue — of JOURNAL into a single EPUB."""
    _setup_logging(verbose)
    if not contact:
        click.echo(
            "warning: no contact address set. The data providers ask clients to "
            "identify themselves; pass --contact or set JOURNAL2EPUB_CONTACT.",
            err=True)
    suffix = f"v{volume}" + (f"i{issue}" if issue else "")
    out = out or Path(f"{journal}-{suffix}.epub")
    res = build_volume(BuildOptions(
        journal_key=journal, volume=volume, issue=issue, out=out,
        cache_dir=cache, work_dir=work,
        contact=contact, offline=offline, limit=limit, retry_failed=retry_failed,
        fresh=fresh))

    click.echo(f"\n{res.epub}")
    click.echo(f"  included : {res.included} of {res.registry_count}")
    if res.missing:
        click.secho(f"  missing  : {res.missing} — listed in the book and in "
                    f"{res.report.name}", fg="yellow")
    click.echo(f"  report   : {res.report}")


@main.command("list")
def list_() -> None:
    """List the journals and themes that are configured."""
    click.echo("journals:")
    for k in available("journals"):
        j = load_journal(k)
        click.echo(f"  {k:16} {j.title} ({j.issn}) — theme: {j.theme}")
    click.echo("themes:")
    for k in available("themes"):
        click.echo(f"  {k}")


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
