from datetime import date

from evidence_pack.checks import REGISTRY, Ctx
from evidence_pack.models import Organisation, Register, UseCase
from evidence_pack.scoring import score_register

AS_OF = date(2026, 10, 29)


def make_uc(**kw) -> UseCase:
    base = dict(id="UC-1", name="Bot", business_owner="A Person (Demo)", purpose="p", model_vendor="ExampleAI LLM (Demo)",
                data_categories=["customer name"], personal_data=True, sensitive_data=False, users_affected=["customers"],
                decision_impact="advisory", human_in_loop=True, hosting_location="India (Mumbai)", monitoring="continuous",
                last_validation_date=date(2026, 9, 1), incidents_12m=0)
    base.update(kw)
    return UseCase(**base)


def make_reg(*ucs, org_controls=None) -> Register:
    return Register(Organisation(name="T (Demo)", as_of=AS_OF, controls=org_controls or {}), list(ucs))


ALL_YES = {k: "yes" for k in ("board_approved_ai_policy", "governance_committee", "inventory_review", "risk_based_audit",
                              "dpdp_basis_documented", "retention_defined", "cross_border_assessed", "evals_release_gate",
                              "bias_testing", "security_testing", "least_privilege", "output_controls", "explainability",
                              "customer_recourse", "model_change_notice", "vendor_exit_plan", "vendor_no_training",
                              "incident_postmortem", "fallback_plan")}


def test_every_check_has_config_and_logic(cfg):
    assert set(cfg.checks) == set(REGISTRY) and len(cfg.checks) >= 20
    assert {c["area"] for c in cfg.checks.values()} == {a["key"] for a in cfg.areas} and len(cfg.areas) == 8


def test_area_reference_mapping_is_in_config_and_flagged(cfg):
    refs = {a["id"]: a["references"] for a in cfg.areas}
    assert refs[1] == ["RBI FREE-AI rec. 14"] and refs[2] == ["RBI FREE-AI rec. 23"] and refs[8] == ["RBI FREE-AI recs. 21-24"]
    assert "to verify against the RBI FREE-AI report text before client use" in cfg.areas_doc["verify_note"]


def test_fully_evidenced_use_case_scores_4_everywhere(cfg):
    sc = score_register(make_reg(make_uc(client_risk_tier="Medium", controls=dict(ALL_YES)), org_controls=ALL_YES), cfg)
    assert [a.score for a in sc.scores["UC-1"].values()] == [4] * 8
    assert sc.overall == 4.0 and sc.gaps == []


def test_empty_evidence_scores_low_and_blank_is_unknown_not_no(cfg):
    sc = score_register(make_reg(make_uc(monitoring="", last_validation_date=None, incidents_12m=None)), cfg)
    results = {r.check_id: r.result for a in sc.scores["UC-1"].values() for r in a.results}
    assert results["MON-1"] == "unknown" and results["LIF-1"] == "unknown" and results["SEC-2"] == "unknown"
    assert min(a.score for a in sc.scores["UC-1"].values()) == 1
    gap = next(g for g in sc.gaps if g.check_id == "SEC-2")
    assert "no evidence provided" in gap.gap and gap.due_date == ""


def test_scores_are_between_1_and_4_and_evidence_notes_exist(cfg, packs):
    for pack in packs.values():
        for per_uc in pack.scored.scores.values():
            for area in per_uc.values():
                assert 1 <= area.score <= 4 and area.results and all(r.note for r in area.results)


def test_validation_recency_depends_on_tier(cfg):
    uc = make_uc(last_validation_date=date(2026, 1, 15))   # about 9.5 months old at 29 Oct 2026
    ctx = lambda tier: Ctx(Organisation(as_of=AS_OF), tier, cfg.scoring, AS_OF)  # noqa: E731
    assert REGISTRY["LIF-1"](uc, ctx("High"))[0] == "partial"    # limit 6 months, up to 12 is partial
    assert REGISTRY["LIF-1"](uc, ctx("Low"))[0] == "pass"        # limit 18 months
    old = make_uc(last_validation_date=date(2024, 1, 1))
    assert REGISTRY["LIF-1"](old, ctx("High"))[0] == "fail"
    assert REGISTRY["LIF-1"](make_uc(last_validation_date=date(2027, 1, 1)), ctx("Low"))[0] == "unknown"


def test_human_oversight_rule_matches_tier(cfg):
    ctx = lambda tier: Ctx(Organisation(as_of=AS_OF), tier, cfg.scoring, AS_OF)  # noqa: E731
    auto_no_human = make_uc(decision_impact="automated", human_in_loop=False)
    assert REGISTRY["OVS-1"](auto_no_human, ctx("High"))[0] == "fail"
    assert REGISTRY["OVS-1"](auto_no_human, ctx("Medium"))[0] == "partial"
    assert REGISTRY["OVS-1"](auto_no_human, ctx("Low"))[0] == "pass"
    assert REGISTRY["OVS-1"](make_uc(decision_impact="advisory", human_in_loop=False), ctx("High"))[0] == "pass"
    assert REGISTRY["OVS-1"](make_uc(decision_impact="automated", human_in_loop=None), ctx("High"))[0] == "unknown"


def test_not_applicable_checks_are_left_out(cfg):
    sc = score_register(make_reg(make_uc(personal_data=False, users_affected=["staff"], model_vendor="In-house model",
                                         data_categories=["policy documents"])), cfg)
    res = {r.check_id: r.result for a in sc.scores["UC-1"].values() for r in a.results}
    assert res["DAT-2"] == res["DAT-3"] == res["OVS-2"] == res["VEN-2"] == res["VEN-3"] == "na"
    assert not any(g.check_id in {"DAT-2", "OVS-2", "VEN-3"} for g in sc.gaps)


def test_cross_border_personal_data_needs_assessment(cfg):
    ctx = Ctx(Organisation(as_of=AS_OF), "Medium", cfg.scoring, AS_OF)
    uc = make_uc(hosting_location="United States (vendor)")
    assert REGISTRY["DAT-4"](uc, ctx)[0] == "unknown"
    uc.controls["cross_border_assessed"] = "yes"
    assert REGISTRY["DAT-4"](uc, ctx)[0] == "pass"


def test_per_use_case_control_overrides_org_default(cfg):
    uc = make_uc(controls={"evals_release_gate": "no"})
    ctx = Ctx(Organisation(as_of=AS_OF, controls={"evals_release_gate": "yes"}), "Low", cfg.scoring, AS_OF)
    assert REGISTRY["LIF-2"](uc, ctx)[0] == "fail"
    assert REGISTRY["LIF-2"](make_uc(), ctx)[0] == "pass"


def test_org_level_gap_listed_once_but_counts_in_every_score(cfg):
    sc = score_register(make_reg(make_uc(id="A"), make_uc(id="B")), cfg)
    assert [g.use_case_id for g in sc.gaps if g.check_id == "ACC-2"] == ["ALL"]
    for u in ("A", "B"):
        assert next(r for r in sc.scores[u]["accountability"].results if r.check_id == "ACC-2").result == "unknown"


def test_severity_grows_with_tier_and_shortfall(cfg):
    base = dict(controls={"least_privilege": "no"})
    high = score_register(make_reg(make_uc(id="H", decision_impact="automated", human_in_loop=True, **base)), cfg)
    low = score_register(make_reg(make_uc(id="L", personal_data=False, users_affected=["staff"], **base)), cfg)
    gh = next(g for g in high.gaps if g.check_id == "SEC-2")
    gl = next(g for g in low.gaps if g.check_id == "SEC-2")
    assert gh.tier == "High" and gl.tier == "Low" and gh.severity_score == 9.0 and gl.severity_score == 3.0
    assert (gh.severity, gl.severity) == ("High", "Medium")


def test_themes_group_repeated_gaps_and_are_ranked(cfg):
    sc = score_register(make_reg(make_uc(id="A"), make_uc(id="B"), make_uc(id="C")), cfg)
    t = next(t for t in sc.themes if t["check_id"] == "SEC-2")
    assert t["count"] == 3 and t["use_cases"] == ["A", "B", "C"]
    assert [x["rank"] for x in sc.themes] == list(range(1, len(sc.themes) + 1))
    assert [x["total_score"] for x in sc.themes] == sorted((x["total_score"] for x in sc.themes), reverse=True)


def test_plan_phases_follow_severity(cfg, packs):
    for pack in packs.values():
        phase = {"High": "Days 0-30", "Medium": "Days 31-60", "Low": "Days 61-90"}
        assert all(p["phase"] == phase[p["priority"]] for p in pack.scored.plan)
        order = [p["phase"] for p in pack.scored.plan]
        assert order == sorted(order, key=["Days 0-30", "Days 31-60", "Days 61-90"].index)


def test_assumptions_mention_blank_is_not_no_and_verify_note(cfg, packs):
    text = " ".join(packs["fintech"].scored.assumptions)
    assert "not evidenced" in text and "to verify against the RBI FREE-AI report text before client use" in text


def test_org_gap_uses_worst_answer_whatever_the_register_order(cfg):
    yes_first = score_register(make_reg(make_uc(id="A", controls={"board_approved_ai_policy": "yes"}),
                                        make_uc(id="B", controls={"board_approved_ai_policy": "no"})), cfg)
    no_first = score_register(make_reg(make_uc(id="B", controls={"board_approved_ai_policy": "no"}),
                                       make_uc(id="A", controls={"board_approved_ai_policy": "yes"})), cfg)
    for sc in (yes_first, no_first):
        gap = [g for g in sc.gaps if g.check_id == "ACC-2"]
        assert len(gap) == 1 and gap[0].result == "fail" and "answered this way for: B" in gap[0].detail


def test_blank_vendor_keeps_vendor_gaps_visible(cfg):
    sc = score_register(make_reg(make_uc(model_vendor="")), cfg)
    res = {r.check_id: r.result for a in sc.scores["UC-1"].values() for r in a.results}
    assert res["VEN-1"] == "fail" and res["VEN-3"] == "unknown" and any(g.check_id == "VEN-3" for g in sc.gaps)
