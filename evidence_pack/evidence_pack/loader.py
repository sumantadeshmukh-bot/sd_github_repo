"""Read the AI use-case register from YAML or CSV and normalise it.

YAML:  organisation / org_controls / use_cases   (see samples/fintech_120.yaml)
CSV:   one row per use case; headers are matched loosely ("Business owner", "model/vendor", ...).
       Lists use ";" between items. Organisation details come from an optional --org YAML file.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .models import Organisation, Register, UseCase


class RegisterError(ValueError):
    """The register cannot be read (message lists every problem found)."""


ALIASES = {
    "owner": "business_owner", "model": "model_vendor", "vendor": "model_vendor", "model_or_vendor": "model_vendor",
    "personal_data_used": "personal_data", "sensitive_data_used": "sensitive_data",
    "human_in_the_loop": "human_in_loop", "hitl": "human_in_loop", "hosting_data_location": "hosting_location",
    "hosting": "hosting_location", "data_location": "hosting_location", "monitoring_in_place": "monitoring",
    "incidents_in_last_12_months": "incidents_12m", "incidents_last_12_months": "incidents_12m",
    "incidents": "incidents_12m", "incidents_in_last_12m": "incidents_12m", "validation_date": "last_validation_date",
    "risk_tier": "client_risk_tier", "use_case_id": "id", "use_case_name": "name", "users": "users_affected",
}
BASE_FIELDS = {"id", "name", "business_owner", "purpose", "model_vendor", "data_categories", "personal_data",
               "sensitive_data", "users_affected", "decision_impact", "human_in_loop", "hosting_location",
               "monitoring", "last_validation_date", "incidents_12m", "client_risk_tier"}
YES = {"yes", "y", "true", "1", "regular", "continuous", "approved", "in place", "documented", "implemented",
       "quarterly", "monthly", "done", "full"}
PARTIAL = {"partial", "draft", "adhoc", "ad hoc", "ad-hoc", "in progress", "planned", "some", "annual", "annually",
           "informal", "basic", "pilot", "periodic"}
NO = {"no", "n", "false", "0", "none", "never", "not in place", "absent"}


def norm_key(h: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_")
    k = k.removeprefix("ctl_")
    return ALIASES.get(k, k)


def norm_control(v: Any) -> str:
    """Control answer -> yes | partial | no | unknown."""
    if v is None:
        return "unknown"
    if isinstance(v, bool):
        return "yes" if v else "no"
    s = str(v).strip().lower()
    if s in YES:
        return "yes"
    if s in PARTIAL:
        return "partial"
    if s in NO:
        return "no"
    return "unknown"


def parse_bool(v: Any, field: str, uc_id: str, errors: list[str]) -> bool | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in YES | {"yes"}:
        return True
    if s in NO:
        return False
    errors.append(f"{uc_id}: '{field}' must be yes/no, got '{v}'")
    return None


def parse_date(v: Any, field: str, uc_id: str, errors: list[str]) -> date | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    errors.append(f"{uc_id}: '{field}' is not a date I can read: '{v}' (use YYYY-MM-DD)")
    return None


def parse_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [p.strip() for p in re.split(r"[;|]", str(v)) if p.strip()]


def parse_users(v: Any) -> list[str]:
    out = []
    for item in parse_list(v if not isinstance(v, str) else v.replace(",", ";")):
        s = item.lower()
        out.append("customers" if s.startswith("customer") else "staff" if s.startswith(("staff", "employee", "internal")) else s)
    return sorted(set(out))


def build_use_case(raw: dict[str, Any], errors: list[str]) -> UseCase | None:
    data: dict[str, Any] = {}
    controls: dict[str, str] = {}
    for k, v in raw.items():
        key = norm_key(str(k))
        if key in BASE_FIELDS:
            data[key] = v
        elif key == "controls" and isinstance(v, dict):
            controls.update({norm_key(ck): norm_control(cv) for ck, cv in v.items()})
        elif v is not None and str(v).strip() != "":
            controls[key] = norm_control(v)          # any other column is treated as an optional control answer
    uc_id = str(data.get("id", "")).strip()
    if not uc_id or not str(data.get("name", "")).strip():
        errors.append(f"Row without id or name: {raw}")
        return None
    impact = str(data.get("decision_impact", "") or "").strip().lower()
    if impact and not impact.startswith(("auto", "advis")):
        errors.append(f"{uc_id}: 'decision_impact' must be advisory or automated, got '{impact}'")
        impact = ""
    mon = str(data.get("monitoring", "") or "").strip().lower()
    mon_map = ("continuous" if mon in {"continuous", "real-time", "realtime", "automated", "live"} else
               "none" if mon in {"no", "none", "n", "false", ""} else "basic")
    inc = data.get("incidents_12m")
    try:
        incidents = None if inc is None or str(inc).strip() == "" else int(inc)
    except ValueError:
        errors.append(f"{uc_id}: 'incidents_12m' must be a whole number, got '{inc}'")
        incidents = None
    tier = str(data.get("client_risk_tier", "") or "").strip().title()
    return UseCase(
        id=uc_id, name=str(data["name"]).strip(), business_owner=str(data.get("business_owner", "") or "").strip(),
        purpose=str(data.get("purpose", "") or "").strip(), model_vendor=str(data.get("model_vendor", "") or "").strip(),
        data_categories=parse_list(data.get("data_categories")),
        personal_data=parse_bool(data.get("personal_data"), "personal_data", uc_id, errors),
        sensitive_data=parse_bool(data.get("sensitive_data"), "sensitive_data", uc_id, errors),
        users_affected=parse_users(data.get("users_affected")),
        decision_impact="automated" if impact.startswith("auto") else "advisory" if impact else "",
        human_in_loop=parse_bool(data.get("human_in_loop"), "human_in_loop", uc_id, errors),
        hosting_location=str(data.get("hosting_location", "") or "").strip(),
        monitoring=mon_map if str(data.get("monitoring", "") or "").strip() else "",
        last_validation_date=parse_date(data.get("last_validation_date"), "last_validation_date", uc_id, errors),
        incidents_12m=incidents, client_risk_tier=tier if tier in {"Low", "Medium", "High"} else "",
        controls=controls)


def _org(d: dict[str, Any] | None, controls: dict[str, Any] | None, errors: list[str]) -> Organisation:
    d = d or {}
    as_of = parse_date(d.get("as_of") or d.get("assessment_date"), "as_of", "organisation", errors)
    return Organisation(name=str(d.get("name", "Client (Demo)")), sector=str(d.get("sector", "")),
                        size=str(d.get("size", "")), as_of=as_of, prepared_by=str(d.get("prepared_by", "")),
                        controls={norm_key(k): norm_control(v) for k, v in (controls or {}).items()})


def load_register(path: Path, org_path: Path | None = None) -> Register:
    errors: list[str] = []
    path = Path(path)
    if not path.exists():
        raise RegisterError(f"Input file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict) or "use_cases" not in doc:
            raise RegisterError("YAML must contain a top-level 'use_cases' list")
        rows, org_doc, ctl = doc["use_cases"], doc.get("organisation"), doc.get("org_controls")
    elif suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        org_doc = ctl = None
    else:
        raise RegisterError(f"Unsupported input type '{suffix}': use .yaml, .yml or .csv")
    if org_path:
        od = yaml.safe_load(Path(org_path).read_text(encoding="utf-8")) or {}
        org_doc, ctl = od.get("organisation", org_doc), od.get("org_controls", ctl)
    ucs = [u for u in (build_use_case(r, errors) for r in rows) if u]
    seen: set[str] = set()
    for u in ucs:
        if u.id in seen:
            errors.append(f"Duplicate use case id '{u.id}'")
        seen.add(u.id)
    org = _org(org_doc, ctl, errors)
    if not ucs and not errors:
        errors.append("The register has no use cases")
    if errors:
        raise RegisterError("Problems in the register:\n  - " + "\n  - ".join(errors))
    return Register(org, ucs, str(path))
