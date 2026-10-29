"""board_summary.docx: two pages for the board."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import narrative as nv
from .docx_util import MUTED, add_table, bullet, footer, new_document, para
from .models import Scored
from .palette import SCORE_FILL, SCORE_TEXT, SEVERITY_FILL, SEVERITY_TEXT, level
from .scoring import Config


def write_board_summary(sc: Scored, cfg: Config, path: Path) -> Path:
    org, ucs, tc = sc.register.org, sc.register.use_cases, nv.tier_counts(sc)
    doc = new_document(9.5, 1.8)
    footer(doc, f"{org.name}: board summary (readiness advice, not a formal audit)")

    # ---------------- page 1
    para(doc, "Board summary: AI readiness", bold=True, size=20, after=0)
    para(doc, f"{org.name}  |  {(org.as_of or date.today()).strftime('%d %B %Y')}", size=10, color=MUTED, after=6)
    para(doc, nv.headline(sc, cfg), bold=True, size=12, after=4)
    add_table(doc, ["Overall", "AI use cases", "High / Medium / Low risk", "Gaps found (High)", "90-day actions"],
              [[f"{sc.overall:.1f} / 4", str(len(ucs)), f"{tc['High']} / {tc['Medium']} / {tc['Low']}",
                f"{len(sc.gaps)} ({sum(g.severity == 'High' for g in sc.gaps)})", str(len(sc.plan))]],
              [2.6, 2.8, 4.6, 3.8, 3.4], size=10, center_cols={0, 1, 2, 3, 4})
    doc.add_heading("Readiness by area (1 = not evidenced, 4 = managed and tested)", 2)
    rows, fills = [], {}
    for i, a in enumerate(cfg.areas):
        s = sc.area_avg[a["key"]]
        weak = [u.id for u in ucs if sc.scores[u.id][a["key"]].score <= 2]
        rows.append([a["name"], f"{s:.1f}", nv.label(cfg, s),
                     (f"{len(weak)} of {len(ucs)} use cases score 1 or 2" if weak else "No use case scores 1 or 2")])
        fills[(i, 1)] = (SCORE_FILL[level(s)], SCORE_TEXT[level(s)])
    add_table(doc, ["Area", "Score", "Level", "Where it is weak"], rows, [4.4, 1.6, 3.8, 7.4], fills=fills, center_cols={1})
    doc.add_heading("What the board needs to know", 2)
    for s in nv.exec_summary(sc, cfg)[:1] + nv.exec_summary(sc, cfg)[2:4]:
        bullet(doc, s)
    doc.add_page_break()

    # ---------------- page 2
    doc.add_heading("Top 5 gaps", 2)
    top = sc.themes[:5]
    add_table(doc, ["#", "Gap", "Where", "Severity"],
              [[str(t["rank"]), t["title"], "Organisation" if t["org_level"] else
               f"{t['count']} use case{'s' if t['count'] != 1 else ''}", t["severity"]] for t in top],
              [0.9, 10.4, 3.6, 2.3], fills={(i, 3): (SEVERITY_FILL[t["severity"]], SEVERITY_TEXT[t["severity"]])
                                            for i, t in enumerate(top)}, center_cols={0})
    doc.add_heading("Decisions we ask the board to take", 2)
    for d in nv.board_decisions(sc):
        bullet(doc, d)
    doc.add_heading("90-day plan at a glance", 2)
    rows = []
    for phase in ("Days 0-30", "Days 31-60", "Days 61-90"):
        acts = [p["action"] for p in sc.plan if p["phase"] == phase]
        rows.append([phase, "\n".join(f"- {x}" for x in acts[:3]) + (f"\n(+{len(acts) - 3} more)" if len(acts) > 3 else "")
                     if acts else "No actions in this phase"])
    add_table(doc, ["Phase", "Main actions"], rows, [2.4, 14.8], size=9)
    doc.add_heading("If nothing changes", 2)
    high_uc = [u for u in ucs if sc.tiers[u.id].tier == "High"]
    bullet(doc, f"{len(high_uc)} High-risk use case{'s' if len(high_uc) != 1 else ''} keep running with the gaps above, which "
                "is the position a supervisor or auditor would test first.")
    bullet(doc, "Gaps that repeat across many use cases (vendor terms, testing, monitoring) become more expensive to fix as "
                "more use cases are added.")
    para(doc, "", after=2)
    para(doc, nv.DISCLAIMER, italic=True, size=8.5, color=MUTED)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path
