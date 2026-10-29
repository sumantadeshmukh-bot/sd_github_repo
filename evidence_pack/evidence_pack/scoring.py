"""Scoring, gaps, ranked themes, 90-day plan and assumptions. Everything is derived from the register."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .checks import REGISTRY, Ctx
from .models import AreaScore, CheckResult, Gap, Register, Scored, TIER_ORDER
from .tiering import enrich, tier_use_case

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
PHASES = [("Days 0-30", "High"), ("Days 31-60", "Medium"), ("Days 61-90", "Low")]
SEV_RANK = {"High": 3, "Medium": 2, "Low": 1}


class Config:
    def __init__(self, config_dir: Path = CONFIG_DIR) -> None:
        load = lambda n: yaml.safe_load((config_dir / n).read_text(encoding="utf-8"))  # noqa: E731
        self.areas_doc, self.tiering, self.scoring = load("areas.yaml"), load("tiering.yaml"), load("scoring.yaml")
        self.checks = {c["id"]: c for c in load("checks.yaml")["checks"]}
        self.areas = self.areas_doc["areas"]
        self.area_by_key = {a["key"]: a for a in self.areas}
        problems = [f"check {c['id']} has no logic in checks.py" for c in self.checks.values() if c["id"] not in REGISTRY]
        problems += [f"check {c['id']} uses unknown area '{c['area']}'" for c in self.checks.values()
                     if c["area"] not in self.area_by_key]
        problems += [f"checks.py defines {k} but checks.yaml has no entry" for k in REGISTRY if k not in self.checks]
        if problems:
            raise ValueError("Config problems: " + "; ".join(problems))

    def band(self, pct: float) -> int:
        return max(level for level, floor in self.scoring["bands"].items() if pct >= floor)

    def severity(self, score: float) -> str:
        t = self.scoring["severity_thresholds"]
        return "High" if score >= t["High"] else "Medium" if score >= t["Medium"] else "Low"


def score_register(reg: Register, cfg: Config | None = None) -> Scored:
    cfg = cfg or Config()
    as_of = reg.org.as_of or date.today()
    tiers, scores = {}, {}
    assumptions: list[str] = []
    for uc in reg.use_cases:
        enrich(uc, cfg.tiering)
        assumptions += uc.assumptions
        tiers[uc.id] = tier_use_case(uc, cfg.tiering)

    values = cfg.scoring["result_value"]
    for uc in reg.use_cases:
        ctx = Ctx(reg.org, tiers[uc.id].tier, cfg.scoring, as_of)
        per_area: dict[str, list[CheckResult]] = defaultdict(list)
        for cid, meta in cfg.checks.items():
            result, note = REGISTRY[cid](uc, ctx)
            per_area[meta["area"]].append(CheckResult(cid, meta["area"], result, note, meta["weight"]))
        scores[uc.id] = {}
        for area in cfg.areas:
            res = per_area[area["key"]]
            live = [r for r in res if r.result != "na"]
            den = sum(r.weight for r in live)
            pct = sum(r.weight * values[r.result] for r in live) / den if den else 1.0
            scores[uc.id][area["key"]] = AreaScore(area["key"], cfg.band(pct), pct, res)

    tf = cfg.tiering["tier_factor"]
    area_avg = {}
    for area in cfg.areas:
        num = sum(scores[u.id][area["key"]].score * tf[tiers[u.id].tier] for u in reg.use_cases)
        area_avg[area["key"]] = round(num / sum(tf[tiers[u.id].tier] for u in reg.use_cases), 2)
    overall = round(sum(area_avg.values()) / len(area_avg), 2)

    gaps = build_gaps(reg, cfg, tiers, scores)
    themes = build_themes(cfg, gaps, reg)
    plan = build_plan(cfg, themes)
    assumptions = sorted(set(assumptions)) + standing_assumptions(cfg, as_of)
    return Scored(reg, tiers, scores, area_avg, overall, gaps, themes, plan, assumptions)


def build_gaps(reg: Register, cfg: Config, tiers, scores) -> list[Gap]:
    values, tf = cfg.scoring["result_value"], cfg.tiering["tier_factor"]
    top_tier = max((tiers[u.id].tier for u in reg.use_cases), key=TIER_ORDER.__getitem__)
    gaps: list[Gap] = []
    for cid, meta in cfg.checks.items():
        for uc in reg.use_cases:
            cr = next(r for r in scores[uc.id][meta["area"]].results if r.check_id == cid)
            if cr.result in ("pass", "na"):
                continue
            if meta["scope"] == "org" and uc is not reg.use_cases[0]:
                continue                       # organisation-level gaps are listed once
            org_level = meta["scope"] == "org"
            tier = top_tier if org_level else tiers[uc.id].tier
            sev_score = round(meta["weight"] * (1 - values[cr.result]) * tf[tier], 1)
            wording = meta["gap"] + (" (no evidence provided)" if cr.result == "unknown" else "")
            gaps.append(Gap(cid, meta["area"], "ALL" if org_level else uc.id, wording, cr.note, cr.result,
                            cfg.severity(sev_score), sev_score,
                            "AI governance lead (to assign)" if org_level else (uc.business_owner or "Owner to be named"),
                            tier))
    gaps.sort(key=lambda g: (-g.severity_score, g.check_id, g.use_case_id))
    return gaps


def build_themes(cfg: Config, gaps: list[Gap], reg: Register) -> list[dict[str, Any]]:
    """Group gaps by check so a gap that repeats across use cases is one theme, ranked by total severity."""
    by_check: dict[str, list[Gap]] = defaultdict(list)
    for g in gaps:
        by_check[g.check_id].append(g)
    themes = []
    for cid, gs in by_check.items():
        meta = cfg.checks[cid]
        top = max(gs, key=lambda g: g.severity_score)
        themes.append({
            "check_id": cid, "area": meta["area"], "title": meta["gap"], "action": meta["action"],
            "effort": meta["effort"], "done_when": meta["done_when"], "use_cases": sorted({g.use_case_id for g in gs}),
            "count": len(gs), "severity": cfg.severity(top.severity_score), "max_score": top.severity_score,
            "total_score": round(sum(g.severity_score for g in gs), 1),
            "owners": _short_owners(gs),
            "org_level": meta["scope"] == "org"})
    themes.sort(key=lambda t: (-t["total_score"], -SEV_RANK[t["severity"]], t["check_id"]))
    for i, t in enumerate(themes, 1):
        t["rank"] = i
    return themes


def _short_owners(gs: list[Gap]) -> list[str]:
    """Up to two owner names, highest-severity gap first, without job titles (e.g. 'Anita Gowda (Demo)')."""
    names: list[str] = []
    for g in sorted(gs, key=lambda x: -x.severity_score):
        short = g.owner.split(",")[0].strip()
        if short and short not in names:
            names.append(short)
    return names[:2]


def build_plan(cfg: Config, themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phase_of = {sev: name for name, sev in PHASES}
    plan = []
    for t in themes:
        plan.append({
            "phase": phase_of[t["severity"]], "action": t["action"], "area": cfg.area_by_key[t["area"]]["name"],
            "addresses": f"{t['check_id']} ({'organisation' if t['org_level'] else str(t['count']) + ' use case(s)'})",
            "priority": t["severity"], "owner": "AI governance lead (to assign)" if t["org_level"] else "; ".join(t["owners"]),
            "effort": t["effort"], "done_when": t["done_when"], "theme_rank": t["rank"]})
    order = {name: i for i, (name, _) in enumerate(PHASES)}
    plan.sort(key=lambda p: (order[p["phase"]], p["theme_rank"]))
    return plan


def standing_assumptions(cfg: Config, as_of: date) -> list[str]:
    s = cfg.scoring
    return [
        f"Assessment date used for ages and deadlines: {as_of.isoformat()}.",
        "Scores are computed only from the register and the control answers supplied. Nothing was tested, observed or "
        "independently verified; every answer is taken as the client stated it.",
        "A control answer left blank is scored as 'not evidenced' (zero points), not as 'no'.",
        "Check not applicable to a use case (for example retention for a use case with no personal data) is left out of that "
        "use case's score.",
        f"Score bands on the share of applicable weighted points earned: 1 below {s['bands'][2]:.0%}, 2 from {s['bands'][2]:.0%}, "
        f"3 from {s['bands'][3]:.0%}, 4 from {s['bands'][4]:.0%}.",
        f"Validation age limits by tier (months): {s['validation_max_age_months']}; up to twice the limit scores 'partial'.",
        "Monitoring answered 'yes' or 'basic' is scored 'partial' (no evidence of drift monitoring); only 'continuous' scores full.",
        "Area scores across use cases are tier-weighted means (High x3, Medium x2, Low x1); the overall score is the plain mean of "
        "the eight area scores.",
        "Risk tiers follow the rules in config/tiering.yaml; the highest matching tier wins.",
        "Organisation-level controls (policy, governance, inventory review, audit cycle) are applied to every use case but each "
        "gap is listed once.",
        f"All references to RBI FREE-AI recommendations, OWASP GenAI and the DPDP Act 2023 are {cfg.areas_doc['verify_note']}.",
        "RBI draft Model Risk Management guidance (Jun 2026) is not yet mapped to the areas (see config/areas.yaml).",
        "Severity of a gap = check weight x shortfall x tier factor; severity bands are in config/scoring.yaml.",
        "Due dates in the Gaps sheet are left blank for the client to set.",
    ]
