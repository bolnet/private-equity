"""Unit + E2E tests for the benchmarking (BX) module."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from finance_mcp.bx.archetype_index import bx_archetype_index
from finance_mcp.bx.delta import bx_delta
from finance_mcp.bx.ingest_corpus import bx_ingest_corpus
from finance_mcp.bx.peer_group import bx_peer_group
from finance_mcp.bx.rank import bx_portco_rank
from finance_mcp.bx.report import bx_report
from finance_mcp.bx.session import clear_sessions
from finance_mcp.bx.snapshot import bx_snapshot, list_snapshots
from finance_mcp.bx.trend import bx_trend


def _opp(i: int, arch: str, impact: float, persist: int = 10) -> dict:
    return {
        "id": f"opp_{i}",
        "archetype": arch,
        "decision_cols": ["x", "y"],
        "segment": {"x": "A", "y": f"B{i}"},
        "n": 500,
        "current_outcome_usd_annual": -impact * 0.5,
        "projected_outcome_usd_annual": impact * 0.5,
        "projected_impact_usd_annual": impact,
        "persistence_quarters_out_of_total": [persist, 12],
        "difficulty_score_1_to_5": 2,
        "time_to_implement_weeks": 3,
        "recommendation": f"Fix opp {i}",
    }


def _opp_map(pid: str, vertical: str, ebitda: float, opps: list[dict]) -> dict:
    return {
        "portco_id": pid,
        "vertical": vertical,
        "ebitda_baseline_usd": ebitda,
        "as_of": "2026-04-24",
        "opportunities": opps,
        "total_projected_impact_usd_annual": sum(
            o["projected_impact_usd_annual"] for o in opps
        ),
    }


def _write_maps(tmp_path: Path, maps: dict[str, dict]) -> list[str]:
    paths = []
    for name, m in maps.items():
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(m), encoding="utf-8")
        paths.append(str(p))
    return paths


@pytest.fixture(autouse=True)
def _clean():
    clear_sessions()
    yield
    clear_sessions()


@pytest.fixture
def three_portco_corpus(tmp_path):
    """Standard 3-portco fixture used across most tests."""
    maps = {
        "etelequote": _opp_map(
            "etelequote",
            "insurance_b2c",
            -1_200_000,
            [_opp(1, "allocation", 3_800_000, 12), _opp(2, "allocation", 3_100_000, 11)],
        ),
        "saas_alpha": _opp_map(
            "saas_alpha",
            "saas_pricing",
            40_000_000,
            [_opp(1, "pricing", 2_100_000, 11), _opp(2, "pricing", 800_000, 9)],
        ),
        "industrial_co": _opp_map(
            "industrial_co",
            "industrial",
            15_000_000,
            [_opp(1, "selection", 1_100_000, 10), _opp(2, "allocation", 600_000, 9)],
        ),
    }
    return _write_maps(tmp_path, maps)


# ----------------------- dx_ingest_corpus -----------------------


def test_ingest_basic(three_portco_corpus):
    r = bx_ingest_corpus(three_portco_corpus, corpus_id="t1")
    assert r["corpus_id"] == "t1"
    assert r["portco_count"] == 3
    assert r["opportunity_count"] == 6


def test_ingest_auto_corpus_id(three_portco_corpus):
    r = bx_ingest_corpus(three_portco_corpus)
    assert r["corpus_id"].startswith("bx_")


def test_ingest_empty_list_raises():
    with pytest.raises(ToolError, match="at least one"):
        bx_ingest_corpus([])


def test_ingest_missing_file_raises(tmp_path):
    with pytest.raises(ToolError, match="not found"):
        bx_ingest_corpus([str(tmp_path / "nope.json")])


def test_ingest_invalid_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not-json-at-all")
    with pytest.raises(ToolError, match="Failed to parse"):
        bx_ingest_corpus([str(bad)])


def test_ingest_missing_fields_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"portco_id": "x"}))  # missing most fields
    with pytest.raises(ToolError, match="missing required fields"):
        bx_ingest_corpus([str(bad)])


# ----------------------- bx_portco_rank -----------------------


def test_rank_top_portco(three_portco_corpus):
    bx_ingest_corpus(three_portco_corpus, corpus_id="r1")
    r = bx_portco_rank("r1", "etelequote", "total_projected_impact_usd_annual")
    assert r["rank"] == 1
    assert r["rank_total"] == 3
    assert r["percentile"] == 100.0
    assert r["value"] == pytest.approx(6_900_000.0)


def test_rank_middle_portco(three_portco_corpus):
    bx_ingest_corpus(three_portco_corpus, corpus_id="r1")
    r = bx_portco_rank("r1", "saas_alpha", "total_projected_impact_usd_annual")
    assert r["rank"] == 2


def test_rank_unknown_metric_raises(three_portco_corpus):
    bx_ingest_corpus(three_portco_corpus, corpus_id="r1")
    with pytest.raises(ToolError, match="metric must be"):
        bx_portco_rank("r1", "etelequote", "nope")


def test_rank_unknown_portco_raises(three_portco_corpus):
    bx_ingest_corpus(three_portco_corpus, corpus_id="r1")
    with pytest.raises(ToolError, match="not in corpus"):
        bx_portco_rank("r1", "ghost", "total_projected_impact_usd_annual")


# ----------------------- bx_archetype_index -----------------------


def test_archetype_index_counts(three_portco_corpus):
    bx_ingest_corpus(three_portco_corpus, corpus_id="a1")
    idx = bx_archetype_index("a1")
    stats = {s["archetype"]: s for s in idx["archetype_stats"]}
    # allocation: etelequote (2 opps) + industrial_co (1 opp) = 2 portcos
    assert stats["allocation"]["portco_count_with_archetype"] == 2
    assert stats["allocation"]["opportunity_count"] == 3
    # pricing: only saas_alpha
    assert stats["pricing"]["portco_count_with_archetype"] == 1
    # routing: no one
    assert stats["routing"]["portco_count_with_archetype"] == 0


def test_archetype_index_share_sums_to_100(three_portco_corpus):
    bx_ingest_corpus(three_portco_corpus, corpus_id="a1")
    idx = bx_archetype_index("a1")
    total = sum(s["share_of_corpus_total_pct"] for s in idx["archetype_stats"])
    assert 99.5 <= total <= 100.5  # rounding tolerance


# ----------------------- bx_peer_group -----------------------


def test_peer_group_returns_ranked_peers(three_portco_corpus):
    bx_ingest_corpus(three_portco_corpus, corpus_id="p1")
    pg = bx_peer_group("p1", "etelequote", top_n=2)
    assert pg["reference_portco_id"] == "etelequote"
    assert pg["reference_top_archetype"] == "allocation"
    assert len(pg["peers"]) == 2
    # Sorted descending by similarity
    sims = [p["similarity_score"] for p in pg["peers"]]
    assert sims == sorted(sims, reverse=True)


def test_peer_group_shared_top_archetype_marker(three_portco_corpus):
    bx_ingest_corpus(three_portco_corpus, corpus_id="p1")
    pg = bx_peer_group("p1", "etelequote", top_n=5)
    # industrial_co's top archetype is selection (not allocation), so
    # shared_top_archetype should be '—'
    industrial = next(p for p in pg["peers"] if p["portco_id"] == "industrial_co")
    assert industrial["shared_top_archetype"] == "—"
    assert industrial["top_archetype"] == "selection"


def test_peer_group_single_portco(tmp_path):
    m = _opp_map("only", "x", 1_000_000, [_opp(1, "allocation", 100_000)])
    paths = _write_maps(tmp_path, {"only": m})
    bx_ingest_corpus(paths, corpus_id="p2")
    pg = bx_peer_group("p2", "only")
    assert pg["peers"] == []
    assert "fewer than 2" in pg["note"]


# ----------------------- bx_report -----------------------


def test_report_renders_html_and_json(three_portco_corpus, tmp_path, monkeypatch):
    bx_ingest_corpus(three_portco_corpus, corpus_id="rep1")
    # Force output into tmp_path so tests don't leave artifacts
    monkeypatch.setattr(
        "finance_mcp.bx.report.SCRIPT_DIR", str(tmp_path)
    )
    r = bx_report("rep1", output_filename="test_report.html")
    html_path = Path(r["path"])
    json_path = Path(r["json_path"])
    assert html_path.exists()
    assert json_path.exists()
    content = html_path.read_text(encoding="utf-8")
    assert "Cross-Portco Benchmark" in content
    assert "etelequote" in content
    assert "allocation" in content.lower()

    sidecar = json.loads(json_path.read_text(encoding="utf-8"))
    assert sidecar["corpus_id"] == "rep1"
    assert sidecar["portco_count"] == 3
    assert "archetype_index" in sidecar
    assert len(sidecar["rank_table"]) == 3


# ----------------------- snapshot / trend / delta -----------------------


@pytest.fixture
def snap_dir(tmp_path, monkeypatch):
    """Redirect snapshot writes to a temp directory."""
    monkeypatch.setattr(
        "finance_mcp.bx.snapshot.SCRIPT_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "finance_mcp.bx.trend.list_snapshots",
        lambda pid: list_snapshots(pid),
    )
    return tmp_path


def test_snapshot_writes_dated_file(snap_dir):
    m = _opp_map("demo", "x", 1_000_000, [_opp(1, "allocation", 100_000)])
    r = bx_snapshot(m, snapshot_date="2026-01-15")
    assert Path(r["path"]).exists()
    assert r["snapshot_date"] == "2026-01-15"
    assert r["opportunity_count"] == 1


def test_snapshot_requires_portco_id(snap_dir):
    with pytest.raises(ToolError, match="portco_id"):
        bx_snapshot({"ebitda_baseline_usd": 1})


def test_snapshot_bad_date_format(snap_dir):
    m = _opp_map("demo", "x", 1_000_000, [])
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        bx_snapshot(m, snapshot_date="Jan 15 2026")


def test_trend_chronological(snap_dir):
    m_v1 = _opp_map("demo", "x", 1_000_000, [_opp(1, "allocation", 500_000)])
    m_v2 = _opp_map("demo", "x", 1_000_000, [_opp(2, "allocation", 300_000)])
    bx_snapshot(m_v1, snapshot_date="2026-01-01")
    bx_snapshot(m_v2, snapshot_date="2026-04-01")
    tr = bx_trend("demo")
    assert tr["snapshot_count"] == 2
    dates = [p["snapshot_date"] for p in tr["points"]]
    assert dates == ["2026-01-01", "2026-04-01"]


def test_trend_no_snapshots(snap_dir):
    tr = bx_trend("never_seen")
    assert tr["points"] == []


def test_delta_captures_closed_and_new(snap_dir):
    opps_v1 = [
        _opp(1, "allocation", 3_000_000),
        _opp(2, "pricing", 1_000_000),
    ]
    opps_v2 = [
        _opp(2, "pricing", 1_000_000),  # persistent
        _opp(3, "timing", 500_000),  # new
    ]
    m_v1 = _opp_map("demo", "x", 1_000_000, opps_v1)
    m_v2 = _opp_map("demo", "x", 1_000_000, opps_v2)
    bx_snapshot(m_v1, snapshot_date="2026-01-15")
    bx_snapshot(m_v2, snapshot_date="2026-04-15")
    dl = bx_delta("demo")
    assert dl["opportunities_closed"] == 1
    assert dl["opportunities_new"] == 1
    assert dl["opportunities_persistent"] == 1
    assert dl["delta_total_impact_usd"] == pytest.approx(-2_500_000.0)
    assert dl["days_elapsed"] == 90
    assert "opp_1" in dl["closed_opp_ids"]
    assert "opp_3" in dl["new_opp_ids"]


def test_delta_single_snapshot(snap_dir):
    m = _opp_map("demo", "x", 1_000_000, [_opp(1, "allocation", 500_000)])
    bx_snapshot(m, snapshot_date="2026-01-01")
    dl = bx_delta("demo")
    assert "at least 2" in dl["note"]


# ----------------------- full E2E -----------------------


def test_e2e_corpus_plus_timeseries(tmp_path, monkeypatch):
    """The full LP narrative: benchmark + delta across snapshots of one portco."""
    monkeypatch.setattr(
        "finance_mcp.bx.snapshot.SCRIPT_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "finance_mcp.bx.report.SCRIPT_DIR", str(tmp_path)
    )

    maps = {
        "etelequote_q1": _opp_map(
            "etelequote",
            "insurance_b2c",
            -1_200_000,
            [
                _opp(1, "allocation", 3_800_000, 12),
                _opp(2, "allocation", 3_100_000, 11),
                _opp(3, "allocation", 2_800_000, 10),
            ],
        ),
        "saas_alpha_q1": _opp_map(
            "saas_alpha",
            "saas_pricing",
            40_000_000,
            [_opp(1, "pricing", 2_100_000, 11)],
        ),
    }
    paths = _write_maps(tmp_path, maps)

    # Benchmark
    bx_ingest_corpus(paths, corpus_id="q1_benchmark")
    r = bx_portco_rank("q1_benchmark", "etelequote", "total_projected_impact_usd_annual")
    assert r["rank"] == 1

    # Snapshot both quarters for etelequote (top opp remediated in Q2)
    q1 = maps["etelequote_q1"]
    q2 = _opp_map(
        "etelequote",
        "insurance_b2c",
        -1_200_000,
        [_opp(2, "allocation", 3_100_000, 11), _opp(3, "allocation", 2_800_000, 10)],
    )
    bx_snapshot(q1, snapshot_date="2026-01-24")
    bx_snapshot(q2, snapshot_date="2026-04-24")

    dl = bx_delta("etelequote")
    assert dl["opportunities_closed"] == 1
    assert "opp_1" in dl["closed_opp_ids"]
    assert dl["delta_total_impact_usd"] == pytest.approx(-3_800_000.0)

    # Full report renders
    rep = bx_report("q1_benchmark", output_filename="e2e_report.html")
    assert Path(rep["path"]).exists()
    assert rep["portco_count"] == 2
