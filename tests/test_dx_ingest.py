"""Unit tests for dx_ingest."""
from __future__ import annotations

import pandas as pd
import pytest
from fastmcp.exceptions import ToolError

from finance_mcp.dx.ingest import dx_ingest
from finance_mcp.dx.session import clear_sessions, get_session


@pytest.fixture(autouse=True)
def _clean_sessions():
    clear_sessions()
    yield
    clear_sessions()


def _write_small_insurance_csvs(tmp_path):
    """Write a tiny 3-month insurance_b2c dataset and return (leads, policies, agents) paths."""
    leads = pd.DataFrame(
        {
            "lead_id": range(1, 61),
            "source": ["Affiliate_B"] * 30 + ["Google"] * 30,
            "state": ["TX"] * 30 + ["CA"] * 30,
            "agent_id": [1] * 60,
            "cost_usd": [50.0] * 60,
            "received_ts": pd.date_range("2025-01-01", periods=60, freq="D").astype(str),
        }
    )
    policies = pd.DataFrame(
        {
            "policy_id": [1001, 1002, 1003],
            "lead_id": [2, 45, 46],
            "issued": [1, 1, 1],
            "premium_annual": [1500.0, 1500.0, 1500.0],
            "commission": [300.0, 300.0, 300.0],
            "chargeback_flag": [1, 0, 0],
        }
    )
    agents = pd.DataFrame(
        {"agent_id": [1], "team": ["Alpha"], "tenure_months": [24]}
    )
    lp = tmp_path / "leads.csv"
    pp = tmp_path / "policies.csv"
    ap = tmp_path / "agents.csv"
    leads.to_csv(lp, index=False)
    policies.to_csv(pp, index=False)
    agents.to_csv(ap, index=False)
    return str(lp), str(pp), str(ap)


def test_ingest_happy_path(tmp_path):
    lp, pp, ap = _write_small_insurance_csvs(tmp_path)
    r = dx_ingest([lp, pp, ap], vertical="insurance_b2c", portco_id="t1")

    assert r["portco_id"] == "t1"
    assert r["template_id"] == "insurance_b2c"
    assert r["entities_loaded"] == {"lead": 60, "policy": 3, "agent": 1}
    assert r["joined_rows"] == 60
    assert r["session_id"].startswith("dx_")
    assert r["null_rate_outcome"] == 0.0


def test_ingest_computes_outcome_column(tmp_path):
    lp, pp, ap = _write_small_insurance_csvs(tmp_path)
    r = dx_ingest([lp, pp, ap], vertical="insurance_b2c", portco_id="t1")
    session = get_session(r["session_id"])
    df = session.joined
    assert "_outcome_usd" in df.columns
    # Non-converts: outcome = -cost_usd = -50
    # Converts with no chargeback: 300 - 50 = +250
    # Converts with chargeback: 300 * 0 - 50 = -50
    assert df["_outcome_usd"].min() == pytest.approx(-50.0)
    assert df["_outcome_usd"].max() == pytest.approx(250.0)


def test_ingest_auto_match(tmp_path):
    lp, pp, ap = _write_small_insurance_csvs(tmp_path)
    r = dx_ingest([lp, pp, ap], vertical="auto", portco_id="t1")
    assert r["template_id"] == "insurance_b2c"
    assert r["template_match_confidence"] > 0.5


def test_ingest_missing_file_raises(tmp_path):
    with pytest.raises(ToolError, match="not found"):
        dx_ingest(
            [str(tmp_path / "nope.csv")],
            vertical="insurance_b2c",
            portco_id="t1",
        )


def test_ingest_unknown_template_raises(tmp_path):
    lp, pp, ap = _write_small_insurance_csvs(tmp_path)
    with pytest.raises(KeyError, match="Unknown vertical template"):
        dx_ingest([lp, pp, ap], vertical="does_not_exist", portco_id="t1")


def test_ingest_no_matching_files_raises(tmp_path):
    df = pd.DataFrame({"a": [1]})
    random_path = tmp_path / "random_no_match.csv"
    df.to_csv(random_path, index=False)
    with pytest.raises(ToolError, match="No provided file matched"):
        dx_ingest(
            [str(random_path)], vertical="insurance_b2c", portco_id="t1"
        )
