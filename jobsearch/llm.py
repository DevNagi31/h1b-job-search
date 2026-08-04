"""Optional Claude re-ranking of the shortlist.

Deterministic scoring is good at "does this posting mention Kubernetes" and bad
at "would they actually interview me". This runs only over jobs that already
passed the cheap filters, so cost stays bounded.

Requires ANTHROPIC_API_KEY and `pip install anthropic`.
"""

from __future__ import annotations

import json
import logging
import re

from .models import Job
from .profile import Profile

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"

SYSTEM = """You screen job postings for a candidate who needs H-1B visa sponsorship.
Return STRICT JSON only, no prose, in this shape:
{"fit": <0-100 int>, "sponsorship": "likely"|"unclear"|"unlikely", "reasoning": "<=40 words"}

Scoring guidance:
- fit weighs skill/title/seniority overlap with the candidate's profile.
- Penalize hard when required years of experience far exceed the candidate's.
- sponsorship: "unlikely" if the posting requires citizenship, clearance, or says
  it won't sponsor; "likely" if it names sponsorship/H-1B or the employer is a
  known heavy H-1B filer; otherwise "unclear"."""


def _prompt(job: Job, profile: Profile) -> str:
    return f"""CANDIDATE
Experience: {profile.years_experience} years
Visa status: {profile.visa_status} (needs H-1B sponsorship)
Target titles: {', '.join(profile.titles_include)}
Core skills: {', '.join(profile.must_have + profile.strong)}
Also knows: {', '.join(profile.nice)}
Max acceptable required experience: {profile.max_years_required} years

POSTING
Company: {job.company}{f' (USCIS H-1B filer: {job.sponsor_h1b_count} approvals)' if job.sponsor_matched else ' (no H-1B filing history found)'}
Title: {job.title}
Location: {job.location}{' [remote]' if job.remote else ''}
Description: {job.description[:6000] or '(not available from this source)'}"""


def rerank(jobs: list[Job], profile: Profile, limit: int = 40, model: str = MODEL) -> list[Job]:
    """Score the top `limit` jobs with Claude. Failures leave llm_score as None."""
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed; skipping LLM rerank")
        return jobs

    client = anthropic.Anthropic()
    targets = sorted(jobs, key=lambda j: j.score, reverse=True)[:limit]

    for job in targets:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=300,
                system=SYSTEM,
                messages=[{"role": "user", "content": _prompt(job, profile)}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            data = _parse(text)
            if data is None:
                continue
            job.llm_score = int(data.get("fit", 0))
            job.llm_reasoning = f"[{data.get('sponsorship', '?')}] {data.get('reasoning', '')}"
            if data.get("sponsorship") == "unlikely":
                job.llm_score = min(job.llm_score, 25)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM rerank failed for %s @ %s: %s", job.title, job.company, e)
    return jobs


def _parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
