"""
Vertical template registry.

Templates are declarative Python modules — no YAML or external config files.
Each template exports a `VerticalTemplate` instance and registers itself
via `register_template()`.  Adding a new vertical = creating a new module
and adding one line to `_BUILTIN_TEMPLATES`.
"""
from __future__ import annotations

from typing import Dict

import pandas as pd

from finance_mcp.dx.models import (
    ArchetypeSpec,
    EntitySpec,
    ValidationGates,
    VerticalTemplate,
)


# ---------------------------------------------------------------------------
# Reference template: insurance B2C distribution (e-TeleQuote shape)
# ---------------------------------------------------------------------------


def _insurance_b2c_outcome(joined: pd.DataFrame) -> pd.Series:
    """
    Per-lead net contribution.

        outcome_per_lead = commission * (1 - chargeback_flag) - cost_usd

    Non-converted leads contribute -cost_usd (commission/chargeback NaN -> 0).
    """
    commission = joined.get("commission")
    chargeback = joined.get("chargeback_flag")
    cost = joined.get("cost_usd")

    if commission is None or cost is None:
        raise ValueError(
            "insurance_b2c outcome requires columns: commission, cost_usd "
            "(and optionally chargeback_flag). Missing one or more."
        )

    commission = commission.fillna(0.0)
    cost = cost.fillna(0.0)
    chargeback = (
        chargeback.fillna(0).astype(float)
        if chargeback is not None
        else pd.Series(0.0, index=joined.index)
    )

    return commission * (1.0 - chargeback) - cost


INSURANCE_B2C = VerticalTemplate(
    id="insurance_b2c",
    version="1.0.0",
    description=(
        "B2C insurance lead-generation + phone sales. Leads purchased from "
        "sources, routed to agents, convert (or not) into policies. Chargebacks "
        "reclaim commissions. Reference case: e-TeleQuote Medicare Advantage."
    ),
    entities=(
        EntitySpec(
            name="lead",
            filename_patterns=("lead", "leads"),
            primary_key="lead_id",
            expected_columns=("lead_id", "source", "state", "cost_usd", "received_ts"),
        ),
        EntitySpec(
            name="policy",
            filename_patterns=("polic",),  # matches policy / policies
            primary_key="policy_id",
            expected_columns=(
                "policy_id",
                "lead_id",
                "issued",
                "premium_annual",
                "commission",
                "chargeback_flag",
            ),
        ),
        EntitySpec(
            name="agent",
            filename_patterns=("agent",),
            primary_key="agent_id",
            expected_columns=("agent_id", "team", "tenure_months"),
        ),
    ),
    join_keys=(
        # child_entity, parent_entity, key_column
        ("policy", "lead", "lead_id"),
    ),
    timestamp_column="received_ts",
    compute_outcome=_insurance_b2c_outcome,
    archetypes=(
        ArchetypeSpec(
            archetype="allocation",
            decision_columns=("source", "state"),
            description="How much lead spend to allocate to each source × state.",
        ),
        ArchetypeSpec(
            archetype="routing",
            decision_columns=("agent_id",),
            description="Which agent to route each lead to.",
        ),
        ArchetypeSpec(
            archetype="selection",
            decision_columns=("source",),
            description="Which lead sources to continue vs. discontinue.",
        ),
    ),
    validation_gates=ValidationGates(
        min_rows_per_segment=30,
        min_months_coverage=12,
        max_missing_pct_in_outcome=0.05,
    ),
)


# ---------------------------------------------------------------------------
# Reference template: SaaS pricing
# ---------------------------------------------------------------------------


def _saas_pricing_outcome(joined: pd.DataFrame) -> pd.Series:
    """
    Per-deal net contribution.

        outcome_per_deal = total_revenue_usd - acquisition_cost_usd

    total_revenue_usd is pre-aggregated from billing in the demo generator
    (real users pre-aggregate in their ETL before handing off). The decision
    is made at the deal level; LTV is the observed outcome.
    """
    rev = joined.get("total_revenue_usd")
    cost = joined.get("acquisition_cost_usd")
    if rev is None or cost is None:
        raise ValueError(
            "saas_pricing outcome requires columns: total_revenue_usd, "
            "acquisition_cost_usd. Missing one or both."
        )
    return rev.fillna(0.0) - cost.fillna(0.0)


SAAS_PRICING = VerticalTemplate(
    id="saas_pricing",
    version="1.0.0",
    description=(
        "B2B SaaS — deals closed at various discount levels against customers "
        "of various sizes. Outcome = deal LTV (pre-aggregated) minus CAC. "
        "Classic cross-section failure: high discounts to small customers "
        "that churn fast destroy unit economics invisible at source-of-deal "
        "aggregation."
    ),
    entities=(
        EntitySpec(
            name="deal",
            filename_patterns=("deal", "deals"),
            primary_key="deal_id",
            expected_columns=(
                "deal_id",
                "customer_id",
                "closed_ts",
                "discount_pct",
                "discount_bucket",
                "acquisition_cost_usd",
                "plan_tier",
                "total_revenue_usd",
            ),
        ),
        EntitySpec(
            name="customer",
            filename_patterns=("customer",),
            primary_key="customer_id",
            expected_columns=(
                "customer_id",
                "employee_count",
                "employee_bucket",
                "industry",
                "region",
            ),
        ),
    ),
    join_keys=(
        # child_entity, parent_entity, key_column
        ("customer", "deal", "customer_id"),
    ),
    timestamp_column="closed_ts",
    compute_outcome=_saas_pricing_outcome,
    archetypes=(
        ArchetypeSpec(
            archetype="pricing",
            decision_columns=("discount_bucket", "employee_bucket"),
            description=(
                "Discount level × customer size — the classic SaaS pricing "
                "blind spot."
            ),
        ),
        ArchetypeSpec(
            archetype="selection",
            decision_columns=("plan_tier", "employee_bucket"),
            description="Which plan tiers to offer which customer sizes.",
        ),
    ),
    validation_gates=ValidationGates(
        min_rows_per_segment=30,
        min_months_coverage=12,
        max_missing_pct_in_outcome=0.05,
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Reference template: Consumer lending (Lending-Club-style)
# ---------------------------------------------------------------------------


def _lending_b2c_outcome(joined: pd.DataFrame) -> pd.Series:
    """
    Per-loan realized lender outcome.

        outcome = total_pymnt + recoveries - funded_amnt

    A positive number means the loan's cashflow (interest + principal + any
    recovery post-default) exceeded the originating principal. Negative means
    the loan lost the lender money.

    Columns come from Lending Club servicing data; real users map their own
    columns via an ETL step before handoff.
    """
    pay = joined.get("total_pymnt")
    rec = joined.get("recoveries")
    fund = joined.get("funded_amnt")
    if pay is None or fund is None:
        raise ValueError(
            "lending_b2c outcome requires columns: total_pymnt, funded_amnt "
            "(recoveries optional)."
        )
    return (
        pay.fillna(0.0)
        + (rec.fillna(0.0) if rec is not None else 0.0)
        - fund.fillna(0.0)
    )


LENDING_B2C = VerticalTemplate(
    id="lending_b2c",
    version="1.0.0",
    description=(
        "Consumer unsecured lending — loans are funded at various grades, "
        "terms, and purposes, then paid back (or charged off). Outcome is "
        "the realized lender cashflow minus principal. Reference corpus: "
        "Lending Club 2015-2016 public loan history."
    ),
    entities=(
        EntitySpec(
            name="loan",
            filename_patterns=("loan",),
            primary_key="loan_id",
            expected_columns=(
                "loan_id",
                "issue_d",
                "grade",
                "term",
                "purpose",
                "addr_state",
                "funded_amnt",
            ),
        ),
        EntitySpec(
            name="performance",
            filename_patterns=("perf", "servicing", "repay"),
            primary_key="loan_id",
            expected_columns=(
                "loan_id",
                "loan_status",
                "total_pymnt",
                "recoveries",
            ),
        ),
    ),
    join_keys=(
        # child, parent, key
        ("performance", "loan", "loan_id"),
    ),
    timestamp_column="issue_d",
    compute_outcome=_lending_b2c_outcome,
    archetypes=(
        ArchetypeSpec(
            archetype="pricing",
            decision_columns=("grade", "term"),
            description="How credit grade × term combine to set loan pricing.",
        ),
        ArchetypeSpec(
            archetype="selection",
            decision_columns=("purpose", "grade"),
            description="Which loan purposes to fund at which credit grade.",
        ),
        ArchetypeSpec(
            archetype="allocation",
            decision_columns=("addr_state", "grade"),
            description="State × grade exposure — geographic concentration risk.",
        ),
    ),
    validation_gates=ValidationGates(
        min_rows_per_segment=30,
        min_months_coverage=12,
        max_missing_pct_in_outcome=0.05,
    ),
)


_BUILTIN_TEMPLATES: Dict[str, VerticalTemplate] = {
    INSURANCE_B2C.id: INSURANCE_B2C,
    SAAS_PRICING.id: SAAS_PRICING,
    LENDING_B2C.id: LENDING_B2C,
}


def get_template(template_id: str) -> VerticalTemplate:
    """Look up a built-in template by id. Raises KeyError if unknown."""
    if template_id not in _BUILTIN_TEMPLATES:
        known = ", ".join(sorted(_BUILTIN_TEMPLATES))
        raise KeyError(
            f"Unknown vertical template '{template_id}'. "
            f"Known templates: {known}"
        )
    return _BUILTIN_TEMPLATES[template_id]


def list_templates() -> tuple[str, ...]:
    """Return all known template ids."""
    return tuple(sorted(_BUILTIN_TEMPLATES))


def register_template(template: VerticalTemplate) -> None:
    """Register a custom template. Overwrites an existing id."""
    _BUILTIN_TEMPLATES[template.id] = template


def match_template(filenames: tuple[str, ...]) -> tuple[str, float]:
    """
    Return (template_id, confidence) for the best-matching template.

    Confidence = fraction of entities in the template whose filename pattern
    matches at least one input file.
    """
    best_id = "custom"
    best_confidence = 0.0

    for template_id, template in _BUILTIN_TEMPLATES.items():
        hits = 0
        for entity in template.entities:
            lowered = {fn.lower() for fn in filenames}
            if any(
                any(pat in fn for pat in entity.filename_patterns) for fn in lowered
            ):
                hits += 1
        confidence = hits / len(template.entities) if template.entities else 0.0
        if confidence > best_confidence:
            best_id = template_id
            best_confidence = confidence

    return best_id, best_confidence
