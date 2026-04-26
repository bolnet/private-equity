"""
Frozen 100-day plans — the immutable "what we said we'd do at Day 0"
artifact a portco's value-creation plan is anchored to.

A 100-day plan in PE is rarely a structured artifact: it lives in a Word
doc on a partner's laptop, gets verbally re-litigated at every weekly
operator call, and quietly drifts. By freezing the plan into typed,
hash-able initiative records up front, we turn drift detection into a
deterministic diff at any later checkpoint.

Each Initiative carries:
  - id: stable identifier (used in the drift report)
  - title: operator-readable name
  - kpi: the single KPI the initiative is judged against
  - target_value_usd: planned EBITDA contribution (annualized $)
  - target_revenue_share: planned share-of-revenue (0..1) for the kpi,
      used when the initiative is a top-line lever rather than a fixed $.
  - due_day: target completion day (1..100 — Day 60 is the reference
      'drift detection' checkpoint)
  - owner: workstream owner archetype (CEO/CFO/COO/CRO/CIO etc.)
  - category: pricing | cost-out | growth | working-capital | tech | org

Plans are keyed by `plan_id` (e.g. "default_100day") and bound to a
`portco_id` so a fund running multiple portcos can keep them separate.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Initiative:
    """One line item in a 100-day plan."""
    id: str
    title: str
    kpi: str
    target_value_usd: float
    target_revenue_share: float  # 0..1; 0 if the lever is fixed-$
    due_day: int                 # 1..100
    owner: str
    category: str


@dataclass(frozen=True)
class HundredDayPlan:
    """Frozen 100-day plan — name + portco binding + ordered initiatives."""
    plan_id: str
    portco_id: str
    plan_name: str
    initiatives: tuple[Initiative, ...]
    annualized_revenue_baseline_usd: float
    annualized_ebitda_target_usd: float


# ---- The frozen demo plan -------------------------------------------------
#
# Anchored to a real PE-backed roll-up (BowlerCo / Bowlero) so the actuals
# pulled from SEC EDGAR are economically meaningful. Numbers are illustrative
# but order-of-magnitude correct for a $1B-revenue specialty entertainment
# operator. The KPIs are chosen so that each one maps to a real public
# 10-Q line item the parser can pull (revenue, opex, COGS, etc.), making
# drift detection deterministic.
_DEFAULT_BOWLERCO_PLAN = HundredDayPlan(
    plan_id="default_100day",
    portco_id="BowlerCo",
    plan_name="BowlerCo Day-100 Value Creation Plan",
    annualized_revenue_baseline_usd=1_150_000_000.0,
    annualized_ebitda_target_usd=345_000_000.0,
    initiatives=(
        Initiative(
            id="init_01",
            title="Lift center-level food & beverage attach rate",
            kpi="revenue_total_annualized",
            target_value_usd=1_200_000_000.0,  # planned annualized revenue
            target_revenue_share=1.0,
            due_day=90,
            owner="CRO",
            category="growth",
        ),
        Initiative(
            id="init_02",
            title="Renegotiate top-20 vendor contracts (COGS-out)",
            kpi="cost_of_revenue_annualized",
            target_value_usd=370_000_000.0,  # planned annualized COGS ceiling
            target_revenue_share=0.0,
            due_day=60,
            owner="CFO",
            category="cost-out",
        ),
        Initiative(
            id="init_03",
            title="Centralize SG&A across the rolled-up centers",
            kpi="sga_annualized",
            target_value_usd=420_000_000.0,  # planned annualized SG&A ceiling
            target_revenue_share=0.0,
            due_day=75,
            owner="COO",
            category="cost-out",
        ),
        Initiative(
            id="init_04",
            title="Land dynamic-pricing pilot in 50 priority centers",
            kpi="operating_income_annualized",
            target_value_usd=85_000_000.0,  # planned annualized operating income
            target_revenue_share=0.0,
            due_day=80,
            owner="CRO",
            category="pricing",
        ),
        Initiative(
            id="init_05",
            title="Refinance senior secured at lower coupon",
            kpi="interest_expense_annualized",
            target_value_usd=120_000_000.0,  # planned annualized interest expense ceiling
            target_revenue_share=0.0,
            due_day=45,
            owner="CFO",
            category="working-capital",
        ),
        Initiative(
            id="init_06",
            title="Loyalty program + CRM rollout",
            kpi="revenue_growth_yoy",
            target_value_usd=0.0,
            target_revenue_share=0.06,  # planned 6% YoY revenue growth from program
            due_day=100,
            owner="CIO",
            category="tech",
        ),
        Initiative(
            id="init_07",
            title="Hire and onboard the integration PMO",
            kpi="net_income_annualized",
            target_value_usd=55_000_000.0,  # planned annualized net income floor
            target_revenue_share=0.0,
            due_day=30,
            owner="CEO",
            category="org",
        ),
    ),
)


_PLANS: dict[str, HundredDayPlan] = {
    _DEFAULT_BOWLERCO_PLAN.plan_id: _DEFAULT_BOWLERCO_PLAN,
}


def list_plans() -> list[str]:
    """Return all known plan ids."""
    return sorted(_PLANS.keys())


def get_plan(plan_id: str) -> HundredDayPlan | None:
    """Look up a frozen plan by id; None if missing."""
    return _PLANS.get(plan_id)


def rebind_plan(plan: HundredDayPlan, portco_id: str) -> HundredDayPlan:
    """
    Return a new plan bound to the given portco_id (immutable swap).

    Useful when the caller wants to use the default plan template against
    a portco_id that differs from the template's frozen binding — we never
    mutate the original; we hand back a copy.
    """
    return HundredDayPlan(
        plan_id=plan.plan_id,
        portco_id=portco_id,
        plan_name=plan.plan_name,
        initiatives=plan.initiatives,
        annualized_revenue_baseline_usd=plan.annualized_revenue_baseline_usd,
        annualized_ebitda_target_usd=plan.annualized_ebitda_target_usd,
    )
