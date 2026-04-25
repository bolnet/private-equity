"""
Synthetic SaaS pricing demo dataset generator.

Produces deals.csv + customers.csv with the seeded pattern:
  discount_bucket="30-50%" × employee_bucket="<50" destroys LTV/CAC.

Small customers given deep discounts churn fast → low total_revenue →
negative net contribution vs. their CAC.

Run: python3 demo/saas_pricing/generate.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


INDUSTRIES = ["SaaS", "Retail", "Healthcare", "FinServices", "Manufacturing", "Media"]
REGIONS = ["NA", "EMEA", "APAC", "LATAM"]
PLAN_TIERS = ["Starter", "Growth", "Business", "Enterprise"]

DISCOUNT_BUCKETS = ("0-10%", "10-20%", "20-30%", "30-50%")
EMPLOYEE_BUCKETS = ("<50", "50-200", "200-500", ">500")


def _discount_bucket(pct: float) -> str:
    if pct < 10:
        return "0-10%"
    if pct < 20:
        return "10-20%"
    if pct < 30:
        return "20-30%"
    return "30-50%"


def _employee_bucket(n: int) -> str:
    if n < 50:
        return "<50"
    if n < 200:
        return "50-200"
    if n < 500:
        return "200-500"
    return ">500"


def _base_ltv_usd(plan: str, emp_bucket: str) -> float:
    """Base expected LTV by plan × size."""
    plan_factor = {"Starter": 6_000, "Growth": 24_000, "Business": 80_000, "Enterprise": 240_000}
    size_factor = {"<50": 0.9, "50-200": 1.1, "200-500": 1.3, ">500": 1.6}
    return plan_factor[plan] * size_factor[emp_bucket]


def _base_cac_usd(plan: str) -> float:
    return {"Starter": 1_500, "Growth": 5_500, "Business": 18_000, "Enterprise": 60_000}[plan]


def generate(
    out_dir: str,
    n_deals: int = 12_000,
    months: int = 36,
    seed: int = 42,
) -> dict:
    """Write deals.csv + customers.csv to out_dir."""
    rng = np.random.default_rng(seed)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    start_date = datetime(2023, 1, 1)
    end_date = start_date + timedelta(days=int(months * 30.44))
    total_seconds = int((end_date - start_date).total_seconds())

    # ----- Customers (one per deal for MVP; real world 1:N is supported too)
    employee_counts = rng.integers(5, 2000, size=n_deals)
    emp_buckets = np.array([_employee_bucket(e) for e in employee_counts])
    industries = rng.choice(INDUSTRIES, size=n_deals)
    regions = rng.choice(REGIONS, size=n_deals, p=[0.55, 0.25, 0.15, 0.05])
    customer_ids = np.arange(1_000_000, 1_000_000 + n_deals)

    customers = pd.DataFrame(
        {
            "customer_id": customer_ids,
            "employee_count": employee_counts,
            "employee_bucket": emp_buckets,
            "industry": industries,
            "region": regions,
        }
    )

    # ----- Deals
    deal_ids = np.arange(500_000, 500_000 + n_deals)
    plans = rng.choice(PLAN_TIERS, size=n_deals, p=[0.25, 0.4, 0.25, 0.10])
    # Discounts biased higher for larger plans
    discount_pct = np.where(
        plans == "Starter", rng.uniform(0, 20, size=n_deals),
        np.where(
            plans == "Growth", rng.uniform(5, 30, size=n_deals),
            np.where(
                plans == "Business", rng.uniform(10, 40, size=n_deals),
                rng.uniform(20, 50, size=n_deals),  # Enterprise
            ),
        ),
    ).round(1)
    disc_buckets = np.array([_discount_bucket(d) for d in discount_pct])

    offsets = rng.integers(0, total_seconds, size=n_deals)
    closed_ts = [start_date + timedelta(seconds=int(s)) for s in offsets]

    # CAC with modest noise
    cac = np.array(
        [_base_cac_usd(p) * rng.normal(1.0, 0.08) for p in plans]
    ).round(2)

    # Expected LTV, then apply churn multipliers
    base_ltv = np.array(
        [_base_ltv_usd(p, e) for p, e in zip(plans, emp_buckets)]
    )

    # ===== SEEDED PATTERN =====
    # Small customers with deep discounts churn fast — LTV collapses.
    bad_mask = (disc_buckets == "30-50%") & (emp_buckets == "<50")
    # Good pattern: large customers with shallow discounts retain well.
    good_mask = (disc_buckets == "0-10%") & (emp_buckets == ">500")

    ltv_multiplier = np.where(
        bad_mask, rng.uniform(0.10, 0.25, size=n_deals),   # fast churn
        np.where(
            good_mask, rng.uniform(1.35, 1.65, size=n_deals),
            rng.uniform(0.75, 1.10, size=n_deals),          # normal
        ),
    )
    total_revenue = (base_ltv * ltv_multiplier).round(2)

    deals = pd.DataFrame(
        {
            "deal_id": deal_ids,
            "customer_id": customer_ids,
            "closed_ts": [t.isoformat() for t in closed_ts],
            "discount_pct": discount_pct,
            "discount_bucket": disc_buckets,
            "acquisition_cost_usd": cac,
            "plan_tier": plans,
            "total_revenue_usd": total_revenue,
        }
    )

    deals.to_csv(out_path / "deals.csv", index=False)
    customers.to_csv(out_path / "customers.csv", index=False)

    return {
        "deals": len(deals),
        "customers": len(customers),
        "months": months,
        "out_dir": str(out_path),
    }


def main():
    p = argparse.ArgumentParser(description="Generate synthetic SaaS pricing demo.")
    p.add_argument("--out", default="demo/saas_pricing")
    p.add_argument("--deals", type=int, default=12_000)
    p.add_argument("--months", type=int, default=36)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    result = generate(
        out_dir=args.out, n_deals=args.deals, months=args.months, seed=args.seed
    )
    print(
        f"Wrote {result['deals']:,} deals and {result['customers']:,} customers "
        f"to {result['out_dir']}"
    )


if __name__ == "__main__":
    main()
