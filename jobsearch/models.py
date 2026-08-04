"""Core data structures shared by sources, scoring, and storage."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


@dataclass
class Job:
    """A single posting, normalized across every source."""

    source: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    remote: bool = False
    posted_at: str | None = None  # ISO8601
    salary_min: int | None = None
    salary_max: int | None = None

    # Filled in by the pipeline, not by sources.
    fingerprint: str = ""
    sponsor_matched: str | None = None
    sponsor_h1b_count: int = 0
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)
    llm_score: int | None = None
    llm_reasoning: str | None = None
    first_seen: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()
        if not self.first_seen:
            self.first_seen = datetime.now(timezone.utc).isoformat()

    def compute_fingerprint(self) -> str:
        """Dedupe key: same role at same company is one job, even across boards."""
        basis = f"{_norm(self.company)}|{_norm(self.title)}|{_norm(self.location)}"
        return hashlib.sha256(basis.encode()).hexdigest()[:16]

    @property
    def haystack(self) -> str:
        return f"{self.title}\n{self.description}\n{self.location}".lower()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
