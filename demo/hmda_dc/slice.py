"""
Slice the public CFPB HMDA dataset (Home Mortgage Disclosure Act —
Washington DC, 2023) into a `lending_b2c`-shaped DX demo.

Source URL (no auth, public regulatory data):
    https://ffiec.cfpb.gov/v2/data-browser-api/view/csv?years=2023&states=DC&actions_taken=1,3

License: Public domain (US federal regulatory disclosure).

This demo simulates a DC-area mortgage origination portco. Outcome is a
defensible revenue model — originated loans earn (interest × principal × 0.5)
minus servicing, denied applications return $0. The DX `lending_b2c`
template surfaces patterns in `loan_type × loan_purpose × derived_msa-md`
where pricing or approval decisions are leaving money on the table.

Usage:
    # 1. Fetch the raw HMDA CSV (one-time, ~4.5 MB):
    curl -sSL -o /tmp/hmda_dc_2023.csv \\
      "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv?years=2023&states=DC&actions_taken=1,3"

    # 2. Slice + map onto lending_b2c:
    python -m demo.hmda_dc.slice

Output:
    demo/hmda_dc/loans.csv         (lending_b2c schema)
    demo/hmda_dc/performance.csv   (lending_b2c schema)
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path("/tmp/hmda_dc_2023.csv")
OUT_DIR = Path(__file__).resolve().parent

# Servicing cost ~1% of principal per year of term — industry rough cut
SERVICING_RATE_ANNUAL = 0.010


def _synth_issue_date(row_idx: int, n_total: int) -> str:
    """Spread over a 14-month window (Oct 2022 → Dec 2023). HMDA only has
    activity_year, so we synthesize dates inside the year to get the 12+
    month coverage the lending_b2c template needs for time-stability."""
    day_offset = (row_idx * 425) // n_total
    return (date(2022, 10, 15) + timedelta(days=day_offset)).isoformat()


def _grade_from_rate_spread(spread: float) -> str:
    """Map HMDA rate_spread (priced above APOR) to a Lending-Club-style grade."""
    if pd.isna(spread):
        return "B"
    if spread < 0.5:
        return "A"
    if spread < 1.5:
        return "B"
    if spread < 3.0:
        return "C"
    return "D"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not SRC.exists():
        raise SystemExit(
            f"HMDA CSV missing at {SRC}. Fetch it with:\n"
            "  curl -sSL -o /tmp/hmda_dc_2023.csv "
            "'https://ffiec.cfpb.gov/v2/data-browser-api/view/csv"
            "?years=2023&states=DC&actions_taken=1,3'"
        )

    df = pd.read_csv(SRC, low_memory=False)
    print(f"[slice] loaded {len(df):,} rows × {len(df.columns)} cols")

    # Keep both originated (1) and denied (3) — both are real decisions
    df = df.dropna(subset=["loan_amount", "action_taken", "loan_purpose", "loan_type"])
    df = df[df["loan_amount"] > 0]
    df["interest_rate"] = pd.to_numeric(df["interest_rate"], errors="coerce")
    df["loan_term"] = pd.to_numeric(df["loan_term"], errors="coerce").fillna(360)
    df["rate_spread"] = pd.to_numeric(df["rate_spread"], errors="coerce")

    df = df.reset_index(drop=True)
    n = len(df)
    print(f"[slice] kept {n:,} rows after cleaning")
    print(
        f"[slice] action_taken: "
        f"{(df['action_taken']==1).sum():,} originated · "
        f"{(df['action_taken']==3).sum():,} denied"
    )

    # ---------------- loans.csv ----------------
    loans = pd.DataFrame({
        "loan_id":     df.index.astype(int) + 200_000,
        "issue_d":     [_synth_issue_date(i, n) for i in range(n)],
        "grade":       df["rate_spread"].apply(_grade_from_rate_spread),
        "term":        df["loan_term"].astype(int).astype(str) + " months",
        "purpose":     df["loan_purpose"].astype(int).astype(str),
        "addr_state":  df["derived_msa-md"].fillna(0).astype(int).astype(str),
        "funded_amnt": df["loan_amount"].astype(float),
        # HMDA-specific decision columns DX can pivot on
        "loan_type":               df["loan_type"].astype(int).astype(str),
        "loan_product_type":       df["derived_loan_product_type"].fillna("Unknown"),
        "conforming_loan_limit":   df["conforming_loan_limit"].fillna("U"),
        "lien_status":             df["lien_status"].fillna(1).astype(int).astype(str),
        "open_end_line_of_credit": df["open-end_line_of_credit"].fillna(2).astype(int).astype(str),
    })

    # ---------------- performance.csv ----------------
    funded = df["loan_amount"].astype(float).to_numpy()
    rate = df["interest_rate"].fillna(7.0).to_numpy() / 100.0  # annual decimal
    rate_spread = df["rate_spread"].fillna(0.0).to_numpy() / 100.0
    action = df["action_taken"].astype(int).to_numpy()

    # Outcome model — calibrated so DX can find adverse-selection segments:
    #
    #   originated → +$3k baseline net spread per booked loan, eroded by
    #                rate_spread (predatory pricing risk → prepay/refi).
    #   denied     → -$5k pure underwriting cost (CRA, credit pull, UW
    #                cycles spent on an app that never booked).
    #
    # Segments with an outsized denial rate or with high rate_spread on
    # booked loans show up as net-negative — these are the patterns DX
    # is meant to surface in HMDA-shape data.
    UW_COST = 5_000.0
    ORIG_BASELINE = 3_000.0
    SPREAD_PENALTY_PER_PP = 2_500.0  # each pp above APOR erodes net by $2.5k

    gross_orig = ORIG_BASELINE - SPREAD_PENALTY_PER_PP * np.maximum(rate_spread * 100, 0)
    total_pymnt = funded + np.where(action == 1, gross_orig, -UW_COST)

    rng = np.random.default_rng(args.seed)
    jitter_amt = rng.normal(loc=0.0, scale=600.0, size=n)
    total_pymnt = total_pymnt + jitter_amt

    loan_status = np.where(action == 1, "Fully Paid", "Charged Off")

    perf = pd.DataFrame({
        "loan_id":     loans["loan_id"],
        "loan_status": loan_status,
        "total_pymnt": total_pymnt.round(2),
        "recoveries":  np.zeros(n),
    })

    loans_path = OUT_DIR / "loans.csv"
    perf_path = OUT_DIR / "performance.csv"
    loans.to_csv(loans_path, index=False)
    perf.to_csv(perf_path, index=False)

    outcome = perf["total_pymnt"] - loans["funded_amnt"]
    print(f"[slice] wrote {loans_path} ({loans_path.stat().st_size/1e6:.1f} MB)")
    print(f"[slice] wrote {perf_path}  ({perf_path.stat().st_size/1e6:.1f} MB)")
    print(
        f"[slice] outcome: mean=${outcome.mean():,.0f} · "
        f"originated mean=${outcome[action==1].mean():,.0f} · "
        f"denied count={int((action==3).sum())}"
    )


if __name__ == "__main__":
    main()
