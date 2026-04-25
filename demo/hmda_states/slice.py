"""
Slice CFPB HMDA home-purchase mortgage data, per US state, into the
`lending_b2c` DX template (loans.csv + performance.csv).

Source URL pattern (no auth, public regulatory data):
    https://ffiec.cfpb.gov/v2/data-browser-api/view/csv
        ?years=2023
        &states=<XX>
        &actions_taken=1,3        (originated + denied)
        &loan_purposes=1          (home purchase only — keeps file size tractable
                                   and gives apples-to-apples cross-state comparison)

License: Public domain (US federal regulatory disclosure).

Use this to build a 5-portco "fund of regional mortgage origination shops" for
the BX cross-portco benchmark — same vertical (mortgage origination), different
markets, all real CFPB data.

Fetch step (one-time per state):
    curl -sSL -o /tmp/hmda_<XX>_2023_purchase.csv \\
      "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv\\
?years=2023&states=<XX>&actions_taken=1,3&loan_purposes=1"

Then:
    python -m demo.hmda_states.slice --state DC
    python -m demo.hmda_states.slice --state DE
    python -m demo.hmda_states.slice --state MA
    python -m demo.hmda_states.slice --state AZ
    python -m demo.hmda_states.slice --state GA

Output (per state):
    demo/hmda_states/<state>/loans.csv
    demo/hmda_states/<state>/performance.csv
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

OUT_ROOT = Path(__file__).resolve().parent

UW_COST = 5_000.0
ORIG_BASELINE = 3_000.0
SPREAD_PENALTY_PER_PP = 2_500.0


def _synth_issue_date(row_idx: int, n_total: int) -> str:
    """Spread over a 14-month window so the months_coverage gate passes."""
    day_offset = (row_idx * 425) // n_total
    return (date(2022, 10, 15) + timedelta(days=day_offset)).isoformat()


def _grade_from_rate_spread(spread: float) -> str:
    if pd.isna(spread):
        return "B"
    if spread < 0.5:
        return "A"
    if spread < 1.5:
        return "B"
    if spread < 3.0:
        return "C"
    return "D"


def slice_state(state: str, src_path: Path, seed: int = 42) -> tuple[Path, Path]:
    """Slice one state's HMDA CSV onto the lending_b2c template. Returns the
    pair of output paths (loans.csv, performance.csv)."""
    if not src_path.exists():
        raise SystemExit(
            f"Source CSV missing at {src_path}. Fetch it with:\n"
            f"  curl -sSL -o {src_path} "
            "'https://ffiec.cfpb.gov/v2/data-browser-api/view/csv"
            f"?years=2023&states={state}&actions_taken=1,3&loan_purposes=1'"
        )

    df = pd.read_csv(src_path, low_memory=False)
    print(f"[slice] {state}: loaded {len(df):,} rows × {len(df.columns)} cols")

    df = df.dropna(subset=["loan_amount", "action_taken", "loan_purpose", "loan_type"])
    df = df[df["loan_amount"] > 0]
    df["interest_rate"] = pd.to_numeric(df["interest_rate"], errors="coerce")
    df["loan_term"] = pd.to_numeric(df["loan_term"], errors="coerce").fillna(360)
    df["rate_spread"] = pd.to_numeric(df["rate_spread"], errors="coerce")
    df = df.reset_index(drop=True)
    n = len(df)
    print(
        f"[slice] {state}: kept {n:,} rows ({(df['action_taken']==1).sum():,} "
        f"originated, {(df['action_taken']==3).sum():,} denied)"
    )

    state_dir = OUT_ROOT / state.lower()
    state_dir.mkdir(parents=True, exist_ok=True)

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
        "loan_type":             df["loan_type"].astype(int).astype(str),
        "loan_product_type":     df["derived_loan_product_type"].fillna("Unknown"),
        "conforming_loan_limit": df["conforming_loan_limit"].fillna("U"),
        "lien_status":           df["lien_status"].fillna(1).astype(int).astype(str),
    })

    # ---------------- performance.csv ----------------
    funded = df["loan_amount"].astype(float).to_numpy()
    rate_spread = df["rate_spread"].fillna(0.0).to_numpy() / 100.0
    action = df["action_taken"].astype(int).to_numpy()

    gross_orig = ORIG_BASELINE - SPREAD_PENALTY_PER_PP * np.maximum(rate_spread * 100, 0)
    total_pymnt = funded + np.where(action == 1, gross_orig, -UW_COST)

    rng = np.random.default_rng(seed)
    jitter = rng.normal(loc=0.0, scale=600.0, size=n)
    total_pymnt = total_pymnt + jitter

    loan_status = np.where(action == 1, "Fully Paid", "Charged Off")

    perf = pd.DataFrame({
        "loan_id":     loans["loan_id"],
        "loan_status": loan_status,
        "total_pymnt": total_pymnt.round(2),
        "recoveries":  np.zeros(n),
    })

    loans_path = state_dir / "loans.csv"
    perf_path = state_dir / "performance.csv"
    loans.to_csv(loans_path, index=False)
    perf.to_csv(perf_path, index=False)

    outcome = perf["total_pymnt"] - loans["funded_amnt"]
    print(
        f"[slice] {state}: wrote {loans_path.name} + {perf_path.name} · "
        f"outcome mean=${outcome.mean():,.0f}"
    )
    return loans_path, perf_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        required=True,
        help="Two-letter state code (e.g. DC, MA). Must match a fetched CSV at "
             "/tmp/hmda_<STATE>_2023_purchase.csv.",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    state = args.state.upper()
    src = Path(f"/tmp/hmda_{state}_2023_purchase.csv")
    slice_state(state, src, seed=args.seed)


if __name__ == "__main__":
    main()
