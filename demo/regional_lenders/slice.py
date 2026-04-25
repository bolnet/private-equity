"""
Slice the public Lending Club 2007–2018Q4 dataset into N regional "portcos"
by US census region — the input corpus for the BX cross-portco demo.

Each region writes its own sub-folder with two CSVs that match the
``lending_b2c`` DX template (loans.csv + performance.csv).

Source dataset: codesignal/lending-club-loan-accepted on HuggingFace Hub.
The same ~1.6 GB CSV used by ``demo.lending_club.slice`` — re-uses the HF cache.

Usage (as a script):
    python -m demo.regional_lenders.slice \\
        --out demo/regional_lenders \\
        --per-region 12000 --start 2015-01 --end 2016-12 --seed 42
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

HF_REPO = "codesignal/lending-club-loan-accepted"
HF_FILE = "accepted_2007_to_2018Q4.csv"

_COMPLETED_STATUSES = frozenset(
    {
        "Fully Paid",
        "Charged Off",
        "Default",
        "Does not meet the credit policy. Status:Fully Paid",
        "Does not meet the credit policy. Status:Charged Off",
    }
)

_USECOLS = (
    "id",
    "issue_d",
    "grade",
    "sub_grade",
    "term",
    "purpose",
    "addr_state",
    "home_ownership",
    "emp_length",
    "verification_status",
    "funded_amnt",
    "int_rate",
    "installment",
    "loan_status",
    "total_pymnt",
    "recoveries",
)


@dataclass(frozen=True)
class Region:
    """One regional 'portco' — a slug used as folder name + the states it covers."""

    slug: str
    label: str
    states: frozenset[str]


# US census regions, with the West split into Pacific + Mountain so we get 5 buckets.
REGIONS: tuple[Region, ...] = (
    Region(
        slug="northeast_lender",
        label="Northeast Lender",
        states=frozenset({"CT", "ME", "MA", "NH", "NJ", "NY", "PA", "RI", "VT"}),
    ),
    Region(
        slug="midwest_lender",
        label="Midwest Lender",
        states=frozenset(
            {"IL", "IN", "IA", "KS", "MI", "MN", "MO", "NE", "ND", "OH", "SD", "WI"}
        ),
    ),
    Region(
        slug="southeast_lender",
        label="Southeast Lender",
        states=frozenset(
            {
                "AL", "AR", "DE", "DC", "FL", "GA", "KY", "LA", "MD",
                "MS", "NC", "OK", "SC", "TN", "TX", "VA", "WV",
            }
        ),
    ),
    Region(
        slug="pacific_lender",
        label="Pacific Coast Lender",
        states=frozenset({"AK", "CA", "HI", "OR", "WA"}),
    ),
    Region(
        slug="mountain_lender",
        label="Mountain West Lender",
        states=frozenset({"AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY"}),
    ),
)


def _download_source() -> str:
    """Fetch the raw Lending Club CSV via HuggingFace Hub. Returns local path."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit(
            "huggingface_hub required. Install: pip install huggingface_hub"
        ) from exc

    print(f"[regional] Resolving {HF_REPO}/{HF_FILE} via HuggingFace Hub…")
    return hf_hub_download(HF_REPO, HF_FILE, repo_type="dataset")


def _load_source(source_csv: str | None, start: str, end: str) -> pd.DataFrame:
    """Load + filter to mature loans in the [start, end] window. No sampling yet."""
    src = source_csv or _download_source()
    print(f"[regional] Loading {src}")
    df = pd.read_csv(src, usecols=list(_USECOLS), dtype={"id": str})
    df = df[df["loan_status"].isin(_COMPLETED_STATUSES)].copy()

    df["_issue_dt"] = pd.to_datetime(df["issue_d"], format="%b-%Y", errors="coerce")
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.offsets.MonthEnd(1)
    df = df[(df["_issue_dt"] >= start_ts) & (df["_issue_dt"] <= end_ts)].copy()

    df["term"] = df["term"].astype(str).str.strip()
    df["int_rate_pct"] = (
        df["int_rate"].astype(str).str.rstrip("%").replace("", None).astype(float)
    )
    return df


def _write_region(
    df: pd.DataFrame,
    region: Region,
    per_region: int,
    seed: int,
    out_dir: Path,
) -> tuple[Path, Path, int] | None:
    """Sample loans for one region and write loans.csv + performance.csv."""
    sub = df[df["addr_state"].isin(region.states)]
    if len(sub) < per_region:
        print(
            f"[regional] {region.slug}: only {len(sub)} mature loans available, "
            f"need {per_region} — skipping."
        )
        return None

    picked = (
        sub.sample(n=per_region, random_state=seed)
        .sort_values("_issue_dt")
        .reset_index(drop=True)
    )

    loans = picked[
        [
            "id",
            "issue_d",
            "grade",
            "sub_grade",
            "term",
            "purpose",
            "addr_state",
            "home_ownership",
            "emp_length",
            "verification_status",
            "funded_amnt",
            "int_rate_pct",
            "installment",
        ]
    ].rename(columns={"id": "loan_id"})

    perf = picked[
        ["id", "loan_status", "total_pymnt", "recoveries"]
    ].rename(columns={"id": "loan_id"})

    region_dir = out_dir / region.slug
    region_dir.mkdir(parents=True, exist_ok=True)
    loans_path = region_dir / "loans.csv"
    perf_path = region_dir / "performance.csv"
    loans.to_csv(loans_path, index=False)
    perf.to_csv(perf_path, index=False)

    outcome = perf["total_pymnt"] + perf["recoveries"] - loans["funded_amnt"]
    print(
        f"[regional] {region.slug}: {len(loans):>6} loans, "
        f"mean_outcome=${outcome.mean():>7.0f} "
        f"loss_rate={(outcome < 0).mean():.1%} → {region_dir}"
    )
    return loans_path, perf_path, len(loans)


def slice_regional(
    out_dir: Path,
    per_region: int = 12_000,
    start: str = "2015-01",
    end: str = "2016-12",
    seed: int = 42,
    source_csv: str | None = None,
) -> dict[str, tuple[Path, Path]]:
    """Partition Lending Club into one demo folder per region."""
    df = _load_source(source_csv, start, end)
    print(
        f"[regional] {len(df):,} mature loans in {start}..{end} across "
        f"{df['addr_state'].nunique()} states."
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, tuple[Path, Path]] = {}
    # Stagger seeds per-region so each portco is an independent sample but
    # still reproducible.
    for offset, region in enumerate(REGIONS):
        written = _write_region(df, region, per_region, seed + offset, out_dir)
        if written is not None:
            loans_path, perf_path, _ = written
            results[region.slug] = (loans_path, perf_path)
    return results


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Slice Lending Club into 5 regional portcos for the BX demo."
    )
    default_out = Path(__file__).resolve().parent
    p.add_argument("--out", type=Path, default=default_out)
    p.add_argument("--per-region", type=int, default=12_000)
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
    slice_regional(
        out_dir=args.out,
        per_region=args.per_region,
        start=args.start,
        end=args.end,
        seed=args.seed,
        source_csv=args.source_csv,
    )
