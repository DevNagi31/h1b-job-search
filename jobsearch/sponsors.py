"""H1B sponsor whitelist.

This is the filter that makes the whole repo worth having: a job only survives
if the hiring company actually has a track record of H1B petitions.

Data source: USCIS H-1B Employer Data Hub, which publishes one CSV per fiscal
year of every employer with approved/denied petitions.
    https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub

Download the CSV for the years you care about, then:
    python -m jobsearch.cli load-sponsors data/h1b_datahubexport-2024.csv

The DOL LCA disclosure files (Employer name + case status) work too — the
loader sniffs the column names rather than assuming a fixed schema.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

# Corporate suffixes that differ between a job board and a federal filing.
_SUFFIXES = {
    "inc", "incorporated", "llc", "l l c", "ltd", "limited", "corp", "corporation",
    "co", "company", "plc", "lp", "llp", "gmbh", "holdings", "group", "usa",
    "us", "america", "technologies", "technology", "labs", "the",
}

_NAME_COLS = ("employer", "employer_name", "employer name", "company", "petitioner")
_COUNT_COLS = (
    "initial approval", "initial approvals", "continuing approval",
    "continuing approvals", "approval", "approvals", "count",
)


def normalize_employer(name: str) -> str:
    """Aggressively normalize so 'Stripe, Inc.' == 'Stripe'."""
    s = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    tokens = [t for t in s.split() if t and t not in _SUFFIXES]
    return " ".join(tokens)


def _pick(header: list[str], candidates: tuple[str, ...]) -> list[int]:
    lowered = [h.strip().lower() for h in header]
    return [i for i, h in enumerate(lowered) if h in candidates]


def load_sponsor_csv(conn: sqlite3.Connection, path: str | Path, fiscal_year: str = "") -> int:
    """Import a USCIS/DOL disclosure CSV into the sponsors table. Idempotent."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"sponsor file not found: {path}")

    fiscal_year = fiscal_year or _year_from_name(path.name)
    loaded = 0
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            return 0
        name_idx = _pick(header, _NAME_COLS)
        if not name_idx:
            raise ValueError(
                f"no employer-name column in {path.name}; saw: {header[:12]}"
            )
        name_i = name_idx[0]
        count_idx = _pick(header, _COUNT_COLS)

        for row in reader:
            if len(row) <= name_i:
                continue
            raw = row[name_i].strip()
            norm = normalize_employer(raw)
            if not norm:
                continue
            approvals = 0
            for i in count_idx:
                if i < len(row):
                    approvals += _as_int(row[i])
            conn.execute(
                """INSERT INTO sponsors (employer_norm, employer_raw, approvals, fiscal_year)
                   VALUES (?,?,?,?)
                   ON CONFLICT(employer_norm) DO UPDATE SET
                     approvals = sponsors.approvals + excluded.approvals,
                     fiscal_year = excluded.fiscal_year""",
                (norm, raw, approvals, fiscal_year),
            )
            loaded += 1
    conn.commit()
    return loaded


def _as_int(v: str) -> int:
    try:
        return int(float(str(v).replace(",", "").strip() or 0))
    except ValueError:
        return 0


def _year_from_name(name: str) -> str:
    m = re.search(r"(20\d{2})", name)
    return m.group(1) if m else ""


class SponsorIndex:
    """In-memory lookup with exact-then-prefix matching on normalized names."""

    def __init__(self, conn: sqlite3.Connection):
        self._exact: dict[str, tuple[str, int]] = {}
        for row in conn.execute("SELECT employer_norm, employer_raw, approvals FROM sponsors"):
            self._exact[row["employer_norm"]] = (row["employer_raw"], row["approvals"])
        # First-token bucket makes "google" match "google cloud services".
        self._by_head: dict[str, list[str]] = {}
        for norm in self._exact:
            head = norm.split(" ")[0]
            self._by_head.setdefault(head, []).append(norm)

    def __len__(self) -> int:
        return len(self._exact)

    def lookup(self, company: str) -> tuple[str | None, int]:
        """Return (matched_employer_name, approval_count) or (None, 0)."""
        norm = normalize_employer(company)
        if not norm:
            return None, 0
        if norm in self._exact:
            raw, n = self._exact[norm]
            return raw, n

        head = norm.split(" ")[0]
        if len(head) < 3:
            return None, 0
        best: tuple[str, int] | None = None
        for cand in self._by_head.get(head, []):
            # Require one side to be a full prefix of the other, so "meta" hits
            # "meta platforms" but "app" never hits "apple".
            if cand.startswith(norm) or norm.startswith(cand):
                raw, n = self._exact[cand]
                if best is None or n > best[1]:
                    best = (raw, n)
        return best if best else (None, 0)
