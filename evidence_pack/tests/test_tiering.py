from evidence_pack.models import UseCase
from evidence_pack.tiering import enrich, outside_india, tier_use_case


def uc(**kw) -> UseCase:
    base = dict(id="X", name="x", personal_data=False, sensitive_data=False, users_affected=["staff"],
                decision_impact="advisory", hosting_location="India (Mumbai)", model_vendor="In-house model")
    base.update(kw)
    return UseCase(**base)


def tier(cfg, **kw):
    u = enrich(uc(**kw), cfg.tiering)
    return tier_use_case(u, cfg.tiering)


def test_automated_customer_decision_with_personal_data_is_high(cfg):
    t = tier(cfg, decision_impact="automated", users_affected=["customers"], personal_data=True)
    assert t.tier == "High" and t.matched[0][0] == "T1"


def test_same_without_personal_data_is_only_medium(cfg):
    assert tier(cfg, decision_impact="automated", users_affected=["customers"], personal_data=False).tier == "Medium"


def test_advisory_staff_tool_is_low_with_default_reason(cfg):
    t = tier(cfg)
    assert t.tier == "Low" and t.matched[0][0] == "default"


def test_sensitive_data_with_customers_is_high_even_if_advisory(cfg):
    assert tier(cfg, sensitive_data=True, personal_data=True, users_affected=["customers"]).tier == "High"


def test_two_incidents_with_personal_data_is_high_one_is_not(cfg):
    assert tier(cfg, personal_data=True, incidents_12m=2).tier == "High"
    assert tier(cfg, personal_data=True, incidents_12m=1).tier == "Low"


def test_personal_data_hosted_outside_india_is_medium(cfg):
    assert tier(cfg, personal_data=True, hosting_location="United States (vendor region)").tier == "Medium"


def test_highest_matching_tier_wins_and_reasons_are_those_of_that_tier(cfg):
    t = tier(cfg, decision_impact="automated", users_affected=["customers"], personal_data=True,
             hosting_location="United States")
    assert t.tier == "High" and all(r[0] in {"T1"} for r in t.matched)


def test_inference_of_personal_data_is_recorded_as_assumption(cfg):
    u = enrich(uc(personal_data=None, data_categories=["customer name", "phone"]), cfg.tiering)
    assert u.personal_effective is True and any("inferred" in a for a in u.assumptions)


def test_unknown_personal_data_is_assumed_yes(cfg):
    u = enrich(uc(personal_data=None, data_categories=[]), cfg.tiering)
    assert u.personal_effective is True and any("assumed 'yes'" in a for a in u.assumptions)


def test_genai_and_third_party_detection(cfg):
    u = enrich(uc(model_vendor="ExampleAI LLM API (Demo)"), cfg.tiering)
    assert u.uses_genai and u.third_party
    u2 = enrich(uc(model_vendor="In-house gradient-boosted model"), cfg.tiering)
    assert not u2.uses_genai and not u2.third_party


def test_hosting_location_parsing():
    assert outside_india(uc(hosting_location="")) is None
    assert outside_india(uc(hosting_location="India (own DC)")) is False
    assert outside_india(uc(hosting_location="EU (vendor SaaS)")) is True


def test_unknown_condition_in_rules_is_an_error(cfg):
    import copy
    import pytest
    bad = copy.deepcopy(cfg.tiering)
    bad["rules"][0]["when"] = {"colour": "red"}
    with pytest.raises(ValueError, match="Unknown tiering condition"):
        tier_use_case(enrich(uc(), bad), bad)
