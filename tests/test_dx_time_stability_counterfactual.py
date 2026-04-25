"""Unit tests for dx_time_stability, dx_counterfactual, dx_evidence_rows."""
from __future__ import annotations

import pandas as pd
import pytest
from fastmcp.exceptions import ToolError

from finance_mcp.dx.counterfactual import dx_counterfactual
from finance_mcp.dx.evidence import dx_evidence_rows
from finance_mcp.dx.ingest import dx_ingest
from finance_mcp.dx.session import clear_sessions
from finance_mcp.dx.time_stability import dx_time_stability


@pytest.fixture(autouse=True)
def _clean_sessions():
    clear_sessions()
    yield
    clear_sessions()


def _setup(tmp_path):
    """18 months of data spread across 6 quarters so stability is measurable."""
    # 180 TX Aff_B losers (30 per quarter × 6 quarters), 30 CA Google winners
    ts_tx = pd.date_range("2024-01-01", periods=180, freq="3D")
    ts_ca = pd.date_range("2024-01-01", periods=30, freq="18D")
    leads = pd.DataFrame(
        {
            "lead_id": list(range(1, 181)) + list(range(181, 211)),
            "source": ["Affiliate_B"] * 180 + ["Google"] * 30,
            "state": ["TX"] * 180 + ["CA"] * 30,
            "agent_id": [1] * 210,
            "cost_usd": [50.0] * 210,
            "received_ts": [t.isoformat() for t in ts_tx] + [t.isoformat() for t in ts_ca],
        }
    )
    policies = pd.DataFrame(
        {
            "policy_id": list(range(1001, 1031)),
            "lead_id": list(range(181, 211)),  # only CA Google converts
            "issued": [1] * 30,
            "premium_annual": [1500.0] * 30,
            "commission": [400.0] * 30,
            "chargeback_flag": [0] * 30,
        }
    )
    agents = pd.DataFrame({"agent_id": [1], "team": ["A"], "tenure_months": [10]})
    lp, pp, ap = tmp_path / "leads.csv", tmp_path / "policies.csv", tmp_path / "agents.csv"
    leads.to_csv(lp, index=False)
    policies.to_csv(pp, index=False)
    agents.to_csv(ap, index=False)
    return dx_ingest([str(lp), str(pp), str(ap)], vertical="insurance_b2c", portco_id="t")


def test_time_stability_fully_persistent(tmp_path):
    r = _setup(tmp_path)
    out = dx_time_stability(
        r["session_id"],
        segment_filter={"source": "Affiliate_B", "state": "TX"},
        direction="negative",
    )
    assert out["total_quarters"] >= 5
    assert out["persistence_score"] >= 0.8
    assert all(m < 0 for m in out["quarterly_outcome_mean"])


def test_time_stability_empty_segment(tmp_path):
    r = _setup(tmp_path)
    out = dx_time_stability(
        r["session_id"],
        segment_filter={"source": "Affiliate_B", "state": "ZZ"},
    )
    assert out["total_quarters"] == 0
    assert out["persistence_score"] == 0.0


def test_counterfactual_throttle_improves_losses(tmp_path):
    r = _setup(tmp_path)
    out = dx_counterfactual(
        r["session_id"],
        segment_filter={"source": "Affiliate_B", "state": "TX"},
        action="throttle",
        action_params={"keep_pct": 0.0},
    )
    assert out["current_outcome_usd_annual"] < 0
    assert out["projected_outcome_usd_annual"] == 0.0
    assert out["projected_impact_usd_annual"] > 0
    assert out["rows_affected"] == 180


def test_counterfactual_discontinue(tmp_path):
    r = _setup(tmp_path)
    out = dx_counterfactual(
        r["session_id"],
        segment_filter={"source": "Affiliate_B", "state": "TX"},
        action="discontinue",
    )
    assert out["projected_outcome_usd_annual"] == 0.0
    assert out["projected_impact_usd_annual"] > 0


def test_counterfactual_invalid_keep_pct(tmp_path):
    r = _setup(tmp_path)
    with pytest.raises(ToolError, match="keep_pct in"):
        dx_counterfactual(
            r["session_id"],
            segment_filter={"source": "Affiliate_B", "state": "TX"},
            action="throttle",
            action_params={"keep_pct": 1.5},
        )


def test_counterfactual_reroute_requires_replacement(tmp_path):
    r = _setup(tmp_path)
    with pytest.raises(ToolError, match="outcome_replacement"):
        dx_counterfactual(
            r["session_id"],
            segment_filter={"source": "Affiliate_B", "state": "TX"},
            action="reroute",
        )


def test_evidence_rows_returns_sample(tmp_path):
    r = _setup(tmp_path)
    out = dx_evidence_rows(
        r["session_id"],
        segment_filter={"source": "Affiliate_B", "state": "TX"},
        limit=5,
    )
    assert out["total_matched"] == 180
    assert out["rows_returned"] == 5
    for row in out["evidence_rows"]:
        assert row["data"]["source"] == "Affiliate_B"
        assert row["data"]["state"] == "TX"


def test_evidence_rows_limit_capped(tmp_path):
    r = _setup(tmp_path)
    out = dx_evidence_rows(
        r["session_id"],
        segment_filter={"source": "Affiliate_B", "state": "TX"},
        limit=500,  # over the 100 cap
    )
    assert out["rows_returned"] <= 100
