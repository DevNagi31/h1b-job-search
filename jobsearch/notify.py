"""Emit a digest of jobs first seen in the last N hours.

Used by CI to open a GitHub issue, which GitHub emails you about. That's the
"tell me ASAP" path — the HTML dashboard is the "browse everything" path.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def recent_jobs(conn: sqlite3.Connection, hours: int, min_score: float, limit: int = 25) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        """SELECT * FROM jobs
           WHERE first_seen >= ? AND score >= ? AND status NOT IN ('hidden','rejected')
           ORDER BY COALESCE(llm_score, score * 100) DESC
           LIMIT ?""",
        (cutoff, min_score, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def digest_markdown(jobs: list[dict], hours: int, dashboard_url: str = "") -> str:
    if not jobs:
        return ""
    lines = [f"**{len(jobs)} new match{'es' if len(jobs) != 1 else ''}** in the last {hours}h.", ""]
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

    if dashboard_url:
        lines.append(f"[Full dashboard →]({dashboard_url})")
    return "\n".join(lines)
