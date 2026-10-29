"""Pydantic schema for parsed invoices and validation results."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    hsn_sac: str
    quantity: Decimal
    rate: Decimal
    amount: Decimal


class Invoice(BaseModel):
    source_file: str
    invoice_no: str | None = None
    invoice_date: date | None = None
    supplier_name: str | None = None
    supplier_gstin: str | None = None
    buyer_name: str | None = None
    buyer_gstin: str | None = None
    place_of_supply: str | None = None  # two-digit state code
    line_items: list[LineItem] = Field(default_factory=list)
    taxable_value: Decimal | None = None
    cgst: Decimal = Decimal("0")
    sgst: Decimal = Decimal("0")
    igst: Decimal = Decimal("0")
    tax_rate_pct: Decimal | None = None
    grand_total: Decimal | None = None
    # field name -> confidence 0..1 (1 = explicit label match, <1 = inferred)
    confidence: dict[str, float] = Field(default_factory=dict)
    parser: str = "offline-regex"


class ValidationResult(BaseModel):
    invoice_no: str | None
    source_file: str
    errors: list[str] = Field(default_factory=list)  # hard failures -> exception report
    warnings: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return "EXCEPTION"
        return "REVIEW" if self.needs_human_review else "OK"
