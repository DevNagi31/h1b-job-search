"""ATS job boards: Greenhouse, Lever, Ashby, SmartRecruiters.

These are public, documented JSON endpoints that companies publish for their own
careers pages. This is the highest-signal, lowest-breakage source in the repo —
you get the full job description, which is what sponsorship detection needs.

Board tokens live in config/companies.yaml.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ..models import Job
from .base import Source, looks_remote, strip_html


def _iso(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):  # epoch millis
        ts = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return str(value)


class GreenhouseSource(Source):
    name = "greenhouse"
    API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        for token in self.config.get("boards", []):
            data = self.get_json(self.API.format(token=token), params={"content": "true"})
            if not isinstance(data, dict):
                continue
            company = data.get("name") or token
            for j in data.get("jobs", []):
                loc = (j.get("location") or {}).get("name", "")
                desc = strip_html(j.get("content"))
                yield Job(
                    source=self.name,
                    company=company,
                    title=j.get("title", ""),
                    url=j.get("absolute_url", ""),
                    location=loc,
                    description=desc,
                    remote=looks_remote(loc, j.get("title")),
                    posted_at=_iso(j.get("updated_at") or j.get("created_at")),
                )


class LeverSource(Source):
    name = "lever"
    API = "https://api.lever.co/v0/postings/{token}"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        for token in self.config.get("boards", []):
            data = self.get_json(self.API.format(token=token), params={"mode": "json"})
            if not isinstance(data, list):
                continue
            for j in data:
                cats = j.get("categories") or {}
                loc = cats.get("location", "")
                desc = strip_html(j.get("descriptionPlain") or j.get("description"))
                lists = " ".join(
                    strip_html(x.get("content", "")) for x in (j.get("lists") or [])
                )
                yield Job(
                    source=self.name,
                    company=token,
                    title=j.get("text", ""),
                    url=j.get("hostedUrl", ""),
                    location=loc,
                    description=f"{desc}\n{lists}".strip(),
                    remote=looks_remote(loc, cats.get("commitment")),
                    posted_at=_iso(j.get("createdAt")),
                )


class AshbySource(Source):
    name = "ashby"
    API = "https://api.ashbyhq.com/posting-api/job-board/{token}"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        for token in self.config.get("boards", []):
            data = self.get_json(self.API.format(token=token), params={"includeCompensation": "true"})
            if not isinstance(data, dict):
                continue
            for j in data.get("jobs", []):
                loc = j.get("location") or ""
                yield Job(
                    source=self.name,
                    company=j.get("companyName") or token,
                    title=j.get("title", ""),
                    url=j.get("jobUrl") or j.get("applyUrl", ""),
                    location=loc,
                    description=strip_html(j.get("descriptionHtml") or j.get("descriptionPlain")),
                    remote=bool(j.get("isRemote")) or looks_remote(loc),
                    posted_at=_iso(j.get("publishedAt")),
                )


class SmartRecruitersSource(Source):
    name = "smartrecruiters"
    API = "https://api.smartrecruiters.com/v1/companies/{token}/postings"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        for token in self.config.get("boards", []):
            data = self.get_json(self.API.format(token=token), params={"limit": 100})
            if not isinstance(data, dict):
                continue
            for j in data.get("content", []):
                loc_obj = j.get("location") or {}
                loc = ", ".join(
                    x for x in (loc_obj.get("city"), loc_obj.get("region"), loc_obj.get("country")) if x
                )
                # The list endpoint omits the description; pull the detail doc.
                detail = self.get_json(f"{self.API.format(token=token)}/{j.get('id')}") or {}
                sections = ((detail.get("jobAd") or {}).get("sections") or {})
                desc = " ".join(
                    strip_html((sections.get(k) or {}).get("text"))
                    for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
                )
                yield Job(
                    source=self.name,
                    company=j.get("company", {}).get("name") or token,
                    title=j.get("name", ""),
                    url=j.get("ref") or f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                    location=loc,
                    description=desc,
                    remote=bool(loc_obj.get("remote")) or looks_remote(loc),
                    posted_at=_iso(j.get("releasedDate")),
                )
