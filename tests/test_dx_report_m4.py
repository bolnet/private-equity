"""Tests for M4 report features — exec summary panel + embedded charts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_mcp.dx.report import (
    _render_exec_summary,
    _render_quarterly_chart_base64,
    dx_report,
)


def _opp_map(**overrides):
    base = {
        "portco_id": "testco",
        "vertical": "insurance_b2c",
        "ebitda_baseline_usd": 10_000_000.0,
        "as_of": "2026-04-24",
        "opportunities": [
            {
                "id": f"opp_{i}",
                "archetype": "allocation",
                "decision_cols": ["source", "state"],
                "segment": {"source": f"S{i}", "state": f"ST{i}"},
                "n": 100 + i,
                "current_outcome_usd_annual": -1000 * (i + 1),
                "projected_outcome_usd_annual": 500,
                "projected_impact_usd_annual": 500_000 * (5 - i),
                "persistence_quarters_out_of_total": [10, 12],
                "difficulty_score_1_to_5": 2,
                "time_to_implement_weeks": 2,
                "recommendation": f"Recommendation {i}",
                "narrative_board": "",
                "narrative_operator": "",
            }
            for i in range(5)
        ],
        "total_projected_impact_usd_annual": 500_000 * (5 + 4 + 3 + 2 + 1),
    }
    base.update(overrides)
    return base


# --------- exec summary ---------


def test_exec_summary_has_headline_total():
    html_str = _render_exec_summary(_opp_map())
    # Total = $7.5M (5+4+3+2+1 = 15 × 500k)
    assert "$7.50M" in html_str
    assert "identified" in html_str


def test_exec_summary_has_top3():
    html_str = _render_exec_summary(_opp_map())
    # The top-3 rows should show the three highest-ranked impacts
    assert "01" in html_str and "02" in html_str and "03" in html_str
    assert "Recommendation 0" in html_str
    assert "Recommendation 1" in html_str
    assert "Recommendation 2" in html_str
    # 4th/5th should NOT be in the top-3 block
    assert "04" not in html_str.split("bridge")[0]


def test_exec_summary_pct_of_ebitda():
    html_str = _render_exec_summary(_opp_map())
    # 7.5M / 10M = 75%
    assert "+75.0%" in html_str


def test_exec_summary_bridge_three_rows():
    html_str = _render_exec_summary(_opp_map())
    assert "bridge-fill baseline" in html_str
    assert "bridge-fill top3" in html_str
    assert "bridge-fill all" in html_str
    assert "Current baseline" in html_str


def test_exec_summary_empty_opps():
    m = _opp_map(opportunities=[], total_projected_impact_usd_annual=0.0)
    html_str = _render_exec_summary(m)
    assert html_str == ""


def test_exec_summary_coverage_pct():
    # Top 3 impact = (5+4+3) * 500k = 6M; total = 7.5M -> 80%
    html_str = _render_exec_summary(_opp_map())
    assert "80%" in html_str


# --------- chart encoder ---------


def test_chart_returns_data_url_when_inputs_valid():
    quarters = ["2024Q1", "2024Q2", "2024Q3", "2024Q4"]
    values = [-200.0, -150.0, -300.0, -180.0]
    src = _render_quarterly_chart_base64(quarters, values)
    assert src is not None
    assert src.startswith("data:image/png;base64,")
    assert len(src) > 1000  # actual PNG bytes encoded


def test_chart_returns_none_on_empty():
    assert _render_quarterly_chart_base64([], []) is None
    assert _render_quarterly_chart_base64(["Q1"], []) is None
    assert _render_quarterly_chart_base64([], [1.0]) is None


def test_chart_returns_none_on_length_mismatch():
    assert _render_quarterly_chart_base64(["Q1", "Q2"], [1.0]) is None


# --------- full dx_report integration ---------


def test_full_report_contains_exec_summary_and_chart(tmp_path):
    opp_map = _opp_map()
    # Add quarterly data to opp_0 so a chart renders
    opp_map["opportunities"][0]["quarters"] = ["Q1", "Q2", "Q3", "Q4"]
    opp_map["opportunities"][0]["quarterly_outcome_total_usd"] = [-100, -150, -90, -120]

    r = dx_report(opp_map, output_filename="m4_integration.html")
    out = Path(r["path"])
    content = out.read_text(encoding="utf-8")

    assert "Executive Summary" in content
    assert "EBITDA Bridge" in content
    assert "data:image/png;base64," in content  # embedded chart
    assert "Ranked opportunity map" in content  # existing section still there

    # JSON sidecar still round-trips
    with Path(r["json_path"]).open() as f:
        loaded = json.load(f)
    assert loaded["portco_id"] == "testco"

    out.unlink(missing_ok=True)
    Path(r["json_path"]).unlink(missing_ok=True)


def test_full_report_without_quarterly_data_has_no_chart(tmp_path):
    """Opportunities without quarterly data should render — just no chart."""
    r = dx_report(_opp_map(), output_filename="m4_no_chart.html")
    out = Path(r["path"])
    content = out.read_text(encoding="utf-8")

    assert "Executive Summary" in content  # exec summary still renders
    assert "data:image/png;base64," not in content
    # The CSS class selector "quarter-chart" appears in <style> — we only care
    # that no actual chart container was rendered.
    assert '<div class="quarter-chart">' not in content

    out.unlink(missing_ok=True)
    Path(r["json_path"]).unlink(missing_ok=True)
