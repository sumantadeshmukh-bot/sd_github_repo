"""Risk tiering from transparent, configurable rules (config/tiering.yaml)."""
from __future__ import annotations

from typing import Any

from .models import TIER_ORDER, TierResult, UseCase


def enrich(uc: UseCase, cfg: dict[str, Any]) -> UseCase:
    """Derive personal/sensitive/third-party/GenAI flags; record an assumption whenever a flag is inferred."""
    text = " ".join(uc.data_categories).lower()
    if uc.personal_data is not None:
        uc.personal_effective = uc.personal_data
    elif uc.data_categories:
        uc.personal_effective = any(k in text for k in cfg["personal_keywords"])
        uc.assumptions.append(f"{uc.id}: personal data inferred as {'yes' if uc.personal_effective else 'no'} "
                              "from the data categories (not stated in the register).")
    else:
        uc.personal_effective = True
        uc.assumptions.append(f"{uc.id}: personal data assumed 'yes' because neither a flag nor data categories were given.")
    if uc.sensitive_data is not None:
        uc.sensitive_effective = uc.sensitive_data
    else:
        uc.sensitive_effective = any(k in text for k in cfg["sensitive_keywords"])
        if uc.sensitive_effective:
            uc.assumptions.append(f"{uc.id}: sensitive data inferred from the data categories (not stated in the register).")
    vendor = uc.model_vendor.lower()
    uc.third_party = bool(vendor) and not any(m in vendor for m in cfg["in_house_markers"])
    probe = f"{vendor} {uc.name} {uc.purpose}".lower()
    declared = uc.controls.get("uses_genai")
    uc.uses_genai = declared == "yes" if declared in ("yes", "no") else any(m in probe for m in cfg["genai_markers"])
    return uc


def outside_india(uc: UseCase) -> bool | None:
    loc = uc.hosting_location.lower()
    if not loc:
        return None
    return not any(k in loc for k in ("india", "on-prem", "on prem", "mumbai", "hyderabad", "chennai", "pune", "delhi"))


def _holds(cond: dict[str, Any], uc: UseCase) -> bool:
    facts = {
        "decision_impact": uc.decision_impact,
        "personal_data": uc.personal_effective,
        "sensitive_data": uc.sensitive_effective,
        "human_in_loop": uc.human_in_loop,
        "hosting_outside_india": bool(outside_india(uc)),
    }
    for key, want in cond.items():
        if key == "users_include":
            if want not in uc.users_affected:
                return False
        elif key == "incidents_min":
            if (uc.incidents_12m or 0) < int(want):
                return False
        elif key in facts:
            if facts[key] != want:
                return False
        else:
            raise ValueError(f"Unknown tiering condition '{key}'")
    return True


def tier_use_case(uc: UseCase, cfg: dict[str, Any]) -> TierResult:
    matched = [(r["id"], r["description"], r["tier"]) for r in cfg["rules"] if _holds(r["when"], uc)]
    tier = max((m[2] for m in matched), key=TIER_ORDER.__getitem__, default=cfg["default_tier"])
    return TierResult(tier, [(rid, desc) for rid, desc, t in matched if t == tier] or
                      [("default", f"No rule matched: default tier {cfg['default_tier']}")])
