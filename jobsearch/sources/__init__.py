"""Source registry. Add a class here to make it available to the CLI."""

from __future__ import annotations

from .apis import (
    AdzunaSource,
    ArbeitnowSource,
    HackerNewsSource,
    RemoteOKSource,
    USAJobsSource,
)
from .ats import AshbySource, GreenhouseSource, LeverSource, SmartRecruitersSource
from .base import Source
from .optin import IndeedSource, LinkedInSource

REGISTRY: dict[str, type[Source]] = {
    cls.name: cls
    for cls in (
        GreenhouseSource, LeverSource, AshbySource, SmartRecruitersSource,
        RemoteOKSource, ArbeitnowSource, AdzunaSource, USAJobsSource,
        HackerNewsSource, LinkedInSource, IndeedSource,
    )
}

__all__ = ["REGISTRY", "Source"]
