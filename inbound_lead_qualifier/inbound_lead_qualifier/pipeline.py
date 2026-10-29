"""Lead pipeline: validate -> consent gate -> enrich -> score -> route -> CRM -> draft -> audit.

Nothing is ever *sent*. Reply emails are written as drafts for a human to review.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from .llm import LLMAdapter, get_adapter
from .models import Enquiry

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
_LOCK = threading.Lock()  # the HTTP server is threaded; serialise file writes

CRM_COLUMNS = [
    "lead_id", "received_at", "status", "tier", "score", "score_breakdown", "name", "email", "phone",
    "company", "website_domain", "role", "company_size", "budget_inr", "timeline", "use_case",
    "consent_given", "consent_timestamp", "notice_version", "follow_up_deadline", "marketing_opt_in", "draft_path",
]

# use-case topic -> keywords, used to pick a paragraph from config["reply"]["use_case_lines"]
USE_CASE_TOPICS = [
    ("invoice", ("invoice", "gst", "bill")),
    ("document", ("document", "policy", "sop", "knowledge", "rag", "q&a")),
    ("lead", ("lead",)),
]

TEMPLATE = Template("""DRAFT - NOT SENT - requires human review before sending
To: $email
Subject: Re: your enquiry about $use_case_short

Dear $first_name,

Thank you for your enquiry on behalf of $company. You mentioned: "$use_case".

$use_case_line

$cta

You are receiving this one-to-one reply because you asked us to contact you about this enquiry.
You can withdraw consent at any time by replying "withdraw" and we will delete your enquiry data.

Regards,
$consultant
""")


@dataclass
class Result:
    lead_id: str
    status: str  # QUALIFIED | WINDOW_EXPIRED | PARKED_NO_CONSENT | REJECTED_INVALID
    tier: str | None = None
    score: float | None = None
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    draft_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ---------- helpers ----------

def mask_email(e: str) -> str:
    user, _, dom = e.partition("@")
    return f"{user[:1]}***@{dom}" if dom else "***"


def mask_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p)
    return f"******{digits[-2:]}" if digits else ""


def domain_of(website: str) -> str:
    """Normalise a website field to a bare domain. No network lookup is made."""
    if not website:
        return ""
    host = urlparse(website if "//" in website else f"//{website}").hostname or ""
    return host.lower().removeprefix("www.")


def lead_id_for(payload: dict[str, Any]) -> str:
    raw = f"{str(payload.get('email', '')).lower()}|{payload.get('submitted_at', '')}"
    return "L-" + hashlib.sha256(raw.encode()).hexdigest()[:10]


def _kw_score(text: str, table: dict[str, list[str]]) -> float:
    t = text.lower()
    for score in sorted(table, key=float, reverse=True):  # highest tier wins
        if any(re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", t) for k in table[score]):
            return float(score)
    return 0.0


# ---------- pipeline ----------

class LeadPipeline:
    def __init__(self, cfg: dict[str, Any], base: Path = Path("."), adapter: LLMAdapter | None = None) -> None:
        self.cfg = cfg
        self.out = base / cfg["output_dir"]
        (self.out / "drafts").mkdir(parents=True, exist_ok=True)
        self.crm_path = self.out / "crm_leads.csv"
        self.parked_path = self.out / "parked_leads.csv"
        self.audit_path = self.out / "audit_log.jsonl"
        self.adapter = adapter or get_adapter()

    def now(self) -> datetime:
        ref = self.cfg.get("reference_time")
        return datetime.fromisoformat(ref) if ref else datetime.now(IST)

    # -- audit trail: append-only JSONL, PII masked --
    def audit(self, lead_id: str, event: str, **details: Any) -> None:
        # wall-clock time of the event; ``now()`` (possibly fixed) is only used for window checks
        rec = {"ts": datetime.now(IST).isoformat(timespec="seconds"), "lead_id": lead_id, "event": event, **details}
        with _LOCK, self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def enrich(self, e: Enquiry) -> dict[str, Any]:
        """Derive signals ONLY from submitted fields. No scraping or third-party lookups."""
        email_dom = e.email.split("@")[1]
        site_dom = domain_of(e.website)
        free = email_dom in self.cfg["free_mail_domains"]
        return {"email_domain": email_dom, "website_domain": site_dom, "free_mail": free,
                "domain_match": bool(site_dom) and (email_dom == site_dom or email_dom.endswith("." + site_dom))}

    def score(self, e: Enquiry, enr: dict[str, Any]) -> tuple[float, dict[str, float]]:
        sc = self.cfg["scorecard"]
        w = sc["weights"]
        budget_f = 0.0
        if e.budget_inr:
            budget_f = next((f for floor, f in sc["budget_bands_inr"] if e.budget_inr >= floor), 0.0)
        parts = {
            "budget": w["budget"] * budget_f,
            "timeline": w["timeline"] * sc["timeline"].get(e.timeline, 0.0),
            "role": w["role"] * _kw_score(e.role, sc["role_keywords"]),
            "company_size": w["company_size"] * sc["company_size"].get(e.company_size, 0.0),
            "use_case_fit": w["use_case_fit"] * _kw_score(f"{e.use_case} {e.message}", sc["use_case_keywords"]),
        }
        if enr["domain_match"]:
            parts["modifier_corporate_email"] = sc["modifiers"]["corporate_email_matching_website"]
        if enr["free_mail"]:
            parts["modifier_free_mail"] = sc["modifiers"]["free_mail"]
        parts = {k: round(v, 1) for k, v in parts.items()}
        return max(0.0, min(100.0, round(sum(parts.values()), 1))), parts

    def route(self, score: float) -> str:
        for tier, floor in sorted(self.cfg["routing"].items(), key=lambda kv: -kv[1]):
            if score >= floor:
                return tier
        return "Nurture"

    def draft(self, e: Enquiry, tier: str, lead_id: str) -> Path:
        rc = self.cfg["reply"]
        uc = e.use_case.lower()
        key = next((k for k, words in USE_CASE_TOPICS if any(w in uc for w in words)), "default")
        body = TEMPLATE.substitute(
            email=e.email, first_name=e.name.split()[0], company=e.company, use_case=e.use_case or "your requirement",
            use_case_short=(e.use_case or "AI automation")[:50], use_case_line=rc["use_case_lines"][key],
            cta=rc["cta"][tier], consultant=rc["consultant_name"])
        if self.adapter.available:  # optional polish; must keep the compliance footer
            polished = self.adapter.complete(
                "Polish this email for tone. Do not add facts, prices, promises or links. Keep the first line and "
                "the consent/withdrawal paragraph verbatim.", body)
            if polished and "DRAFT - NOT SENT" in polished and "withdraw" in polished:
                body = polished
        path = self.out / "drafts" / f"{lead_id}.txt"
        path.write_text(body, encoding="utf-8")
        return path

    def _write_csv(self, path: Path, cols: list[str], row: dict[str, Any]) -> None:
        with _LOCK:
            existing: dict[str, dict[str, Any]] = {}
            if path.exists():
                with path.open(newline="", encoding="utf-8") as fh:
                    existing = {r["lead_id"]: r for r in csv.DictReader(fh)}
            existing[row["lead_id"]] = row  # idempotent upsert: re-posting a lead does not duplicate it
            with path.open("w", newline="", encoding="utf-8") as fh:
                wr = csv.DictWriter(fh, fieldnames=cols)
                wr.writeheader()
                wr.writerows(existing.values())

    def process(self, payload: dict[str, Any]) -> Result:
        lead_id = lead_id_for(payload)
        allowed = set(self.cfg["allowed_fields"])
        dropped = sorted(set(payload) - allowed)
        payload = {k: v for k, v in payload.items() if k in allowed}
        self.audit(lead_id, "received", source=payload.get("source"), dropped_fields=dropped)

        try:
            e = Enquiry.model_validate(payload)
        except ValidationError as exc:
            reasons = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors()]
            self.audit(lead_id, "rejected_invalid", reasons=reasons)
            return Result(lead_id, "REJECTED_INVALID", reasons=reasons)

        # Consent gate (DPDP: consent must be given, specific and tied to a notice)
        c = e.consent
        problems = []
        if not c.given:
            problems.append("consent not given")
        if c.given and not c.timestamp:
            problems.append("consent timestamp missing")
        if c.notice_version not in self.cfg["accepted_notice_versions"]:
            problems.append(f"unknown privacy notice version '{c.notice_version}'")
        if c.given and "respond_to_enquiry" not in c.purposes:
            problems.append("purpose 'respond_to_enquiry' not consented")
        if problems:
            # Park WITHOUT personal data: only id, time and reason are kept.
            self._write_csv(self.parked_path, ["lead_id", "received_at", "reason"],
                            {"lead_id": lead_id, "received_at": e.submitted_at.isoformat(), "reason": "; ".join(problems)})
            self.audit(lead_id, "parked_no_consent", reasons=problems)
            return Result(lead_id, "PARKED_NO_CONSENT", reasons=problems)
        self.audit(lead_id, "consent_recorded", consent_timestamp=c.timestamp, notice_version=c.notice_version,
                   purposes=c.purposes, marketing_opt_in=e.marketing_opt_in)

        enr = self.enrich(e)
        score, parts = self.score(e, enr)
        tier = self.route(score)
        self.audit(lead_id, "scored", score=score, tier=tier, breakdown=parts, email=mask_email(e.email),
                   phone=mask_phone(e.phone), enrichment=enr)

        deadline = c.timestamp + timedelta(days=self.cfg["follow_up_window_days"])
        status, draft_path, reasons = "QUALIFIED", None, []
        if self.now() > deadline:
            status = "WINDOW_EXPIRED"
            reasons.append(f"follow-up window closed on {deadline.isoformat()}; fresh consent needed before contact")
            self.audit(lead_id, "follow_up_blocked", deadline=deadline)
        else:
            draft_path = str(self.draft(e, tier, lead_id))
            self.audit(lead_id, "reply_drafted", path=draft_path, sent=False)

        self._write_csv(self.crm_path, CRM_COLUMNS, {
            "lead_id": lead_id, "received_at": e.submitted_at.isoformat(), "status": status, "tier": tier,
            "score": score, "score_breakdown": json.dumps(parts), "name": e.name, "email": e.email, "phone": e.phone,
            "company": e.company, "website_domain": enr["website_domain"], "role": e.role,
            "company_size": e.company_size, "budget_inr": e.budget_inr or "", "timeline": e.timeline,
            "use_case": e.use_case, "consent_given": c.given, "consent_timestamp": c.timestamp.isoformat(),
            "notice_version": c.notice_version, "follow_up_deadline": deadline.isoformat(),
            "marketing_opt_in": e.marketing_opt_in, "draft_path": draft_path or ""})
        log.info("%s %-16s %-8s score=%5.1f", lead_id, status, tier, score)
        return Result(lead_id, status, tier, score, parts, reasons, draft_path)
