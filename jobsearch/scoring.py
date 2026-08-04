"""Deterministic filter + score. Runs on every job before any LLM sees it."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import Job
from .profile import Profile
from .sponsors import SponsorIndex

_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?year", re.I)

#: Max times one skill bucket can contribute, so a keyword-stuffed JD can't win.
BUCKET_CAP = 2


class Rejected(Exception):
    """Job fails a hard filter.

    `reason` is a stable bucket for tallying; str() carries the specifics.
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _contains(hay: str, needles: list[str]) -> list[str]:
    return [n for n in needles if n in hay]


def _required_years(text: str) -> int | None:
    """Largest 'N years' figure in the first chunk of the description."""
    hits = [int(m) for m in _YEARS_RE.findall(text[:4000]) if int(m) <= 30]
    return max(hits) if hits else None


def hard_filter(job: Job, profile: Profile) -> None:
    """Raise Rejected if the job is disqualified outright."""
    title = job.title.lower()
    hay = job.haystack

    if hit := _contains(title, profile.titles_exclude):
        raise Rejected("excluded title term", hit[0])

    if blocked := _contains(hay, profile.blockers):
        raise Rejected("posting rules out sponsorship", f"'{blocked[0]}'")

    loc = (job.location or "").lower()
    if loc and (hit := _contains(loc, profile.loc_exclude)):
        raise Rejected("excluded location", hit[0])

    if profile.must_have and not _contains(hay, profile.must_have):
        raise Rejected("no must-have skill present")

    if profile.drop_below_min and job.salary_max and job.salary_max < profile.min_salary:
        raise Rejected("salary below floor", f"max {job.salary_max} < {profile.min_salary}")

    req = _required_years(job.description)
    if req is not None and req > profile.max_years_required:
        raise Rejected("too senior", f"requires {req}y, cap is {profile.max_years_required}")


def score(job: Job, profile: Profile, sponsors: SponsorIndex | None) -> Job:
    """Attach score (0-1), reasons, and sponsor match. Mutates and returns job."""
    w = profile.weights
    hay = job.haystack
    title = job.title.lower()
    points = 0.0
    reasons: list[str] = []

    if hits := _contains(title, profile.titles_include):
        points += w["title_match"]
        reasons.append(f"title matches {hits[0]}")

    for bucket, key in (("must_have", "skill_must"), ("strong", "skill_strong"), ("nice", "skill_nice")):
        hits = _contains(hay, getattr(profile, bucket))
        if hits:
            # Cap per bucket so a keyword-stuffed JD can't dominate.
            points += w[key] * min(len(hits), BUCKET_CAP)
            reasons.append(f"{bucket}: {', '.join(hits[:4])}")

    if sponsors is not None:
        matched, count = sponsors.lookup(job.company)
        if matched:
            job.sponsor_matched = matched
            job.sponsor_h1b_count = count
            points += w["sponsor_history"]
            reasons.append(f"USCIS H1B filer ({matched}, {count} approvals)")

    if boosts := _contains(hay, profile.boosters):
        points += w["sponsor_language"]
        reasons.append(f"sponsorship language: '{boosts[0]}'")

    loc = (job.location or "").lower()
    if (job.remote and profile.remote_ok) or _contains(loc, profile.loc_preferred):
        points += w["location_pref"]
        reasons.append("preferred location")

    if job.salary_min and job.salary_min >= profile.min_salary:
        points += w["salary_ok"]
        reasons.append(f"salary from ${job.salary_min:,}")

    if _is_fresh(job.posted_at, profile.fresh_days):
        points += w["recent_posting"]
        reasons.append(f"posted within {profile.fresh_days}d")

    # Normalize against the best realistically attainable score. Salary and
    # posted-date are excluded: most sources omit both, so counting them in the
    # denominator would cap every ATS job at ~0.9 for no reason.
    ceiling = (
        w["title_match"]
        + (w["skill_must"] + w["skill_strong"] + w["skill_nice"]) * BUCKET_CAP
        + w["sponsor_history"] + w["sponsor_language"] + w["location_pref"]
    )
    job.score = round(min(points / ceiling, 1.0), 4)
    job.score_reasons = reasons
    return job


def _is_fresh(posted_at: str | None, days: int) -> bool:
    if not posted_at:
        return False
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc) - timedelta(days=days)


def process(jobs: list[Job], profile: Profile, sponsors: SponsorIndex | None,
            require_sponsor: bool = False) -> tuple[list[Job], dict[str, int]]:
    """Filter then score a batch. Returns (kept, rejection reason counts)."""
    kept: list[Job] = []
    dropped: dict[str, int] = {}
    for job in jobs:
        try:
            hard_filter(job, profile)
        except Rejected as e:
            dropped[e.reason] = dropped.get(e.reason, 0) + 1
            continue
        score(job, profile, sponsors)
        if require_sponsor and not job.sponsor_matched and not _contains(job.haystack, profile.boosters):
            dropped["no sponsor evidence"] = dropped.get("no sponsor evidence", 0) + 1
            continue
        if job.score < profile.min_score:
            dropped["below min_score"] = dropped.get("below min_score", 0) + 1
            continue
        kept.append(job)
    return kept, dropped
