"""Unit tests for dx_segment_stats."""
from __future__ import annotations

import pandas as pd
import pytest
from fastmcp.exceptions import ToolError

from finance_mcp.dx.ingest import dx_ingest
from finance_mcp.dx.segment_stats import dx_segment_stats
from finance_mcp.dx.session import clear_sessions


@pytest.fixture(autouse=True)
def _clean_sessions():
    clear_sessions()
    yield
    clear_sessions()


def _setup_session(tmp_path):
    """Create a session with 3 clearly-separated cells of outcome."""
    # 40 TX Aff_B losers, 40 CA Google winners, 40 FL Facebook neutrals
    leads = pd.DataFrame(
        {
            "lead_id": range(1, 121),
            "source": (["Affiliate_B"] * 40) + (["Google"] * 40) + (["Facebook"] * 40),
            "state": (["TX"] * 40) + (["CA"] * 40) + (["FL"] * 40),
            "agent_id": [1] * 120,
            "cost_usd": [50.0] * 120,
            "received_ts": pd.date_range("2024-01-01", periods=120, freq="3D").astype(str),
        }
    )
    # Only CA-Google converts (40 winners), and one FL-Facebook converts (neutral).
    policies = pd.DataFrame(
        {
            "policy_id": list(range(1001, 1041)) + [2000],
            "lead_id": list(range(41, 81)) + [81],
            "issued": [1] * 41,
            "premium_annual": [1500.0] * 41,
            "commission": [400.0] * 41,  # 400 - 50 = +350 per winner
            "chargeback_flag": [0] * 40 + [1],  # the FL Facebook one is chargeback -> net -50
        }
    )
    agents = pd.DataFrame({"agent_id": [1], "team": ["A"], "tenure_months": [12]})
    lp, pp, ap = tmp_path / "leads.csv", tmp_path / "policies.csv", tmp_path / "agents.csv"
    leads.to_csv(lp, index=False)
    policies.to_csv(pp, index=False)
    agents.to_csv(ap, index=False)
    return dx_ingest([str(lp), str(pp), str(ap)], vertical="insurance_b2c", portco_id="t")


def test_segment_stats_worst_first(tmp_path):
    r = _setup_session(tmp_path)
    out = dx_segment_stats(
        r["session_id"],
        decision_cols=["source", "state"],
        min_segment_n=30,
        top_k=5,
        rank_by="worst_total",
    )
    assert len(out["segments"]) >= 1
    # Worst must be TX × Affiliate_B (all 40 are non-converts at -$50)
    worst = out["segments"][0]
    assert worst["segment"] == {"source": "Affiliate_B", "state": "TX"}
    assert worst["n"] == 40
    assert worst["outcome_mean"] == pytest.approx(-50.0)


def test_segment_stats_best_first(tmp_path):
    r = _setup_session(tmp_path)
    out = dx_segment_stats(
        r["session_id"],
        decision_cols=["source", "state"],
        min_segment_n=30,
        top_k=5,
        rank_by="best_total",
    )
    best = out["segments"][0]
    assert best["segment"] == {"source": "Google", "state": "CA"}
    assert best["outcome_mean"] == pytest.approx(350.0)


def test_segment_stats_filters_small(tmp_path):
    r = _setup_session(tmp_path)
    out = dx_segment_stats(
        r["session_id"],
        decision_cols=["source", "state"],
        min_segment_n=50,  # all 40-row cells excluded
        top_k=5,
    )
    assert out["segments"] == []


def test_segment_stats_unknown_col(tmp_path):
    r = _setup_session(tmp_path)
    with pytest.raises(ToolError, match="Decision columns not in data"):
        dx_segment_stats(r["session_id"], decision_cols=["nonexistent"])
