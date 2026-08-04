"""Opt-in sources that scrape HTML rather than use a sanctioned API.

You asked for these, so they're here and working — but they are OFF by default
and you should know what you're turning on:

  * Scraping LinkedIn and Indeed is against their Terms of Service. That's a
    contract issue, not a criminal one (hiQ v. LinkedIn), but they enforce it
    with IP blocks and account bans. Don't run these from an IP you care about,
    and never with logged-in cookies for an account you need.
  * Both are aggressively bot-defended. Expect these adapters to return zero
    results periodically and to need selector updates when markup changes. The
    ATS sources in ats.py are the ones that will still work in six months.

Enable per source in config/sources.yaml with `enabled: true`.
"""

from __future__ import annotations

import re
import time
from typing import Iterable
from urllib.parse import quote_plus

from ..models import Job
from .base import Source, looks_remote, strip_html

_CARD_RE = re.compile(r"<li>.*?</li>", re.S)
_A_RE = re.compile(r'href="([^"]+/jobs/view/[^"?]+)', re.S)
_TITLE_RE = re.compile(r'class="base-search-card__title"[^>]*>(.*?)<', re.S)
_COMPANY_RE = re.compile(r'class="base-search-card__subtitle"[^>]*>.*?>(.*?)<', re.S)
_LOC_RE = re.compile(r'class="job-search-card__location"[^>]*>(.*?)<', re.S)
_DATE_RE = re.compile(r'datetime="([^"]+)"')


class LinkedInSource(Source):
    """LinkedIn's unauthenticated guest job-search partial. Fragile by nature."""

    name = "linkedin"
    requires_optin = True
    API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        self.session.headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        )
        location = self.config.get("location", "United States")
        pages = int(self.config.get("pages", 2))
        delay = float(self.config.get("delay_seconds", 3))

        for q in queries:
            for page in range(pages):
                params = {
                    "keywords": q,
                    "location": location,
                    "start": page * 25,
                    "f_TPR": self.config.get("time_filter", "r604800"),  # last 7 days
                }
                try:
                    r = self.session.get(self.API, params=params, timeout=30)
                    if r.status_code != 200:
                        break
                    html = r.text
                except Exception:  # noqa: BLE001
                    break

                cards = _CARD_RE.findall(html)
                if not cards:
                    break
                for card in cards:
                    url = _A_RE.search(card)
                    title = _TITLE_RE.search(card)
                    company = _COMPANY_RE.search(card)
                    if not (url and title and company):
                        continue
                    loc = _LOC_RE.search(card)
                    posted = _DATE_RE.search(card)
                    loc_text = strip_html(loc.group(1)) if loc else ""
                    yield Job(
                        source=self.name,
                        company=strip_html(company.group(1)),
                        title=strip_html(title.group(1)),
                        url=url.group(1),
                        location=loc_text,
                        # The card has no JD. Sponsorship blockers can't be
                        # detected here, so these lean on sponsor history alone.
                        description="",
                        remote=looks_remote(loc_text, title.group(1)),
                        posted_at=posted.group(1) if posted else None,
                    )
                time.sleep(delay)


class IndeedSource(Source):
    """Indeed blocks datacenter IPs and plain HTTP clients outright.

    Rather than ship a scraper that silently returns nothing, this adapter
    routes through a proxy/scraping API you configure. Set INDEED_PROXY_URL to
    a template containing {url} (e.g. a ScraperAPI/Zyte endpoint). Without it,
    the source no-ops and says so.
    """

    name = "indeed"
    requires_optin = True
    BASE = "https://www.indeed.com/jobs?q={q}&l={l}&fromage={days}&start={start}"
    _JSON_RE = re.compile(r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.*?\});', re.S)

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        import json
        import os

        proxy_tpl = self.config.get("proxy_url") or os.getenv("INDEED_PROXY_URL")
        if not proxy_tpl:
            raise RuntimeError(
                "indeed source enabled but no proxy configured; set INDEED_PROXY_URL "
                "(template containing {url}) or disable it in config/sources.yaml"
            )

        location = self.config.get("location", "United States")
        pages = int(self.config.get("pages", 2))
        for q in queries:
            for page in range(pages):
                target = self.BASE.format(
                    q=quote_plus(q), l=quote_plus(location),
                    days=self.config.get("max_days_old", 7), start=page * 10,
                )
                try:
                    r = self.session.get(proxy_tpl.format(url=quote_plus(target)), timeout=60)
                    m = self._JSON_RE.search(r.text)
                    if not m:
                        break
                    results = json.loads(m.group(1)).get("metaData", {}).get(
                        "mosaicProviderJobCardsModel", {}).get("results", [])
                except Exception:  # noqa: BLE001
                    break

                for j in results:
                    key = j.get("jobkey")
                    loc = j.get("formattedLocation") or ""
                    yield Job(
                        source=self.name,
                        company=j.get("company", ""),
                        title=j.get("displayTitle") or j.get("title", ""),
                        url=f"https://www.indeed.com/viewjob?jk={key}",
                        location=loc,
                        description=strip_html(" ".join(j.get("jobCardRequirementsModel", {}).get("jobOnlyRequirements", []) or []) + " " + (j.get("snippet") or "")),
                        remote=bool(j.get("remoteLocation")) or looks_remote(loc),
                        posted_at=None,
                    )
                time.sleep(float(self.config.get("delay_seconds", 4)))
