"""
Synthetic e-TeleQuote demo dataset generator.

Produces leads.csv / policies.csv / agents.csv with the known
TX×Affiliate_B / FL×Facebook / NY×Affiliate_B negative-ROI pattern
deliberately embedded. Used as the canonical end-to-end test for the
Decision-Optimization Diagnostic.

Generation is seeded (seed=42), so the output is fully deterministic —
the E2E test asserts specific $ thresholds that depend on this seed.

Run:  python demo/etelequote/generate.py
"""
from __future__ import annotations

import argparse
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


STATES = [
    "CA", "TX", "FL", "NY", "PA", "IL", "OH", "GA", "NC", "MI",
    "NJ", "VA", "WA", "AZ", "MA", "TN", "IN", "MO", "MD", "WI",
    "CO", "MN", "SC", "AL", "LA", "KY", "OR", "OK", "CT", "UT",
    "IA", "NV", "AR", "MS", "KS", "NM", "NE", "WV", "ID", "HI",
    "NH", "ME", "MT", "RI", "DE", "SD", "ND", "AK", "VT", "WY",
]

SOURCES = [
    "Affiliate_A", "Affiliate_B", "Facebook", "Google",
    "Tiktok", "Radio", "Direct_Mail", "Referral",
]

# Known bad cells — these are the e-TeleQuote-pattern losses.
BAD_CELLS = {
    ("TX", "Affiliate_B"),
    ("FL", "Facebook"),
    ("NY", "Affiliate_B"),
}
# A small known good cell for variety.
GOOD_CELLS = {
    ("CA", "Google"),
    ("WA", "Referral"),
}


def _base_conversion_rate(source: str) -> float:
    """Base conversion rate by lead source."""
    return {
        "Affiliate_A": 0.09,
        "Affiliate_B": 0.08,
        "Facebook": 0.07,
        "Google": 0.11,
        "Tiktok": 0.05,
        "Radio": 0.06,
        "Direct_Mail": 0.08,
        "Referral": 0.14,
    }.get(source, 0.08)


def _base_cost_usd(source: str) -> float:
    """Base cost per lead by source."""
    return {
        "Affiliate_A": 42.0,
        "Affiliate_B": 51.0,
        "Facebook": 38.0,
        "Google": 28.0,
        "Tiktok": 22.0,
        "Radio": 18.0,
        "Direct_Mail": 33.0,
        "Referral": 12.0,
    }.get(source, 35.0)


def _base_chargeback_rate(source: str) -> float:
    """Base chargeback rate — some sources ship bad policies that get reclaimed."""
    return {
        "Affiliate_A": 0.08,
        "Affiliate_B": 0.22,  # known ugly
        "Facebook": 0.18,
        "Google": 0.06,
        "Tiktok": 0.10,
        "Radio": 0.09,
        "Direct_Mail": 0.08,
        "Referral": 0.04,
    }.get(source, 0.10)


def generate(
    out_dir: str,
    n_leads: int = 412_000,
    months: int = 36,
    n_agents: int = 220,
    seed: int = 42,
) -> dict:
    """Write leads.csv, policies.csv, agents.csv to out_dir."""
    rng = np.random.default_rng(seed)
    random.seed(seed)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    start_date = datetime(2023, 1, 1)
    end_date = start_date + timedelta(days=int(months * 30.44))
    total_seconds = int((end_date - start_date).total_seconds())

    # ----- Leads -----
    source_weights = np.array([0.14, 0.22, 0.18, 0.14, 0.09, 0.07, 0.08, 0.08])
    source_weights = source_weights / source_weights.sum()
    source_choices = rng.choice(SOURCES, size=n_leads, p=source_weights)

    state_weights = np.array([0.11, 0.09, 0.07, 0.06, 0.05, 0.04] + [0.016] * 44)
    state_weights = state_weights / state_weights.sum()
    state_choices = rng.choice(STATES, size=n_leads, p=state_weights)

    # Timestamps uniformly distributed over the window
    offsets = rng.integers(0, total_seconds, size=n_leads)
    timestamps = [start_date + timedelta(seconds=int(s)) for s in offsets]

    # Cost with source-level noise
    costs = np.array(
        [_base_cost_usd(s) * rng.normal(1.0, 0.05) for s in source_choices]
    ).round(2)

    lead_ids = np.arange(1, n_leads + 1)

    # Agent assignments
    agent_ids = rng.integers(1, n_agents + 1, size=n_leads)

    leads = pd.DataFrame(
        {
            "lead_id": lead_ids,
            "source": source_choices,
            "state": state_choices,
            "agent_id": agent_ids,
            "cost_usd": costs,
            "received_ts": [t.isoformat() for t in timestamps],
        }
    )

    # ----- Policies -----
    # Conversion depends on source × state. Bad cells convert less AND
    # get charged back more.
    is_bad = np.array(
        [(s, st) in BAD_CELLS for s, st in zip(source_choices, state_choices)]
    )
    is_good = np.array(
        [(s, st) in GOOD_CELLS for s, st in zip(source_choices, state_choices)]
    )

    base_conv = np.array([_base_conversion_rate(s) for s in source_choices])
    conv_rate = np.where(
        is_bad, base_conv * 0.35, np.where(is_good, base_conv * 1.35, base_conv)
    )
    issued = rng.random(n_leads) < conv_rate

    base_cb = np.array([_base_chargeback_rate(s) for s in source_choices])
    chargeback_prob = np.where(
        is_bad, np.minimum(base_cb * 2.5, 0.85),
        np.where(is_good, base_cb * 0.5, base_cb),
    )
    chargeback = (rng.random(n_leads) < chargeback_prob).astype(int)

    premium = np.where(
        issued,
        rng.normal(1_650, 220, size=n_leads).clip(600, 4_200),
        np.nan,
    )
    commission = np.where(
        issued,
        premium * rng.uniform(0.18, 0.24, size=n_leads),
        np.nan,
    )

    policy_ids = np.where(issued, np.arange(100_000, 100_000 + n_leads), np.nan)

    policies_mask = issued
    policies = pd.DataFrame(
        {
            "policy_id": policy_ids[policies_mask].astype(int),
            "lead_id": lead_ids[policies_mask],
            "issued": np.ones(policies_mask.sum(), dtype=int),
            "premium_annual": premium[policies_mask].round(2),
            "commission": commission[policies_mask].round(2),
            "chargeback_flag": chargeback[policies_mask],
        }
    )

    # ----- Agents -----
    teams = rng.choice(["Team_Alpha", "Team_Beta", "Team_Gamma", "Team_Delta"], size=n_agents)
    tenure = rng.integers(1, 96, size=n_agents)
    agents = pd.DataFrame(
        {
            "agent_id": np.arange(1, n_agents + 1),
            "team": teams,
            "tenure_months": tenure,
        }
    )

    leads.to_csv(out_path / "leads.csv", index=False)
    policies.to_csv(out_path / "policies.csv", index=False)
    agents.to_csv(out_path / "agents.csv", index=False)

    return {
        "leads": len(leads),
        "policies": len(policies),
        "agents": len(agents),
        "months": months,
        "out_dir": str(out_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic e-TeleQuote demo dataset."
    )
    parser.add_argument(
        "--out", default="demo/etelequote", help="Output directory."
    )
    parser.add_argument("--leads", type=int, default=412_000)
    parser.add_argument("--months", type=int, default=36)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    result = generate(
        out_dir=args.out, n_leads=args.leads, months=args.months, seed=args.seed
    )
    print(
        f"Wrote {result['leads']:,} leads, {result['policies']:,} policies, "
        f"{result['agents']:,} agents to {result['out_dir']}"
    )


if __name__ == "__main__":
    main()
