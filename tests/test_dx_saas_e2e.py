"""
End-to-end reproduction of the SaaS pricing pattern.

Validates that the DX pipeline — using a *second* vertical template — finds
the seeded discount × customer-size cross-section pattern: deep discounts
to small customers destroy LTV/CAC, while the same discounts to large
customers are highly profitable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from demo.saas_pricing import generate as saas_gen  # noqa: E402

from finance_mcp.dx.counterfactual import dx_counterfactual  # noqa: E402
from finance_mcp.dx.evidence import dx_evidence_rows  # noqa: E402
from finance_mcp.dx.ingest import dx_ingest  # noqa: E402
from finance_mcp.dx.memo import dx_memo  # noqa: E402
from finance_mcp.dx.report import dx_report  # noqa: E402
from finance_mcp.dx.segment_stats import dx_segment_stats  # noqa: E402
from finance_mcp.dx.session import clear_sessions  # noqa: E402
from finance_mcp.dx.time_stability import dx_time_stability  # noqa: E402


SEEDED_BAD = {"discount_bucket": "30-50%", "employee_bucket": "<50"}
SEEDED_GOOD = {"discount_bucket": "0-10%", "employee_bucket": ">500"}


@pytest.fixture(scope="module")
def saas_data(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("saas_e2e")
    saas_gen.generate(
        out_dir=str(out_dir), n_deals=8_000, months=36, seed=42
    )
    return {
        "deals": str(out_dir / "deals.csv"),
        "customers": str(out_dir / "customers.csv"),
    }


@pytest.fixture(autouse=True)
def _clean():
    clear_sessions()
    yield
    clear_sessions()


def test_e2e_saas_pattern(saas_data, tmp_path):
    # Step 1: Ingest
    ingest = dx_ingest(
        data_paths=[saas_data["deals"], saas_data["customers"]],
        vertical="saas_pricing",
        portco_id="saas_e2e",
    )
    assert ingest["gates_failed"] == []
    assert ingest["template_id"] == "saas_pricing"
    assert ingest["months_coverage"] >= 30
    assert ingest["joined_rows"] == 8_000
    sid = ingest["session_id"]

    # Step 2: Segment search on discount × employee size
    stats = dx_segment_stats(
        sid,
        decision_cols=["discount_bucket", "employee_bucket"],
        min_segment_n=20,  # smaller cells in 8k sample
        top_k=16,
        rank_by="worst_total",
    )
    assert len(stats["segments"]) >= 6

    # Top-worst segment must be the seeded 30-50% × <50 cell
    worst = stats["segments"][0]
    assert worst["segment"] == SEEDED_BAD
    assert worst["outcome_mean"] < 0, (
        f"Seeded 30-50% × <50 should have negative mean; got {worst['outcome_mean']}"
    )

    # Step 3: Persistence — 30-50% × <50 should be bad across most quarters
    ts = dx_time_stability(sid, segment_filter=SEEDED_BAD, direction="negative")
    assert ts["total_quarters"] >= 8
    assert ts["persistence_score"] >= 0.75, (
        f"Expected seeded bad cell to be negative in >=75% of quarters; "
        f"got {ts['persistence_score']}"
    )

    # Step 4: Counterfactual — capping deep-discount small-customer deals at a
    # baseline should project a positive impact
    cf = dx_counterfactual(
        sid,
        segment_filter=SEEDED_BAD,
        action="reroute",
        action_params={"outcome_replacement": 1_500.0},
    )
    assert cf["current_outcome_usd_annual"] < 0
    assert cf["projected_impact_usd_annual"] > 0

    # Step 5: Validate with the "good" cell — same 30-50% discount level is
    # PROFITABLE at >500-employee customers. This is the cross-section insight.
    good_stats = dx_segment_stats(
        sid,
        decision_cols=["discount_bucket", "employee_bucket"],
        min_segment_n=20,
        top_k=5,
        rank_by="best_total",
    )
    good_cells = [
        (s["segment"]["discount_bucket"], s["segment"]["employee_bucket"])
        for s in good_stats["segments"][:3]
    ]
    assert ("30-50%", ">500") in good_cells, (
        f"Deep discount × large customer should be a top-best cell; "
        f"got top 3 = {good_cells}"
    )

    # Step 6: Evidence rows ground the claim
    ev = dx_evidence_rows(sid, segment_filter=SEEDED_BAD, limit=5)
    assert ev["total_matched"] >= 20
    assert ev["rows_returned"] == 5
    for row in ev["evidence_rows"]:
        assert row["data"]["discount_bucket"] == "30-50%"
        assert row["data"]["employee_bucket"] == "<50"

    # Step 7: Build Opportunity, validate memo with dx_memo, render report
    opp = {
        "id": "opp_saas_discount_small",
        "archetype": "pricing",
        "decision_cols": ["discount_bucket", "employee_bucket"],
        "segment": SEEDED_BAD,
        "n": ev["total_matched"],
        "current_outcome_usd_annual": cf["current_outcome_usd_annual"],
        "projected_outcome_usd_annual": cf["projected_outcome_usd_annual"],
        "projected_impact_usd_annual": cf["projected_impact_usd_annual"],
        "persistence_quarters_out_of_total": [
            ts["persistence_quarters"],
            ts["total_quarters"],
        ],
        "difficulty_score_1_to_5": 2,
        "time_to_implement_weeks": 3,
        "recommendation": "Cap discounts at 20% for <50-employee customers",
        "evidence_row_ids": [r["row_id"] for r in ev["evidence_rows"]],
        "narrative_board": "",
        "narrative_operator": "",
    }

    memo = dx_memo(opp, audience="board")
    assert memo["validated"] is True, f"violations: {memo['violations']}"
    assert memo["used_default_skeleton"] is True

    opp_map = {
        "portco_id": "saas_e2e",
        "vertical": "saas_pricing",
        "ebitda_baseline_usd": 40_000_000.0,
        "as_of": "2026-04-24",
        "opportunities": [opp],
        "total_projected_impact_usd_annual": cf["projected_impact_usd_annual"],
    }
    report = dx_report(opp_map, output_filename="saas_e2e_test.html")
    out_path = Path(report["path"])
    json_path = Path(report["json_path"])
    assert out_path.exists() and json_path.exists()

    with json_path.open() as f:
        loaded = json.load(f)
    assert loaded["portco_id"] == "saas_e2e"
    assert loaded["vertical"] == "saas_pricing"

    out_path.unlink(missing_ok=True)
    json_path.unlink(missing_ok=True)
