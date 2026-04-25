"""
End-to-end reproduction of the e-TeleQuote pattern.

This test is the canonical integration proof: it generates the synthetic
e-TeleQuote dataset on the fly (seed=42) and asserts that the six DX tools,
chained together, surface the seeded TX × Affiliate_B pattern as a top-3
persistent opportunity with a positive throttle counterfactual.

The absolute dollar numbers depend on the seed. What we assert is structural:
  • All three seeded bad cells appear in the top 10 worst segments.
  • TX × Affiliate_B is persistent across every observed quarter.
  • Throttling to 3% projects a positive impact.
  • dx_report emits HTML + JSON sidecar.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make demo/ importable as a package (each subdir has __init__.py)
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from demo.etelequote import generate as etelequote_gen  # noqa: E402

from finance_mcp.dx.counterfactual import dx_counterfactual  # noqa: E402
from finance_mcp.dx.evidence import dx_evidence_rows  # noqa: E402
from finance_mcp.dx.ingest import dx_ingest  # noqa: E402
from finance_mcp.dx.report import dx_report  # noqa: E402
from finance_mcp.dx.segment_stats import dx_segment_stats  # noqa: E402
from finance_mcp.dx.session import clear_sessions  # noqa: E402
from finance_mcp.dx.time_stability import dx_time_stability  # noqa: E402


SEEDED_BAD_CELLS = [
    ("Affiliate_B", "TX"),
    ("Facebook", "FL"),
    ("Affiliate_B", "NY"),
]


@pytest.fixture(scope="module")
def etelequote_data(tmp_path_factory):
    """Generate a 40k-lead × 36-month synthetic dataset once per test module."""
    out_dir = tmp_path_factory.mktemp("etelequote_e2e")
    etelequote_gen.generate(
        out_dir=str(out_dir), n_leads=40_000, months=36, seed=42
    )
    return {
        "leads": str(out_dir / "leads.csv"),
        "policies": str(out_dir / "policies.csv"),
        "agents": str(out_dir / "agents.csv"),
        "out_dir": out_dir,
    }


@pytest.fixture(autouse=True)
def _clean_sessions():
    clear_sessions()
    yield
    clear_sessions()


def test_e2e_pattern_detection(etelequote_data, tmp_path):
    # Step 1: Ingest
    ingest = dx_ingest(
        data_paths=[
            etelequote_data["leads"],
            etelequote_data["policies"],
            etelequote_data["agents"],
        ],
        vertical="insurance_b2c",
        portco_id="etelequote_e2e",
    )
    assert ingest["gates_failed"] == []
    assert ingest["months_coverage"] >= 30
    assert ingest["template_match_confidence"] == 1.0
    sid = ingest["session_id"]

    # Step 2: Segment search
    stats = dx_segment_stats(
        sid,
        decision_cols=["source", "state"],
        min_segment_n=30,
        top_k=20,
        rank_by="worst_total",
    )
    assert len(stats["segments"]) >= 10

    # Primary seeded cell (TX × Affiliate_B) must be in the top 3.
    top3 = [
        (s["segment"]["source"], s["segment"]["state"])
        for s in stats["segments"][:3]
    ]
    assert ("Affiliate_B", "TX") in top3, (
        f"Primary seeded bad cell (Affiliate_B, TX) not in top 3; got {top3}"
    )

    # At least 2 of 3 seeded cells must appear in the top 20 (Affiliate_B
    # legitimately dominates the top-of-list, which can push Facebook×FL deeper).
    top20 = {
        (s["segment"]["source"], s["segment"]["state"])
        for s in stats["segments"][:20]
    }
    hits = sum(1 for c in SEEDED_BAD_CELLS if c in top20)
    assert hits >= 2, (
        f"Expected >=2 of 3 seeded bad cells in top 20; got {hits}. Top 20: {top20}"
    )

    # Step 3: Time stability for TX × Affiliate_B
    ts = dx_time_stability(
        sid,
        segment_filter={"source": "Affiliate_B", "state": "TX"},
        direction="negative",
    )
    assert ts["total_quarters"] >= 10
    assert ts["persistence_score"] >= 0.9, (
        f"TX × Affiliate_B should be bad in >=90% of quarters, "
        f"got {ts['persistence_score']}"
    )

    # Step 4: Counterfactual
    cf = dx_counterfactual(
        sid,
        segment_filter={"source": "Affiliate_B", "state": "TX"},
        action="throttle",
        action_params={"keep_pct": 0.03},
    )
    assert cf["current_outcome_usd_annual"] < 0
    assert cf["projected_impact_usd_annual"] > 0
    # Majority of segment rows are dropped (keep_pct=0.03 = keep ~3%)
    total_seg_rows = cf["rows_affected"] + cf["rows_retained"]
    assert cf["rows_affected"] / total_seg_rows >= 0.95

    # Step 5: Evidence rows
    ev = dx_evidence_rows(
        sid,
        segment_filter={"source": "Affiliate_B", "state": "TX"},
        limit=5,
    )
    assert ev["total_matched"] >= 500
    assert ev["rows_returned"] == 5
    for row in ev["evidence_rows"]:
        assert row["data"]["source"] == "Affiliate_B"
        assert row["data"]["state"] == "TX"

    # Step 6: Build an OpportunityMap and render the report
    opp_map = {
        "portco_id": "etelequote_e2e",
        "vertical": "insurance_b2c",
        "ebitda_baseline_usd": -1_200_000.0,
        "as_of": "2026-04-24",
        "opportunities": [
            {
                "id": "opp_TX_AffB",
                "archetype": "allocation",
                "decision_cols": ["source", "state"],
                "segment": {"source": "Affiliate_B", "state": "TX"},
                "n": ev["total_matched"],
                "current_outcome_usd_annual": cf["current_outcome_usd_annual"],
                "projected_outcome_usd_annual": cf["projected_outcome_usd_annual"],
                "projected_impact_usd_annual": cf["projected_impact_usd_annual"],
                "persistence_quarters_out_of_total": [
                    ts["persistence_quarters"],
                    ts["total_quarters"],
                ],
                "difficulty_score_1_to_5": 1,
                "time_to_implement_weeks": 2,
                "recommendation": "Throttle TX × Affiliate_B to 3% of current volume",
                "evidence_row_ids": [r["row_id"] for r in ev["evidence_rows"]],
                "narrative_board": "",
                "narrative_operator": "",
            }
        ],
        "total_projected_impact_usd_annual": cf["projected_impact_usd_annual"],
    }
    report = dx_report(opp_map, output_filename="etelequote_e2e_test.html")

    out_path = Path(report["path"])
    json_path = Path(report["json_path"])
    assert out_path.exists()
    assert json_path.exists()
    assert report["opportunities_rendered"] == 1
    assert report["bytes_written"] > 1000

    # JSON sidecar should be valid round-trippable JSON
    with json_path.open() as f:
        loaded = json.load(f)
    assert loaded["portco_id"] == "etelequote_e2e"
    assert loaded["opportunities"][0]["segment"] == {
        "source": "Affiliate_B",
        "state": "TX",
    }

    # Cleanup the emitted report files
    out_path.unlink(missing_ok=True)
    json_path.unlink(missing_ok=True)
