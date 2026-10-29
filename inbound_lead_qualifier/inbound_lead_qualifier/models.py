"""Payload schema for the inbound enquiry webhook."""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
IN_MOBILE_RE = re.compile(r"^(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}$")
SIZES = {"1-10", "11-50", "51-200", "201-1000", "1000+"}


class Consent(BaseModel):
    given: bool
    timestamp: datetime | None = None
    notice_version: str | None = None
    purposes: list[str] = Field(default_factory=list)


class Enquiry(BaseModel):
    # Unknown fields are dropped (data minimisation), not stored.
    model_config = ConfigDict(extra="ignore")

    submitted_at: datetime
    name: str = Field(min_length=2, max_length=100)
    email: str
    phone: str = ""
    company: str = Field(min_length=1, max_length=150)
    website: str = ""
    role: str = ""
    company_size: str
    budget_inr: int | None = Field(default=None, ge=0)
    timeline: str = "exploring"
    use_case: str = ""
    message: str = Field(default="", max_length=2000)
    consent: Consent
    marketing_opt_in: bool = False
    source: str = "website-form"

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not EMAIL_RE.match(v.strip()):
            raise ValueError("invalid email address")
        return v.strip().lower()

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if v and not IN_MOBILE_RE.match(v.strip()):
            raise ValueError("phone must be an Indian mobile number (+91 followed by 10 digits starting 6-9)")
        return v.strip()

    @field_validator("company_size")
    @classmethod
    def _size(cls, v: str) -> str:
        if v not in SIZES:
            raise ValueError(f"company_size must be one of {sorted(SIZES)}")
        return v
