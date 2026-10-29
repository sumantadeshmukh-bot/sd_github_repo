"""Plain-English text generated from the scored data (no hand-written findings)."""
from __future__ import annotations

from collections import Counter

from .models import Scored
from .palette import level
from .scoring import Config

DISCLAIMER = (
    "This document is readiness advice based on the AI use-case register and control answers supplied by the organisation. "
    "It is not a formal audit, certification or assurance opinion, and nothing in it was independently tested or verified. "
    "Formal cyber-security audits must be performed by CERT-In empanelled auditors. Regulatory references are working "
    "mappings and are to be verified against the source text before client use."
)


def lc(text: str) -> str:
    """Lower-case only the first letter, so acronyms such as AI stay intact in running text."""
    return text[:1].lower() + text[1:]


def label(cfg: Config, score: float) -> str:
    return cfg.areas_doc["score_labels"][level(score)]


def tier_counts(sc: Scored) -> Counter:
    return Counter(t.tier for t in sc.tiers.values())


def ranked_areas(sc: Scored, cfg: Config) -> list[tuple[str, float]]:
    return sorted(((a["key"], sc.area_avg[a["key"]]) for a in cfg.areas), key=lambda kv: (kv[1], kv[0]))


def headline(sc: Scored, cfg: Config) -> str:
    ra = ranked_areas(sc, cfg)
    weak, strong = cfg.area_by_key[ra[0][0]]["name"], cfg.area_by_key[ra[-1][0]]["name"]
    return (f"Overall AI readiness is {sc.overall:.1f} out of 4 ({label(cfg, sc.overall).lower()}). "
            f"The strongest area is {lc(strong)} ({ra[-1][1]:.1f}); the weakest is {lc(weak)} ({ra[0][1]:.1f}).")


def exec_summary(sc: Scored, cfg: Config) -> list[str]:
    org, tc = sc.register.org, tier_counts(sc)
    n = len(sc.register.use_cases)
    high = [g for g in sc.gaps if g.severity == "High"]
    size = f" ({org.size})" if org.size else ""
    urgent = sorted((u for u in sc.register.use_cases if sc.tiers[u.id].tier == "High"),
                    key=lambda u: sum(a.score for a in sc.scores[u.id].values()))[:3]
    out = [
        f"We reviewed {n} AI use case{'s' if n != 1 else ''} at {org.name}{size}. Under the agreed tiering rules "
        f"{tc['High']} are High risk, {tc['Medium']} Medium and {tc['Low']} Low.",
        headline(sc, cfg),
        f"We found {len(sc.gaps)} gaps across {len(sc.themes)} themes; {len(high)} are rated High. The three themes with the "
        f"largest combined weight are: " + "; ".join(f"{lc(t['title'])} ({t['count']} use case{'s' if t['count'] != 1 else ''})"
                                                    for t in sc.themes[:3]) + ".",
    ]
    if urgent:
        out.append("High-risk use cases with the lowest average scores, to look at first: " +
                   "; ".join(f"{u.id} {u.name} ({sum(a.score for a in sc.scores[u.id].values()) / 8:.1f})" for u in urgent) + ".")
    p1 = [p for p in sc.plan if p["phase"] == "Days 0-30"]
    out.append(f"The 90-day plan has {len(sc.plan)} actions; {len(p1)} are in the first 30 days (High-severity gaps).")
    return out


def area_findings(sc: Scored, cfg: Config, area_key: str) -> dict:
    """Per-check counts across use cases for one area, plus the use cases that miss each check."""
    rows = []
    for cid, meta in cfg.checks.items():
        if meta["area"] != area_key:
            continue
        cells = {u.id: next(r for r in sc.scores[u.id][area_key].results if r.check_id == cid) for u in sc.register.use_cases}
        live = {k: v for k, v in cells.items() if v.result != "na"}
        if not live:
            rows.append({"id": cid, "title": meta["title"], "applicable": 0})
            continue
        c = Counter(v.result for v in live.values())
        rows.append({"id": cid, "title": meta["title"], "applicable": len(live), "pass": c["pass"], "partial": c["partial"],
                     "miss": c["fail"] + c["unknown"], "missing_ids": [k for k, v in live.items() if v.result in ("fail", "unknown")]})
    return {"checks": rows, "scores": {u.id: sc.scores[u.id][area_key].score for u in sc.register.use_cases}}


def check_sentence(r: dict) -> str:
    if not r["applicable"]:
        return f"{r['title']}: not applicable to any use case."
    s = f"{r['title']}: {r['pass']} of {r['applicable']} fully meet it"
    if r["partial"]:
        s += f", {r['partial']} partly"
    if r["miss"]:
        ids = r["missing_ids"]
        s += f", {r['miss']} do not or are not evidenced ({', '.join(ids[:4])}{', ...' if len(ids) > 4 else ''})"
    return s + "."


def board_decisions(sc: Scored) -> list[str]:
    """Decisions requested from the board, only for the gaps that actually exist."""
    ids = {t["check_id"] for t in sc.themes}
    out = []
    if "ACC-2" in ids:
        out.append("Approve a board-level AI policy (scope, risk appetite, roles, approvals).")
    if "ACC-3" in ids:
        out.append("Name an accountable AI governance lead and set up a monthly AI risk forum.")
    if {"LIF-1", "LIF-2", "SEC-1"} & ids:
        out.append("Fund independent validation, evaluation sets and security testing for the High-risk use cases first.")
    if "OVS-1" in ids:
        out.append("Require a human checkpoint on automated High- and Medium-risk decisions until controls are in place.")
    if {"VEN-2", "VEN-3", "VEN-4"} & ids:
        out.append("Ask management to renegotiate AI vendor terms: model-change notice, data-use limits and an exit plan.")
    if "MON-4" in ids:
        out.append("Add AI systems to the internal audit plan, High-risk use cases first.")
    return out[:5] or ["No board decisions are needed beyond noting this report."]
