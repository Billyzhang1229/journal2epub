"""Durable, resumable build state.

Killing a volume build halfway and restarting must pick up where it stopped.
Every article's outcome is committed to SQLite as soon as it is known, so a
restart re-reads what is done and only works on what is not.

The parsed article itself is not stored here — the HTTP cache already makes
re-parsing cheap and deterministic, and storing the model would add a
serialisation format to keep in step with the model.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from .model import Resolution

SCHEMA = """
CREATE TABLE IF NOT EXISTS build (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    journal      TEXT NOT NULL,
    volume       TEXT NOT NULL,
    issue        TEXT NOT NULL DEFAULT '',
    started      TEXT NOT NULL,
    discovered   INTEGER DEFAULT 0,
    registry_count INTEGER DEFAULT 0,
    source_count   INTEGER DEFAULT -1,
    tool_version TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS article (
    doi        TEXT PRIMARY KEY,
    ord        INTEGER NOT NULL,
    pmcid      TEXT DEFAULT '',
    pmid       TEXT DEFAULT '',
    title      TEXT DEFAULT '',
    stub       TEXT NOT NULL,
    resolution TEXT DEFAULT 'pending',
    note       TEXT DEFAULT '',
    provenance TEXT DEFAULT '',
    unhandled  TEXT DEFAULT '[]',
    updated    TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS asset (
    url      TEXT PRIMARY KEY,
    doi      TEXT NOT NULL,
    filename TEXT NOT NULL,
    status   TEXT DEFAULT 'pending',
    bytes    INTEGER DEFAULT 0,
    note     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS article_res ON article(resolution);
"""


@dataclass(slots=True)
class ArticleRecord:
    doi: str
    order: int
    pmcid: str
    pmid: str
    title: str
    resolution: str
    note: str
    stub: dict
    provenance: dict
    unhandled: list

    @property
    def done(self) -> bool:
        return self.resolution != "pending"

    @property
    def ok(self) -> bool:
        return self.resolution == Resolution.OK.value


class BuildState:
    def __init__(self, path: Path, journal: str = "", volume: str = "",
                 issue: str = "", tool_version: str = "") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        with closing(self.db.cursor()) as c:
            c.executescript(SCHEMA)
        if journal:
            self._init_build(journal, volume, issue, tool_version)

    def _init_build(self, journal: str, volume: str, issue: str,
                    tool_version: str) -> None:
        row = self.db.execute(
            "SELECT journal, volume, issue FROM build WHERE id=1").fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO build (id, journal, volume, issue, started, tool_version)"
                " VALUES (1, ?, ?, ?, ?, ?)",
                (journal, volume, issue, _now(), tool_version))
        elif (row["journal"], row["volume"], row["issue"]) != (journal, volume, issue):
            raise ValueError(
                f"state file {self.path} belongs to {row['journal']} "
                f"vol {row['volume']}{' iss ' + row['issue'] if row['issue'] else ''}, "
                f"not {journal} vol {volume}{' iss ' + issue if issue else ''}")

    # -- discovery -------------------------------------------------------
    @property
    def discovered(self) -> bool:
        r = self.db.execute("SELECT discovered FROM build WHERE id=1").fetchone()
        return bool(r and r["discovered"])

    def record_discovery(self, stubs, registry_count: int, source_count: int) -> None:
        with self.db:
            for s in stubs:
                self.db.execute(
                    "INSERT OR IGNORE INTO article (doi, ord, pmcid, pmid, title, stub)"
                    " VALUES (?,?,?,?,?,?)",
                    (s.doi, s.order, s.pmcid, s.pmid, s.title, json.dumps(asdict(s))))
                # Discovery may learn ids on a later run; keep them fresh.
                self.db.execute(
                    "UPDATE article SET pmcid=?, pmid=?, ord=? WHERE doi=?",
                    (s.pmcid, s.pmid, s.order, s.doi))
            self.db.execute(
                "UPDATE build SET discovered=1, registry_count=?, source_count=? WHERE id=1",
                (registry_count, source_count))

    # -- per-article -----------------------------------------------------
    def pending(self) -> list[ArticleRecord]:
        rows = self.db.execute(
            "SELECT * FROM article WHERE resolution='pending' ORDER BY ord").fetchall()
        return [_rec(r) for r in rows]

    def all_records(self) -> list[ArticleRecord]:
        rows = self.db.execute("SELECT * FROM article ORDER BY ord").fetchall()
        return [_rec(r) for r in rows]

    def record_outcome(self, doi: str, resolution: Resolution, note: str = "",
                       provenance: dict | None = None, unhandled: list | None = None) -> None:
        self.db.execute(
            "UPDATE article SET resolution=?, note=?, provenance=?, unhandled=?, updated=?"
            " WHERE doi=?",
            (resolution.value, note, json.dumps(provenance or {}),
             json.dumps(unhandled or []), _now(), doi))

    def reset(self, only_failed: bool = True) -> int:
        """Re-queue articles so a rerun retries them."""
        if only_failed:
            cur = self.db.execute(
                "UPDATE article SET resolution='pending' WHERE resolution IN"
                " ('fetch-failed','parse-failed')")
        else:
            cur = self.db.execute("UPDATE article SET resolution='pending'")
        return cur.rowcount

    # -- assets ----------------------------------------------------------
    def asset_status(self, url: str) -> str | None:
        r = self.db.execute("SELECT status FROM asset WHERE url=?", (url,)).fetchone()
        return r["status"] if r else None

    def record_asset(self, url: str, doi: str, filename: str, status: str,
                     nbytes: int = 0, note: str = "") -> None:
        self.db.execute(
            "INSERT INTO asset (url, doi, filename, status, bytes, note) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(url) DO UPDATE SET status=excluded.status,"
            " bytes=excluded.bytes, note=excluded.note",
            (url, doi, filename, status, nbytes, note))

    def failed_assets(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM asset WHERE status!='ok'").fetchall()

    # -- summary ---------------------------------------------------------
    def counts(self) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT resolution, COUNT(*) n FROM article GROUP BY resolution").fetchall()
        return {r["resolution"]: r["n"] for r in rows}

    def build_info(self) -> dict:
        r = self.db.execute("SELECT * FROM build WHERE id=1").fetchone()
        return dict(r) if r else {}

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _rec(r: sqlite3.Row) -> ArticleRecord:
    return ArticleRecord(
        doi=r["doi"], order=r["ord"], pmcid=r["pmcid"], pmid=r["pmid"],
        title=r["title"], resolution=r["resolution"], note=r["note"],
        stub=json.loads(r["stub"]), provenance=json.loads(r["provenance"] or "{}"),
        unhandled=json.loads(r["unhandled"] or "[]"),
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
