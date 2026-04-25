"""
Slice the public Lending Club 2007–2018Q4 dataset down to a demo-sized
2015–2016 cut (30k loans by default), split into two CSVs that match the
`lending_b2c` DX template (loans.csv + performance.csv).

Source dataset: codesignal/lending-club-loan-accepted on HuggingFace Hub.
The file is ~1.6GB and lives under HF's cache dir; this slice is ~7MB.

Usage (as a script):
    python -m demo.lending_club.slice --out demo/lending_club \\
        --n 30000 --start 2015-01 --end 2016-12 --seed 42
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

HF_REPO = "codesignal/lending-club-loan-accepted"
HF_FILE = "accepted_2007_to_2018Q4.csv"

# Loan statuses that definitively end the loan — we need mature outcomes
# so total_pymnt and recoveries reflect the full cashflow.
_COMPLETED_STATUSES = {
    "Fully Paid",
    "Charged Off",
    "Default",
    "Does not meet the credit policy. Status:Fully Paid",
    "Does not meet the credit policy. Status:Charged Off",
}

# Columns we actually need — loading just these drops memory by ~8x.
_USECOLS = [
    "id", "issue_d", "grade", "sub_grade", "term", "purpose", "addr_state",
    "home_ownership", "emp_length", "verification_status",
    "funded_amnt", "int_rate", "installment",
    "loan_status", "total_pymnt", "recoveries",
]


def _download_source() -> str:
    """Fetch the raw Lending Club CSV via HuggingFace Hub. Returns local path."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit(
            "huggingface_hub required. Install: pip install huggingface_hub"
        ) from exc

    print(f"[slice] Downloading {HF_REPO}/{HF_FILE} via HuggingFace Hub…")
    return hf_hub_download(HF_REPO, HF_FILE, repo_type="dataset")


def slice_dataset(
    out_dir: Path,
    n: int = 30_000,
    start: str = "2015-01",
    end: str = "2016-12",
    seed: int = 42,
    source_csv: str | None = None,
) -> tuple[Path, Path]:
    """
    Slice the Lending Club CSV into two demo files.

    Returns:
        (loans_path, performance_path)
    """
    src = source_csv or _download_source()
    print(f"[slice] Loading {src}")

    df = pd.read_csv(src, usecols=_USECOLS, dtype={"id": str})
    df = df[df["loan_status"].isin(_COMPLETED_STATUSES)].copy()

    df["_issue_dt"] = pd.to_datetime(df["issue_d"], format="%b-%Y", errors="coerce")
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.offsets.MonthEnd(1)
    df = df[(df["_issue_dt"] >= start_ts) & (df["_issue_dt"] <= end_ts)].copy()

    df["term"] = df["term"].astype(str).str.strip()
    df["int_rate_pct"] = (
        df["int_rate"].astype(str).str.rstrip("%").replace("", None).astype(float)
    )

    if len(df) < n:
        raise ValueError(
            f"Only {len(df)} completed loans in {start}..{end}; need {n}."
        )
    df = df.sample(n=n, random_state=seed).sort_values("_issue_dt").reset_index(drop=True)

    loans = df[[
        "id", "issue_d", "grade", "sub_grade", "term", "purpose", "addr_state",
        "home_ownership", "emp_length", "verification_status",
        "funded_amnt", "int_rate_pct", "installment",
    ]].rename(columns={"id": "loan_id"})

    perf = df[[
        "id", "loan_status", "total_pymnt", "recoveries",
    ]].rename(columns={"id": "loan_id"})

    out_dir.mkdir(parents=True, exist_ok=True)
    loans_path = out_dir / "loans.csv"
    perf_path = out_dir / "performance.csv"
    loans.to_csv(loans_path, index=False)
    perf.to_csv(perf_path, index=False)

    # Quick provenance summary
    outcome = perf["total_pymnt"] + perf["recoveries"] - loans["funded_amnt"]
    print(
        f"[slice] Wrote {len(loans)} rows → {loans_path} + {perf_path}\n"
        f"[slice] Outcome: mean=${outcome.mean():.0f} "
        f"median=${outcome.median():.0f} "
        f"negative_share={(outcome < 0).mean():.1%}"
    )
    return loans_path, perf_path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Slice Lending Club data for DX demo.")
    default_out = Path(__file__).resolve().parent
    p.add_argument("--out", type=Path, default=default_out)
    p.add_argument("--n", type=int, default=30_000)
    p.add_argument("--start", default="2015-01")
    p.add_argument("--end", default="2016-12")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--source-csv",
        default=os.environ.get("LENDING_CLUB_CSV"),
        help="Path to the raw Lending Club CSV (default: download via HF Hub).",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    slice_dataset(
        out_dir=args.out,
        n=args.n,
        start=args.start,
        end=args.end,
        seed=args.seed,
        source_csv=args.source_csv,
    )
