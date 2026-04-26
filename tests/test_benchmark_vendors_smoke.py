"""Smoke + offline tests for benchmark_vendors.

The offline test is hermetic — uses a synthetic record fixture and seeds
the cache directly so no USAspending call is made. The integration test
is gated on `RUN_USASPENDING_LIVE=1` because it requires network egress
and would be flaky in CI.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from finance_mcp.procurement import benchmark_vendors
from finance_mcp.procurement.benchmark import (
    _agency_opportunities,
    _build_price_matrix,
    _records_to_frame,
    _scaled,
    _to_roman,
    _vendor_spreads,
)
from finance_mcp.procurement.fetcher import _cache_key

_REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Synthetic fixture — 3 agencies, 3 vendors, mix of prices
# ---------------------------------------------------------------------------

FIXTURE_RECORDS: list[dict] = [
    # VendorOne — A pays 100K (×2), B pays 50K, C pays 75K
    {"Award ID": "a1a", "Awarding Agency": "AGENCY_A", "Awarding Sub Agency": "",
     "Recipient Name": "VendorOne", "Award Amount": 100_000, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r1"},
    {"Award ID": "a1b", "Awarding Agency": "AGENCY_A", "Awarding Sub Agency": "",
     "Recipient Name": "VendorOne", "Award Amount": 100_000, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r1"},
    {"Award ID": "a2", "Awarding Agency": "AGENCY_B", "Awarding Sub Agency": "",
     "Recipient Name": "VendorOne", "Award Amount": 50_000, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r1"},
    {"Award ID": "a3", "Awarding Agency": "AGENCY_C", "Awarding Sub Agency": "",
     "Recipient Name": "VendorOne", "Award Amount": 75_000, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r1"},
    # VendorTwo — A pays 80K, C pays 40K
    {"Award ID": "a4", "Awarding Agency": "AGENCY_A", "Awarding Sub Agency": "",
     "Recipient Name": "VendorTwo", "Award Amount": 80_000, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r2"},
    {"Award ID": "a5", "Awarding Agency": "AGENCY_C", "Awarding Sub Agency": "",
     "Recipient Name": "VendorTwo", "Award Amount": 40_000, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r2"},
    # VendorThree — only one buyer; should be excluded from cohort spread
    {"Award ID": "a6", "Awarding Agency": "AGENCY_A", "Awarding Sub Agency": "",
     "Recipient Name": "VendorThree", "Award Amount": 999_000, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r3"},
    # Drop row: missing agency
    {"Award ID": "a7", "Awarding Agency": "", "Awarding Sub Agency": "",
     "Recipient Name": "VendorOne", "Award Amount": 60_000, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r1"},
    # Drop row: zero amount
    {"Award ID": "a8", "Awarding Agency": "AGENCY_B", "Awarding Sub Agency": "",
     "Recipient Name": "VendorOne", "Award Amount": 0, "Description": "x",
     "Start Date": "2024-01-01", "End Date": "2024-12-31",
     "NAICS": "541512", "PSC": "D310", "recipient_id": "r1"},
]


@pytest.mark.unit
def test_records_to_frame_drops_invalid_rows() -> None:
    df = _records_to_frame(FIXTURE_RECORDS)
    assert len(df) == 7  # 9 input - 1 missing agency - 1 zero-amount


@pytest.mark.unit
def test_price_matrix_cells() -> None:
    df = _records_to_frame(FIXTURE_RECORDS)
    matrix = _build_price_matrix(df)
    assert matrix.loc["VendorOne", "AGENCY_A"] == 100_000
    assert matrix.loc["VendorOne", "AGENCY_B"] == 50_000
    assert matrix.loc["VendorTwo", "AGENCY_C"] == 40_000


@pytest.mark.unit
def test_vendor_spreads_filters_single_buyer_vendors() -> None:
    df = _records_to_frame(FIXTURE_RECORDS)
    spreads = _vendor_spreads(df)
    vendor_names = {s.vendor for s in spreads}
    assert "VendorOne" in vendor_names
    assert "VendorTwo" in vendor_names
    assert "VendorThree" not in vendor_names  # single buyer — excluded


@pytest.mark.unit
def test_agency_opportunities_math() -> None:
    df = _records_to_frame(FIXTURE_RECORDS)
    opps = _agency_opportunities(df)
    by_agency = {o.agency: o for o in opps}

    # AGENCY_A: VendorOne 2 awards × ($100K - $50K) = $100K
    #         + VendorTwo 1 award  × ($80K  - $40K) = $40K  → $140K
    assert by_agency["AGENCY_A"].savings_if_matched_best_usd == 140_000

    # AGENCY_C: VendorOne 1 × ($75K - $50K) = $25K; VendorTwo at the floor
    assert by_agency["AGENCY_C"].savings_if_matched_best_usd == 25_000

    # AGENCY_B is the cheapest VendorOne buyer; doesn't buy VendorTwo → $0
    assert by_agency["AGENCY_B"].savings_if_matched_best_usd == 0


@pytest.mark.unit
def test_scaled_helper() -> None:
    assert _scaled(500) == "$500"
    assert _scaled(1_700) == "$2K"
    assert _scaled(1_234_567) == "$1.2M"
    assert _scaled(2_500_000_000) == "$2.50B"


@pytest.mark.unit
def test_to_roman() -> None:
    assert _to_roman(4) == "IV"
    assert _to_roman(9) == "IX"
    assert _to_roman(0) == "I"


@pytest.mark.unit
def test_benchmark_vendors_offline_via_seeded_cache(tmp_path: Path) -> None:
    """End-to-end run with a pre-seeded cache file — no network needed."""
    psc = "D310"
    fy = 2024
    max_records = 2000
    key = _cache_key(psc, fy, max_records)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / f"usaspending_{psc}_{fy}_{key}.json"
    cache_file.write_text(
        json.dumps(
            {
                "psc_code": psc,
                "fiscal_year": fy,
                "max_records": max_records,
                "n_records": len(FIXTURE_RECORDS),
                "records": FIXTURE_RECORDS,
            }
        ),
        encoding="utf-8",
    )

    result = benchmark_vendors(
        psc_code=psc,
        fiscal_year=fy,
        max_records=max_records,
        cache_dir=str(cache_dir),
        output_filename="benchmark_offline_smoke",
    )

    assert result["n_contracts"] == 7
    assert result["n_agencies"] == 3
    assert result["n_vendors"] == 3
    assert result["total_savings_opportunity_usd"] == 165_000  # 140K + 25K + 0

    # AGENCY_A is the biggest opportunity by construction.
    biggest = result["top_savings_per_agency"][0]
    assert biggest["agency"] == "AGENCY_A"
    assert biggest["savings_if_matched_best_usd"] == 140_000

    report = Path(result["report_path"])
    assert report.exists()
    html = report.read_text(encoding="utf-8")
    assert "Top savings opportunities by buyer" in html
    assert "AGENCY_A" in html
    assert "VendorOne" in html

    sidecar = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    assert sidecar["n_agencies"] == 3
    assert sidecar["psc_code"] == psc
    assert sidecar["vendor_agency_price_matrix"]["VendorOne"]["AGENCY_A"] == 100_000


# ---------------------------------------------------------------------------
# Live integration test — opt-in only (requires network)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_USASPENDING_LIVE") != "1",
    reason="Set RUN_USASPENDING_LIVE=1 to hit the public USAspending API.",
)
def test_benchmark_vendors_live_default_args(tmp_path: Path) -> None:
    """Live smoke test against USAspending — D310 / FY2024 / 2000 records."""
    result = benchmark_vendors(
        psc_code="D310",
        fiscal_year=2024,
        max_records=2000,
        cache_dir=str(tmp_path / "usaspending_cache"),
        output_filename="benchmark_live_smoke",
    )

    print("\n=== benchmark_vendors live smoke ===")
    print(f"n_contracts:                    {result['n_contracts']}")
    print(f"n_agencies:                     {result['n_agencies']}")
    print(f"n_vendors:                      {result['n_vendors']}")
    print(f"total_savings_opportunity_usd:  ${result['total_savings_opportunity_usd']:,.2f}")
    if result["top_savings_per_agency"]:
        biggest = result["top_savings_per_agency"][0]
        print("Biggest single opportunity:")
        for k, v in biggest.items():
            print(f"  {k}: {v}")

    assert result["n_agencies"] >= 5
    assert result["n_vendors"] >= 5
    assert result["total_savings_opportunity_usd"] > 0
    assert Path(result["report_path"]).exists()
    assert Path(result["json_path"]).exists()
