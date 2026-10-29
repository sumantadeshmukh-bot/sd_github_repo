"""Data classes shared by the loader, scorer and report writers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

TIER_ORDER = {"Low": 0, "Medium": 1, "High": 2}


@dataclass
class UseCase:
    id: str
    name: str
    business_owner: str = ""
    purpose: str = ""
    model_vendor: str = ""
    data_categories: list[str] = field(default_factory=list)
    personal_data: bool | None = None        # as declared in the register (None = not stated)
    sensitive_data: bool | None = None
    users_affected: list[str] = field(default_factory=list)   # e.g. ["customers", "staff"]
    decision_impact: str = ""                # "advisory" | "automated" | ""
    human_in_loop: bool | None = None
    hosting_location: str = ""
    monitoring: str = ""                     # "none" | "basic" | "continuous" | ""
    last_validation_date: date | None = None
    incidents_12m: int | None = None
    client_risk_tier: str = ""
    controls: dict[str, str] = field(default_factory=dict)    # optional per-use-case control answers
    # derived (filled by enrich())
    personal_effective: bool = True
    sensitive_effective: bool = False
    third_party: bool = True
    uses_genai: bool = False
    assumptions: list[str] = field(default_factory=list)


@dataclass
class Organisation:
    name: str = "Client (Demo)"
    sector: str = ""
    size: str = ""
    as_of: date | None = None
    prepared_by: str = ""
    controls: dict[str, str] = field(default_factory=dict)    # org-level control answers / defaults


@dataclass
class Register:
    org: Organisation
    use_cases: list[UseCase]
    source: str = ""


@dataclass
class TierResult:
    tier: str
    matched: list[tuple[str, str]]           # (rule id, description)


@dataclass
class CheckResult:
    check_id: str
    area: str
    result: str                              # pass | partial | fail | unknown | na
    note: str
    weight: float


@dataclass
class AreaScore:
    area: str
    score: int
    pct: float
    results: list[CheckResult]


@dataclass
class Gap:
    check_id: str
    area: str
    use_case_id: str                         # "ALL" for organisation-level gaps
    gap: str
    detail: str
    result: str
    severity: str
    severity_score: float
    owner: str
    tier: str
    due_date: str = ""                       # intentionally blank: the client sets it


@dataclass
class Scored:
    register: Register
    tiers: dict[str, TierResult]
    scores: dict[str, dict[str, AreaScore]]  # use case id -> area key -> AreaScore
    area_avg: dict[str, float]               # area key -> tier-weighted mean score
    overall: float
    gaps: list[Gap]
    themes: list[dict[str, Any]]             # gaps grouped by check, ranked
    plan: list[dict[str, Any]]
    assumptions: list[str]
