"""Load and validate config/profile.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_WEIGHTS = {
    "title_match": 5,
    "skill_must": 4,
    "skill_strong": 3,
    "skill_nice": 1,
    "sponsor_history": 6,
    "sponsor_language": 5,
    "location_pref": 2,
    "salary_ok": 2,
    "recent_posting": 2,
}


@dataclass
class Profile:
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path = "config/profile.yaml") -> "Profile":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Copy config/profile.yaml from the repo and edit it."
            )
        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
        return cls(raw=data)

    def _get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def _lower_list(self, *keys: str) -> list[str]:
        vals = self._get(*keys, default=[]) or []
        return [str(v).lower().strip() for v in vals if str(v).strip()]

    # --- matching inputs -------------------------------------------------
    @property
    def titles_include(self) -> list[str]: return self._lower_list("titles", "include")
    @property
    def titles_exclude(self) -> list[str]: return self._lower_list("titles", "exclude")
    @property
    def must_have(self) -> list[str]: return self._lower_list("skills", "must_have")
    @property
    def strong(self) -> list[str]: return self._lower_list("skills", "strong")
    @property
    def nice(self) -> list[str]: return self._lower_list("skills", "nice")
    @property
    def loc_preferred(self) -> list[str]: return self._lower_list("location", "preferred")
    @property
    def loc_exclude(self) -> list[str]: return self._lower_list("location", "exclude")
    @property
    def blockers(self) -> list[str]: return self._lower_list("sponsorship", "blockers")
    @property
    def boosters(self) -> list[str]: return self._lower_list("sponsorship", "boosters")

    @property
    def remote_ok(self) -> bool: return bool(self._get("location", "remote_ok", default=True))
    @property
    def min_salary(self) -> int: return int(self._get("compensation", "min_salary", default=0) or 0)
    @property
    def drop_below_min(self) -> bool:
        return bool(self._get("compensation", "drop_below_min", default=False))
    @property
    def max_years_required(self) -> int:
        return int(self._get("seniority", "max_years_required", default=99) or 99)
    @property
    def years_experience(self) -> int:
        return int(self._get("identity", "years_experience", default=0) or 0)
    @property
    def visa_status(self) -> str:
        return str(self._get("identity", "visa_status", default="requires H-1B sponsorship"))
    @property
    def min_score(self) -> float:
        return float(self._get("scoring", "min_score", default=0.35))
    @property
    def fresh_days(self) -> int:
        return int(self._get("scoring", "fresh_days", default=14))

    @property
    def weights(self) -> dict[str, float]:
        w = dict(DEFAULT_WEIGHTS)
        w.update(self._get("scoring", "weights", default={}) or {})
        return {k: float(v) for k, v in w.items()}

    def validate(self) -> list[str]:
        problems = []
        if not self.titles_include:
            problems.append("titles.include is empty — nothing will score well.")
        if not (self.must_have or self.strong or self.nice):
            problems.append("no skills defined — scoring will be title-only.")
        return problems
