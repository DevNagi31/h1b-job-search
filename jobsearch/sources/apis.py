"""Aggregator APIs: RemoteOK, Adzuna, USAJOBS, Arbeitnow, HN Who's Hiring.

Adzuna and USAJOBS need free API keys, set as env vars. Everything else is keyless.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Iterable

from ..models import Job
from .base import Source, looks_remote, strip_html


class RemoteOKSource(Source):
    name = "remoteok"
    API = "https://remoteok.com/api"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        data = self.get_json(self.API)
        if not isinstance(data, list):
            return
        terms = [q.lower() for q in queries]
        for j in data[1:]:  # element 0 is a legal notice, not a job
            if not isinstance(j, dict):
                continue
            title = j.get("position") or ""
            # The public feed mixes real engineering roles in with retail and
            # non-English filler, so match on tags and body too, not just title.
            blob = f"{title} {' '.join(j.get('tags') or [])} {strip_html(j.get('description'))[:1500]}".lower()
            if terms and not any(t in blob for t in terms):
                continue
            yield Job(
                source=self.name,
                company=j.get("company", ""),
                title=title,
                url=j.get("url", ""),
                location=j.get("location") or "Remote",
                description=strip_html(j.get("description")),
                remote=True,
                posted_at=j.get("date"),
                salary_min=j.get("salary_min") or None,
                salary_max=j.get("salary_max") or None,
            )


class ArbeitnowSource(Source):
    name = "arbeitnow"
    API = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        terms = [q.lower() for q in queries]
        for page in range(1, int(self.config.get("pages", 3)) + 1):
            data = self.get_json(self.API, params={"page": page})
            if not isinstance(data, dict) or not data.get("data"):
                return
            for j in data["data"]:
                title = j.get("title", "")
                if terms and not any(t in title.lower() for t in terms):
                    continue
                created = j.get("created_at")
                yield Job(
                    source=self.name,
                    company=j.get("company_name", ""),
                    title=title,
                    url=j.get("url", ""),
                    location=j.get("location", ""),
                    description=strip_html(j.get("description")),
                    remote=bool(j.get("remote")),
                    posted_at=datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
                    if isinstance(created, (int, float)) else None,
                )


class AdzunaSource(Source):
    """Free tier: 250 calls/day. Set ADZUNA_APP_ID and ADZUNA_APP_KEY."""

    name = "adzuna"
    API = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
        if not (app_id and app_key):
            return
        for q in queries:
            for page in range(1, int(self.config.get("pages", 2)) + 1):
                data = self.get_json(
                    self.API.format(page=page),
                    params={
                        "app_id": app_id, "app_key": app_key, "what": q,
                        "results_per_page": 50, "max_days_old": self.config.get("max_days_old", 21),
                        "content-type": "application/json",
                    },
                )
                if not isinstance(data, dict) or not data.get("results"):
                    break
                for j in data["results"]:
                    loc = (j.get("location") or {}).get("display_name", "")
                    yield Job(
                        source=self.name,
                        company=(j.get("company") or {}).get("display_name", ""),
                        title=j.get("title", ""),
                        url=j.get("redirect_url", ""),
                        location=loc,
                        description=strip_html(j.get("description")),
                        remote=looks_remote(loc, j.get("title")),
                        posted_at=j.get("created"),
                        salary_min=int(j["salary_min"]) if j.get("salary_min") else None,
                        salary_max=int(j["salary_max"]) if j.get("salary_max") else None,
                    )


class USAJobsSource(Source):
    """Federal postings. Mostly citizens-only, but cap-exempt research roles show up.
    Set USAJOBS_API_KEY and USAJOBS_EMAIL."""

    name = "usajobs"
    API = "https://data.usajobs.gov/api/search"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        key, email = os.getenv("USAJOBS_API_KEY"), os.getenv("USAJOBS_EMAIL")
        if not (key and email):
            return
        self.session.headers.update({"Authorization-Key": key, "User-Agent": email, "Host": "data.usajobs.gov"})
        for q in queries:
            data = self.get_json(self.API, params={"Keyword": q, "ResultsPerPage": 50})
            items = ((data or {}).get("SearchResult") or {}).get("SearchResultItems") or []
            for item in items:
                d = item.get("MatchedObjectDescriptor") or {}
                ui = (d.get("UserArea") or {}).get("Details") or {}
                locs = ", ".join(l.get("LocationName", "") for l in (d.get("PositionLocation") or [])[:3])
                pay = (d.get("PositionRemuneration") or [{}])[0]
                yield Job(
                    source=self.name,
                    company=(d.get("OrganizationName") or ""),
                    title=d.get("PositionTitle", ""),
                    url=d.get("PositionURI", ""),
                    location=locs,
                    description=strip_html(
                        " ".join([ui.get("JobSummary", ""), ui.get("MajorDuties", "") if isinstance(ui.get("MajorDuties"), str) else "",
                                  d.get("QualificationSummary", "") or ""])
                    ),
                    remote=looks_remote(locs, ui.get("TeleworkEligible")),
                    posted_at=d.get("PublicationStartDate"),
                    salary_min=int(float(pay.get("MinimumRange", 0) or 0)) or None,
                    salary_max=int(float(pay.get("MaximumRange", 0) or 0)) or None,
                )


class HackerNewsSource(Source):
    """Ask HN: Who is Hiring? — each top-level comment is one posting.
    Noisy, but startups that sponsor often post here first."""

    name = "hackernews"
    SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
    ITEM = "https://hn.algolia.com/api/v1/items/{id}"
    _HEAD = re.compile(r"^([^|]{2,60})\|")

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        threads = self.get_json(
            self.SEARCH, params={"query": "Ask HN: Who is hiring?", "tags": "story,author_whoishiring", "hitsPerPage": 2}
        )
        terms = [q.lower() for q in queries]
        for hit in (threads or {}).get("hits", []):
            thread = self.get_json(self.ITEM.format(id=hit["objectID"]))
            for c in (thread or {}).get("children", []):
                text = strip_html(c.get("text"))
                if not text or (terms and not any(t in text.lower() for t in terms)):
                    continue
                m = self._HEAD.match(text)
                company = (m.group(1).strip() if m else text[:40]).strip()
                yield Job(
                    source=self.name,
                    company=company,
                    title=text[:120],
                    url=f"https://news.ycombinator.com/item?id={c.get('id')}",
                    location="",
                    description=text,
                    remote=looks_remote(text),
                    posted_at=c.get("created_at"),
                )
