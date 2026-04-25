"""
Smoke test for the BX regional-lenders corpus demo.

These tests assume ``python -m demo.regional_lenders.slice`` has been run at
least once (CSVs are committed). They do *not* re-run the DX pipeline (slow);
instead they validate the slicer's output shape and check that the demo data
is in the right place to feed BX.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from demo.regional_lenders.slice import REGIONS

DEMO_ROOT = Path(__file__).resolve().parents[1] / "demo" / "regional_lenders"

# Columns required by the lending_b2c DX template.
_LOANS_REQUIRED = {
    "loan_id",
    "issue_d",
    "grade",
    "term",
    "purpose",
    "addr_state",
    "funded_amnt",
}
_PERF_REQUIRED = {"loan_id", "loan_status", "total_pymnt", "recoveries"}


@pytest.mark.parametrize("region", REGIONS, ids=lambda r: r.slug)
def test_region_slice_shape(region) -> None:
    region_dir = DEMO_ROOT / region.slug
    if not (region_dir / "loans.csv").exists():
        pytest.skip(
            f"{region.slug} CSVs not on disk — run `python -m "
            f"demo.regional_lenders.slice` to regenerate."
        )

    loans = pd.read_csv(region_dir / "loans.csv")
    perf = pd.read_csv(region_dir / "performance.csv")

    assert _LOANS_REQUIRED.issubset(loans.columns), (
        f"{region.slug} loans.csv missing required columns "
        f"{_LOANS_REQUIRED - set(loans.columns)}"
    )
    assert _PERF_REQUIRED.issubset(perf.columns), (
        f"{region.slug} performance.csv missing required columns "
        f"{_PERF_REQUIRED - set(perf.columns)}"
    )

    # Every state in this region's loans must belong to the region's roster.
    stray = set(loans["addr_state"].dropna()) - region.states
    assert not stray, f"{region.slug} contains out-of-region states: {stray}"

    # Same number of rows in both CSVs (one performance row per loan).
    assert len(loans) == len(perf), (
        f"{region.slug}: loans/performance row mismatch ({len(loans)} vs {len(perf)})"
    )
    # Demo target: 12k loans per region. Allow ±10% slack for re-slicing.
    assert 10_000 <= len(loans) <= 15_000, f"{region.slug} unexpected size {len(loans)}"


def test_regions_are_disjoint() -> None:
    """No state should appear in more than one region — the partition must be clean."""
    seen: dict[str, str] = {}
    for region in REGIONS:
        for state in region.states:
            if state in seen:
                pytest.fail(
                    f"State {state} appears in both {seen[state]} and {region.slug}"
                )
            seen[state] = region.slug
    # Sanity: should be 50 states + DC = 51.
    assert len(seen) == 51, f"Expected 51 state-codes, got {len(seen)}"


def test_five_regional_folders_match_definition() -> None:
    """Every region defined in REGIONS must correspond to a sub-folder."""
    if not any((DEMO_ROOT / r.slug / "loans.csv").exists() for r in REGIONS):
        pytest.skip("No regional CSVs on disk — slicer hasn't been run.")
    for region in REGIONS:
        d = DEMO_ROOT / region.slug
        assert d.is_dir(), f"missing region folder {d}"
