import csv
from datetime import date
from pathlib import Path

import pytest

from evidence_pack.loader import RegisterError, load_register, norm_control, norm_key

ROOT = Path(__file__).resolve().parents[1]


def test_both_samples_load_with_expected_sizes():
    assert len(load_register(ROOT / "samples/fintech_120.yaml").use_cases) == 8
    reg = load_register(ROOT / "samples/nbfc_12.csv", ROOT / "samples/nbfc_org.yaml")
    assert len(reg.use_cases) == 12 and reg.org.name.startswith("Hemavati") and reg.org.as_of == date(2026, 10, 29)


def test_csv_headers_from_the_brief_are_recognised():
    assert norm_key("Business owner") == "business_owner" and norm_key("Model/vendor") == "model_vendor"
    assert norm_key("Human-in-the-loop") == "human_in_loop" and norm_key("Hosting/data location") == "hosting_location"
    assert norm_key("Incidents in last 12 months") == "incidents_12m" and norm_key("ctl_evals_release_gate") == "evals_release_gate"


def test_control_answers_are_normalised():
    assert [norm_control(x) for x in ("Yes", "regular", "draft", "annual", "No", "", None, "???", True)] == \
        ["yes", "yes", "partial", "partial", "no", "unknown", "unknown", "unknown", "yes"]


def write_csv(path: Path, rows: list[dict]) -> Path:
    cols = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return path


def test_csv_values_are_parsed(tmp_path):
    p = write_csv(tmp_path / "r.csv", [{"ID": "U1", "Name": "n", "Users affected": "Customers; Staff", "Decision impact": "Automated",
                                         "Human-in-the-loop": "No", "Last validation date": "15/03/2026",
                                         "Monitoring in place": "yes", "Incidents in last 12 months": "2"}])
    u = load_register(p).use_cases[0]
    assert u.users_affected == ["customers", "staff"] and u.decision_impact == "automated" and u.human_in_loop is False
    assert u.last_validation_date == date(2026, 3, 15) and u.monitoring == "basic" and u.incidents_12m == 2


def test_all_problems_are_reported_together(tmp_path):
    p = write_csv(tmp_path / "bad.csv", [
        {"ID": "U1", "Name": "n", "Decision impact": "sometimes", "Last validation date": "last spring",
         "Incidents in last 12 months": "many"},
        {"ID": "U1", "Name": "dup"}, {"ID": "", "Name": "no id"}])
    with pytest.raises(RegisterError) as e:
        load_register(p)
    msg = str(e.value)
    assert "decision_impact" in msg and "not a date" in msg and "whole number" in msg and "Duplicate" in msg and "without id" in msg


def test_missing_file_and_bad_types(tmp_path):
    with pytest.raises(RegisterError, match="not found"):
        load_register(tmp_path / "nope.csv")
    f = tmp_path / "x.txt"
    f.write_text("hi")
    with pytest.raises(RegisterError, match="Unsupported"):
        load_register(f)
    y = tmp_path / "x.yaml"
    y.write_text("organisation: {name: X}\n")
    with pytest.raises(RegisterError, match="use_cases"):
        load_register(y)


def test_yaml_dates_and_booleans(tmp_path):
    y = tmp_path / "r.yaml"
    y.write_text("use_cases:\n  - {id: A, name: n, personal_data: yes, last_validation_date: 2026-05-01, human_in_loop: no}\n")
    u = load_register(y).use_cases[0]
    assert u.personal_data is True and u.human_in_loop is False and u.last_validation_date == date(2026, 5, 1)


def test_unknown_columns_become_control_answers(tmp_path):
    p = write_csv(tmp_path / "r.csv", [{"ID": "A", "Name": "n", "ctl_vendor_exit_plan": "yes", "fallback_plan": "partial"}])
    assert load_register(p).use_cases[0].controls == {"vendor_exit_plan": "yes", "fallback_plan": "partial"}
