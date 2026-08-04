"""Source plugin contract + shared HTTP helpers."""

from __future__ import annotations

import logging
import re
import time
from typing import Iterable

import requests

from ..models import Job

log = logging.getLogger(__name__)

USER_AGENT = "personal-h1b-jobsearch/1.0 (+https://github.com/)"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class Source:
    """Subclass and implement fetch(). Register in sources/__init__.py."""

    name: str = "base"
    #: Sources that scrape rather than use a sanctioned API stay off unless
    #: explicitly enabled in config/sources.yaml.
    requires_optin: bool = False

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.config.get("user_agent", USER_AGENT)

    def fetch(self, queries: list[str]) -> Iterable[Job]:
        raise NotImplementedError

    # --- helpers ---------------------------------------------------------
    def get_json(self, url: str, params: dict | None = None, retries: int = 2) -> dict | list | None:
        for attempt in range(retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=30)
                if r.status_code == 404:
                    return None
                if r.status_code == 429:
                    time.sleep(2 ** attempt * 2)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001 - one bad source must not kill the run
                if attempt == retries:
                    log.warning("%s: GET %s failed: %s", self.name, url, e)
                    return None
                time.sleep(1.5 * (attempt + 1))
        return None


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(entity, char)
    return _WS_RE.sub(" ", text).strip()


def looks_remote(*fields: str | None) -> bool:
    blob = " ".join(f.lower() for f in fields if f)
    return any(k in blob for k in ("remote", "anywhere", "distributed", "work from home"))
