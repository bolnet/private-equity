"""
Canonical chart-of-accounts the normalizer maps TO.

Frozen — no portco's column quirks ever modify this. The schema mirrors
the `lending_b2c` template (Lending-Club shape) so a normalized CSV is
drop-in compatible with the DX pipeline.

Each `CanonicalField` carries:
  - `name`: the canonical column name in the output CSV
  - `dtype`: target dtype family ('numeric', 'string', 'date')
  - `aliases`: known/expected column-name variants — the primary fuzzy lookup
  - `regex_patterns`: secondary regex patterns when no alias hits exactly
  - `required`: if True, missing columns fail validation; if False, NaN-fill
  - `magnitude_class`: 'currency' | 'count' | 'rate' | None — drives the
    anomaly detector's magnitude check

Adding a new field = appending one frozen dataclass to CANONICAL_FIELDS.
No mutation, no global state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DTypeFamily = Literal["numeric", "string", "date"]
MagnitudeClass = Literal["currency", "count", "rate"]


@dataclass(frozen=True)
class CanonicalField:
    """One column in the canonical chart-of-accounts."""

    name: str
    dtype: DTypeFamily
    aliases: tuple[str, ...]
    regex_patterns: tuple[str, ...] = ()
    required: bool = False
    magnitude_class: MagnitudeClass | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# The frozen schema. Mirror order/shape of dx.templates.LENDING_B2C.
# ---------------------------------------------------------------------------

CANONICAL_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField(
        name="loan_id",
        dtype="string",
        aliases=(
            "loan_id",
            "id",
            "loanid",
            "loan_no",
            "loan_number",
            "loannumber",
            "account_id",
            "account_number",
            "contract_id",
        ),
        regex_patterns=(r"^loan[_\s-]*id$", r"^loan[_\s-]*(no|num|number)$"),
        required=True,
        description="Unique loan identifier — primary key.",
    ),
    CanonicalField(
        name="issue_d",
        dtype="date",
        aliases=(
            "issue_d",
            "issue_date",
            "issued",
            "issued_at",
            "origination_date",
            "orig_date",
            "funding_date",
            "funded_date",
            "open_date",
            "start_date",
        ),
        regex_patterns=(r"^(issue|orig(in)?|fund|open|start)[_\s-]*(d|date|dt)$",),
        required=False,
        description="Origination date.",
    ),
    CanonicalField(
        name="grade",
        dtype="string",
        aliases=(
            "grade",
            "credit_grade",
            "risk_grade",
            "rating",
            "credit_rating",
            "tier",
            "credit_tier",
            "risk_tier",
        ),
        regex_patterns=(r"^(credit|risk)?[_\s-]*(grade|rating|tier)$",),
        required=False,
        description="Credit grade / risk tier.",
    ),
    CanonicalField(
        name="term",
        dtype="string",
        aliases=(
            "term",
            "loan_term",
            "term_months",
            "term_in_months",
            "duration",
            "tenor",
            "maturity",
        ),
        regex_patterns=(r"^(loan[_\s-]*)?term([_\s-]*(months|m))?$", r"^tenor$"),
        required=False,
        description="Loan term / tenor.",
    ),
    CanonicalField(
        name="purpose",
        dtype="string",
        aliases=(
            "purpose",
            "loan_purpose",
            "use_of_funds",
            "use_of_proceeds",
            "loan_reason",
            "reason",
            "category",
        ),
        regex_patterns=(r"^(loan[_\s-]*)?(purpose|reason|category)$",),
        required=False,
        description="Loan purpose / use-of-funds bucket.",
    ),
    CanonicalField(
        name="addr_state",
        dtype="string",
        aliases=(
            "addr_state",
            "state",
            "borrower_state",
            "property_state",
            "state_code",
            "us_state",
            "region",
        ),
        regex_patterns=(r"^(addr|borrower|property|us)?[_\s-]*state([_\s-]*code)?$",),
        required=False,
        description="Borrower / property state code.",
    ),
    CanonicalField(
        name="funded_amnt",
        dtype="numeric",
        aliases=(
            "funded_amnt",
            "funded_amount",
            "loan_amount",
            "loan_amt",
            "principal",
            "principal_amount",
            "amount_funded",
            "amount",
            "origination_amount",
            "orig_amount",
            "disbursed_amount",
        ),
        regex_patterns=(
            r"^(funded|loan|orig(in)?|principal|disbursed)[_\s-]*(amnt|amt|amount)$",
            r"^amount[_\s-]*funded$",
        ),
        required=True,
        magnitude_class="currency",
        description="Originated principal — currency.",
    ),
    CanonicalField(
        name="loan_status",
        dtype="string",
        aliases=(
            "loan_status",
            "status",
            "current_status",
            "performance_status",
            "loan_state",
        ),
        regex_patterns=(r"^(loan|current|performance)?[_\s-]*status$",),
        required=False,
        description="Performance status (Fully Paid / Charged Off / etc.).",
    ),
    CanonicalField(
        name="total_pymnt",
        dtype="numeric",
        aliases=(
            "total_pymnt",
            "total_payment",
            "total_payments",
            "total_paid",
            "amount_paid",
            "payments_received",
            "cumulative_payment",
            "lifetime_payment",
        ),
        regex_patterns=(r"^total[_\s-]*(pymnt|payment|paid)s?$",),
        required=True,
        magnitude_class="currency",
        description="Total cashflow received from borrower.",
    ),
    CanonicalField(
        name="recoveries",
        dtype="numeric",
        aliases=(
            "recoveries",
            "recovery",
            "recovery_amount",
            "post_chargeoff_recovery",
            "post_default_recovery",
            "amount_recovered",
        ),
        regex_patterns=(r"^(amount[_\s-]*)?recover(y|ies|ed)([_\s-]*amount)?$",),
        required=False,
        magnitude_class="currency",
        description="Post-default recoveries.",
    ),
)


# Convenience views — pure functions, no state.

def canonical_names() -> tuple[str, ...]:
    """Ordered tuple of canonical field names (CSV column order)."""
    return tuple(f.name for f in CANONICAL_FIELDS)


def required_names() -> tuple[str, ...]:
    """Canonical fields that MUST be mappable, else validation fails."""
    return tuple(f.name for f in CANONICAL_FIELDS if f.required)


def field_by_name(name: str) -> CanonicalField | None:
    """Look up a canonical field by name. Returns None if absent."""
    for f in CANONICAL_FIELDS:
        if f.name == name:
            return f
    return None


def currency_fields() -> tuple[str, ...]:
    """Canonical fields whose magnitude is in currency — used by anomaly check."""
    return tuple(f.name for f in CANONICAL_FIELDS if f.magnitude_class == "currency")
