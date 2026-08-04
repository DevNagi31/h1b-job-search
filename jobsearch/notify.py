"""Digest of jobs you haven't been told about yet.

Used by CI to open a GitHub issue, which GitHub emails you about. That's the
"tell me ASAP" path — the HTML dashboard is the "browse everything" path.

Notification state is tracked per job (`jobs.notified_at`) rather than by a time
window. A time window re-reports the same job on every run that overlaps it,
which means duplicate alerts whenever the schedule is tighter than the window.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def unnotified_jobs(conn: sqlite3.Connection, min_score: float, limit: int = 25) -> list[dict]:
    """Jobs never included in a digest, best first."""
    rows = conn.execute(
        """SELECT * FROM jobs
           WHERE notified_at IS NULL
             AND score >= ?
             AND status NOT IN ('hidden','rejected')
           ORDER BY COALESCE(llm_score, score * 100) DESC
           LIMIT ?""",
        (min_score, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_notified(conn: sqlite3.Connection, jobs: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "UPDATE jobs SET notified_at = ? WHERE fingerprint = ?",
        [(now, j["fingerprint"]) for j in jobs],
    )
    conn.commit()


def count_pending(conn: sqlite3.Connection, min_score: float) -> int:
    return conn.execute(
        """SELECT COUNT(*) FROM jobs
           WHERE notified_at IS NULL AND score >= ?
             AND status NOT IN ('hidden','rejected')""",
        (min_score,),
    ).fetchone()[0]


def digest_markdown(jobs: list[dict], dashboard_url: str = "", pending: int = 0) -> str:
    if not jobs:
        return ""
    lines = [f"**{len(jobs)} new match{'es' if len(jobs) != 1 else ''}.**", ""]
    for j in jobs:
        pts = j["llm_score"] if j.get("llm_score") is not None else round(j["score"] * 100)
        bits = [f"`{pts}`", f"**[{j['title']}]({j['url']})**", f"— {j['company']}"]
        if j.get("location"):
            bits.append(f"· {j['location']}")
        lines.append(" ".join(bits))

        notes = []
        if j.get("sponsor_matched"):
            notes.append(f"H-1B filer: {j['sponsor_h1b_count']} approvals")
        else:
            notes.append("no H-1B filing history")
        if j.get("llm_reasoning"):
            notes.append(j["llm_reasoning"])
        lines.append(f"  <sub>{' · '.join(notes)}</sub>")
        lines.append("")

    if pending > len(jobs):
        lines.append(f"_{pending - len(jobs)} more pending; they'll arrive in the next digest._")
        lines.append("")
    if dashboard_url:
        lines.append(f"[Full dashboard →]({dashboard_url})")
    return "\n".join(lines)
