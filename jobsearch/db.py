"""SQLite persistence. The DB is the memory: it tracks what you've already seen."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    fingerprint      TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    company          TEXT NOT NULL,
    title            TEXT NOT NULL,
    url              TEXT NOT NULL,
    location         TEXT,
    description      TEXT,
    remote           INTEGER DEFAULT 0,
    posted_at        TEXT,
    salary_min       INTEGER,
    salary_max       INTEGER,
    sponsor_matched  TEXT,
    sponsor_h1b_count INTEGER DEFAULT 0,
    score            REAL DEFAULT 0,
    score_reasons    TEXT,
    llm_score        INTEGER,
    llm_reasoning    TEXT,
    first_seen       TEXT,
    status           TEXT DEFAULT 'new',  -- new | seen | applied | rejected | hidden
    notified_at      TEXT                 -- set once a digest has reported it
);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS sponsors (
    employer_norm TEXT PRIMARY KEY,
    employer_raw  TEXT NOT NULL,
    approvals     INTEGER DEFAULT 0,
    fiscal_year   TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    fetched    INTEGER,
    new_jobs   INTEGER,
    notes      TEXT
);
"""

_FIELDS = [
    "fingerprint", "source", "company", "title", "url", "location", "description",
    "remote", "posted_at", "salary_min", "salary_max", "sponsor_matched",
    "sponsor_h1b_count", "score", "score_reasons", "llm_score", "llm_reasoning",
    "first_seen",
]


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a DB was first created."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for column, ddl in (("notified_at", "TEXT"),):
        if column not in have:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")
    conn.commit()


def upsert_jobs(conn: sqlite3.Connection, jobs: list[Job]) -> int:
    """Insert new jobs, refresh scores on existing ones. Returns count of genuinely new."""
    new = 0
    for job in jobs:
        row = conn.execute(
            "SELECT fingerprint FROM jobs WHERE fingerprint = ?", (job.fingerprint,)
        ).fetchone()
        d = job.to_dict()
        d["remote"] = int(d["remote"])
        d["score_reasons"] = json.dumps(d["score_reasons"])
        values = [d[f] for f in _FIELDS]
        if row is None:
            conn.execute(
                f"INSERT INTO jobs ({','.join(_FIELDS)}) VALUES ({','.join('?' * len(_FIELDS))})",
                values,
            )
            new += 1
        else:
            # Keep first_seen and status; refresh everything else.
            updatable = [f for f in _FIELDS if f not in ("fingerprint", "first_seen")]
            conn.execute(
                f"UPDATE jobs SET {','.join(f + '=?' for f in updatable)} WHERE fingerprint=?",
                [d[f] for f in updatable] + [job.fingerprint],
            )
    conn.commit()
    return new


def load_jobs(
    conn: sqlite3.Connection,
    min_score: float = 0.0,
    limit: int = 500,
    include_hidden: bool = False,
) -> list[dict]:
    sql = "SELECT * FROM jobs WHERE score >= ?"
    if not include_hidden:
        sql += " AND status NOT IN ('hidden','rejected')"
    sql += " ORDER BY COALESCE(llm_score, score * 100) DESC, first_seen DESC LIMIT ?"
    rows = conn.execute(sql, (min_score, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["score_reasons"] = json.loads(d["score_reasons"] or "[]")
        out.append(d)
    return out


def set_status(conn: sqlite3.Connection, fingerprint: str, status: str) -> bool:
    cur = conn.execute(
        "UPDATE jobs SET status = ? WHERE fingerprint = ?", (status, fingerprint)
    )
    conn.commit()
    return cur.rowcount > 0


def record_run(conn: sqlite3.Connection, started_at: str, fetched: int, new_jobs: int, notes: str) -> None:
    conn.execute(
        "INSERT INTO runs (started_at, fetched, new_jobs, notes) VALUES (?,?,?,?)",
        (started_at, fetched, new_jobs, notes),
    )
    conn.commit()
