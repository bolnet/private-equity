"""
Test the lending_b2c vertical template and orchestrator integration.

The Lending Club slice (`demo/lending_club/{loans,performance}.csv`) is
gitignored — regenerate via `python -m demo.lending_club.slice`. These
tests skip cleanly when the files are absent so the suite stays green
on fresh clones.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from finance_mcp.dx.ingest import dx_ingest
from finance_mcp.dx.segment_stats import dx_segment_stats
from finance_mcp.dx.session import clear_sessions
from finance_mcp.dx.templates import get_template, list_templates
from finance_mcp.dx_orchestrator import run_diagnostic


DEMO_DIR = Path(__file__).resolve().parent.parent / "demo" / "lending_club"
LOANS = DEMO_DIR / "loans.csv"
PERF = DEMO_DIR / "performance.csv"

_requires_demo = pytest.mark.skipif(
    not (LOANS.exists() and PERF.exists()),
    reason=(
        "lending_club demo CSVs not present; regenerate with "
        "`python -m demo.lending_club.slice`"
    ),
)


def test_template_registered():
    assert "lending_b2c" in list_templates()
    tpl = get_template("lending_b2c")
    assert tpl.timestamp_column == "issue_d"
    assert {e.name for e in tpl.entities} == {"loan", "performance"}
    archetypes = {a.archetype for a in tpl.archetypes}
    assert {"pricing", "selection", "allocation"}.issubset(archetypes)


@_requires_demo
def test_lending_ingest_and_segment_stats():
    clear_sessions()
    ing = dx_ingest(
        data_paths=[str(LOANS), str(PERF)],
        vertical="auto",
        portco_id="lending_test",
    )
    assert ing["template_id"] == "lending_b2c"
    assert ing["gates_failed"] == []
    assert ing["joined_rows"] > 1_000
    assert ing["months_coverage"] >= 12

    stats = dx_segment_stats(
        session_id=ing["session_id"],
        decision_cols=["purpose", "grade"],
        min_segment_n=30,
        top_k=20,
        rank_by="worst_total",
    )
    # Real data must contain negative-outcome segments at sub-prime grades.
    worst = stats["segments"][0]
    assert worst["outcome_total_usd_annual"] < 0
    assert worst["segment"]["grade"] in {"E", "F", "G"}


@_requires_demo
def test_lending_end_to_end_produces_report():
    clear_sessions()
    result = run_diagnostic(
        data_paths=[str(LOANS), str(PERF)],
        portco_id="lending_e2e",
        top_k_opportunities=3,
    )
    assert result.template_id == "lending_b2c"
    assert result.opportunities_rendered >= 1
    assert result.total_impact_usd_annual > 0
    assert Path(result.report_path).exists()
    assert Path(result.json_path).exists()
