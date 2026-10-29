"""Check logic. Each check returns (result, note) where result is pass | partial | fail | unknown | na.

``unknown`` means no evidence was provided (scored as zero, worded as "not evidenced").
Weights, titles and gap wording live in config/checks.yaml; thresholds in config/scoring.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from .models import Organisation, UseCase
from .tiering import outside_india


@dataclass
class Ctx:
    org: Organisation
    tier: str
    scoring: dict[str, Any]
    as_of: date


Result = tuple[str, str]
REGISTRY: dict[str, Callable[[UseCase, Ctx], Result]] = {}


def check(check_id: str):
    def deco(fn: Callable[[UseCase, Ctx], Result]):
        REGISTRY[check_id] = fn
        return fn
    return deco


def ctl(uc: UseCase, ctx: Ctx, name: str) -> str:
    """Control answer: the use case's own answer, else the organisation default, else unknown."""
    return uc.controls.get(name) or ctx.org.controls.get(name) or "unknown"


def from_ctl(value: str, label: str) -> Result:
    return {"yes": ("pass", f"{label}: yes"), "partial": ("partial", f"{label}: partly in place"),
            "no": ("fail", f"{label}: no"), "unknown": ("unknown", f"{label}: not stated")}[value]


def _months(a: date, b: date) -> float:
    return (b - a).days / 30.4375


# ------------------------------------------------------------------ 1 Accountability
@check("ACC-1")
def acc_1(uc, ctx):
    owner = uc.business_owner.strip()
    if not owner or owner.lower() in ctx.scoring["placeholder_owners"]:
        return "fail", "no named owner in the register"
    return "pass", f"owner: {owner}"


@check("ACC-2")
def acc_2(uc, ctx):
    return from_ctl(ctl(uc, ctx, "board_approved_ai_policy"), "board-approved AI policy")


@check("ACC-3")
def acc_3(uc, ctx):
    return from_ctl(ctl(uc, ctx, "governance_committee"), "AI governance owner or committee")


# ------------------------------------------------------------------ 2 Inventory
@check("INV-1")
def inv_1(uc, ctx):
    filled = {
        "id": uc.id, "name": uc.name, "business_owner": uc.business_owner, "purpose": uc.purpose,
        "model_vendor": uc.model_vendor, "data_categories": uc.data_categories, "users_affected": uc.users_affected,
        "decision_impact": uc.decision_impact, "human_in_loop": uc.human_in_loop is not None,
        "hosting_location": uc.hosting_location, "monitoring": uc.monitoring,
        "last_validation_date": uc.last_validation_date, "incidents_12m": uc.incidents_12m is not None}
    fields = ctx.scoring["core_fields"]
    missing = [f for f in fields if not filled.get(f) and filled.get(f) != 0]
    share = 1 - len(missing) / len(fields)
    if not missing:
        return "pass", f"all {len(fields)} core fields filled"
    return ("partial" if share >= 0.8 else "fail"), f"{len(fields) - len(missing)}/{len(fields)} core fields filled; missing: {', '.join(missing)}"


@check("INV-2")
def inv_2(uc, ctx):
    if uc.client_risk_tier:
        return "pass", f"client's own tier: {uc.client_risk_tier}"
    return "fail", "no risk tier in the client's register (tier below was assigned by this tool)"


@check("INV-3")
def inv_3(uc, ctx):
    return from_ctl(ctl(uc, ctx, "inventory_review"), "inventory reviewed on a fixed cycle")


# ------------------------------------------------------------------ 3 Data governance
@check("DAT-1")
def dat_1(uc, ctx):
    if uc.data_categories and uc.personal_data is not None:
        return "pass", f"categories: {', '.join(uc.data_categories)}; personal data {'yes' if uc.personal_data else 'no'}"
    if uc.data_categories:
        return "partial", "categories listed but personal/sensitive data flag not stated"
    return "fail", "no data categories declared"


@check("DAT-2")
def dat_2(uc, ctx):
    if not uc.personal_effective:
        return "na", "no personal data"
    return from_ctl(ctl(uc, ctx, "dpdp_basis_documented"), "DPDP lawful basis / consent documented")


@check("DAT-3")
def dat_3(uc, ctx):
    if not uc.personal_effective:
        return "na", "no personal data"
    return from_ctl(ctl(uc, ctx, "retention_defined"), "retention defined")


@check("DAT-4")
def dat_4(uc, ctx):
    out = outside_india(uc)
    if out is None:
        return "fail", "hosting / data location not recorded"
    if not out:
        return "pass", f"hosted in India ({uc.hosting_location})"
    if not uc.personal_effective:
        return "pass", f"hosted outside India ({uc.hosting_location}); no personal data"
    r, n = from_ctl(ctl(uc, ctx, "cross_border_assessed"), "cross-border transfer assessed")
    return r, f"hosted outside India ({uc.hosting_location}); {n}"


# ------------------------------------------------------------------ 4 Lifecycle and evals
@check("LIF-1")
def lif_1(uc, ctx):
    if uc.last_validation_date is None:
        return "unknown", "no validation date recorded"
    limit = ctx.scoring["validation_max_age_months"][ctx.tier]
    age = _months(uc.last_validation_date, ctx.as_of)
    note = f"last validated {uc.last_validation_date.isoformat()} ({age:.0f} months ago; {ctx.tier} tier allows {limit})"
    if age < 0:
        return "unknown", f"validation date {uc.last_validation_date.isoformat()} is in the future"
    return ("pass" if age <= limit else "partial" if age <= 2 * limit else "fail"), note


@check("LIF-2")
def lif_2(uc, ctx):
    return from_ctl(ctl(uc, ctx, "evals_release_gate"), "evals used as a release gate")


@check("LIF-3")
def lif_3(uc, ctx):
    if "customers" not in uc.users_affected:
        return "na", "does not affect customers"
    return from_ctl(ctl(uc, ctx, "bias_testing"), "bias / fairness testing")


# ------------------------------------------------------------------ 5 AI security
@check("SEC-1")
def sec_1(uc, ctx):
    label = "prompt-injection / adversarial testing" if uc.uses_genai else "adversarial / robustness testing"
    return from_ctl(ctl(uc, ctx, "security_testing"), label)


@check("SEC-2")
def sec_2(uc, ctx):
    return from_ctl(ctl(uc, ctx, "least_privilege"), "least-privilege access for tools, agents and credentials")


@check("SEC-3")
def sec_3(uc, ctx):
    if not (uc.uses_genai or uc.personal_effective):
        return "na", "no GenAI and no personal data"
    return from_ctl(ctl(uc, ctx, "output_controls"), "output filtering / data-leak controls")


# ------------------------------------------------------------------ 6 Human oversight
@check("OVS-1")
def ovs_1(uc, ctx):
    if uc.decision_impact == "advisory":
        return "pass", "advisory only: a person makes the decision"
    if not uc.decision_impact:
        return "unknown", "decision impact (advisory / automated) not stated"
    if uc.human_in_loop is True:
        return "pass", "automated, with a human in the loop"
    if uc.human_in_loop is None:
        return "unknown", "automated; human-in-the-loop not stated"
    if ctx.tier == "High":
        return "fail", "automated High-tier decisions with no human in the loop"
    if ctx.tier == "Medium":
        return "partial", "automated Medium-tier decisions with no human in the loop"
    return "pass", "automated Low-tier decisions without a human in the loop (acceptable for the tier)"


@check("OVS-2")
def ovs_2(uc, ctx):
    if "customers" not in uc.users_affected:
        return "na", "does not affect customers"
    return from_ctl(ctl(uc, ctx, "explainability"), "explainability of outputs")


@check("OVS-3")
def ovs_3(uc, ctx):
    if not (uc.decision_impact == "automated" and "customers" in uc.users_affected):
        return "na", "no automated decisions about customers"
    return from_ctl(ctl(uc, ctx, "customer_recourse"), "customer recourse for automated decisions")


# ------------------------------------------------------------------ 7 Vendor AI risk
@check("VEN-1")
def ven_1(uc, ctx):
    return ("pass", f"model / vendor: {uc.model_vendor}") if uc.model_vendor else ("fail", "model / vendor not recorded")


@check("VEN-2")
def ven_2(uc, ctx):
    if not uc.third_party:
        return "na", "built in-house"
    return from_ctl(ctl(uc, ctx, "model_change_notice"), "model-change notice / version pinning")


@check("VEN-3")
def ven_3(uc, ctx):
    if not uc.third_party:
        return "na", "built in-house"
    return from_ctl(ctl(uc, ctx, "vendor_exit_plan"), "vendor exit plan")


@check("VEN-4")
def ven_4(uc, ctx):
    if not uc.third_party or not (uc.uses_genai or uc.personal_effective):
        return "na", "no third-party model, or no GenAI / personal data"
    return from_ctl(ctl(uc, ctx, "vendor_no_training"), "contractual limit on vendor use of data")


# ------------------------------------------------------------------ 8 Monitoring and audit
@check("MON-1")
def mon_1(uc, ctx):
    return {"continuous": ("pass", "continuous monitoring in place"),
            "basic": ("partial", "basic / periodic monitoring only (no evidence of drift monitoring)"),
            "none": ("fail", "no monitoring in place"),
            "": ("unknown", "monitoring not stated")}[uc.monitoring]


@check("MON-2")
def mon_2(uc, ctx):
    if uc.incidents_12m is None:
        return "unknown", "incident count not stated"
    if uc.incidents_12m == 0:
        return "pass", "0 incidents in 12 months (confirm an incident log exists)"
    review = ctl(uc, ctx, "incident_postmortem")
    if review == "yes":
        return "pass", f"{uc.incidents_12m} incident(s); post-incident reviews done"
    base = "fail" if uc.incidents_12m >= ctx.scoring["incident_fail_threshold"] else "partial"
    return base, f"{uc.incidents_12m} incident(s) in 12 months; post-incident review {'not done' if review == 'no' else 'not evidenced'}"


@check("MON-3")
def mon_3(uc, ctx):
    if ctx.tier == "Low" and uc.decision_impact != "automated":
        return "na", "Low tier and advisory only"
    return from_ctl(ctl(uc, ctx, "fallback_plan"), "fallback / continuity plan")


@check("MON-4")
def mon_4(uc, ctx):
    return from_ctl(ctl(uc, ctx, "risk_based_audit"), "AI systems covered by a risk-based audit cycle")
