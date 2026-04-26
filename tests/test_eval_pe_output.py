"""
Smoke + unit tests for eval_pe_output.

Runs against the real corpus already on disk under finance_output/.
Validates that:
  - Citation accuracy is ~100% for the deterministic templated explainer
    (the explainer is built so every $ figure comes from a structured field).
  - Coverage is 100% for memo/source pairs that share all opp ids.
  - Hallucination rate is low for templated prose.
  - The HTML + JSON sidecar are written.
  - Consistency between board + operator memos sharing one source = 1.0.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from finance_mcp.eval import eval_pe_output

REPO_ROOT = Path(__file__).resolve().parent.parent
FIN = REPO_ROOT / "finance_output"


def _abs(rel: str) -> str:
    return str(FIN / rel)


# --- Smoke pairs -----------------------------------------------------------

SMOKE_PAIRS = [
    ("explain_MortgageCo_board.json",      "dx_report_MortgageCo.json"),
    ("explain_HMDA_GA_board.json",         "dx_report_HMDA_GA.json"),
    ("explain_HMDA_GA_operator.json",      "dx_report_HMDA_GA.json"),
    ("explain_southeast_lender_board.json","dx_report_southeast_lender.json"),
]


@pytest.mark.parametrize("memo_name,source_name", SMOKE_PAIRS)
def test_smoke_real_corpus(memo_name: str, source_name: str) -> None:
    memo_path = FIN / memo_name
    source_path = FIN / source_name
    if not memo_path.exists() or not source_path.exists():
        pytest.skip(f"Fixture missing: {memo_path} or {source_path}")

    result = eval_pe_output(str(memo_path), str(source_path))

    assert "scores" in result
    scores = result["scores"]
    # Templated explainer should produce ~100% citation accuracy.
    assert scores["citation_accuracy"] >= 0.95, (
        f"{memo_name}: citation_accuracy={scores['citation_accuracy']:.2%} "
        f"(expected ~100% for deterministic templated memos)"
    )
    # Coverage should be 100% — explainer addresses every opp.
    assert scores["coverage"] == 1.0, (
        f"{memo_name}: coverage={scores['coverage']:.2%} (expected 100%)"
    )
    # Hallucination should be near zero on templated prose.
    assert scores["hallucination_rate"] <= 0.10, (
        f"{memo_name}: hallucination_rate={scores['hallucination_rate']:.2%}"
    )
    # Artifacts written.
    assert Path(result["report_path"]).exists()
    assert Path(result["json_path"]).exists()


def test_consistency_board_vs_operator() -> None:
    """HMDA_GA has both board + operator memos for the same source — they
    should agree on headline."""
    memo = FIN / "explain_HMDA_GA_board.json"
    source = FIN / "dx_report_HMDA_GA.json"
    if not memo.exists() or not source.exists():
        pytest.skip("HMDA_GA fixtures missing")
    result = eval_pe_output(str(memo), str(source))
    assert result["scores"]["consistency"] == 1.0


def test_missing_memo_raises() -> None:
    with pytest.raises(ToolError, match="Memo JSON not found"):
        eval_pe_output("/nonexistent/memo.json", str(FIN / "dx_report_MortgageCo.json"))


def test_missing_source_raises() -> None:
    with pytest.raises(ToolError, match="Source JSON not found"):
        eval_pe_output(str(FIN / "explain_MortgageCo_board.json"), "/nonexistent/src.json")


def test_malformed_memo_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    with pytest.raises(ToolError, match="Memo JSON is malformed"):
        eval_pe_output(str(bad), str(FIN / "dx_report_MortgageCo.json"))


def test_empty_memo_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"opportunities_explained": []}))
    with pytest.raises(ToolError, match="zero opportunities_explained"):
        eval_pe_output(str(empty), str(FIN / "dx_report_MortgageCo.json"))


def test_per_opp_breakdown_shape() -> None:
    """The per-opp JSON sidecar carries one row per memo opp."""
    memo = FIN / "explain_MortgageCo_board.json"
    source = FIN / "dx_report_MortgageCo.json"
    if not memo.exists() or not source.exists():
        pytest.skip("MortgageCo fixtures missing")
    result = eval_pe_output(str(memo), str(source))
    payload = json.loads(Path(result["json_path"]).read_text())
    assert len(payload["per_opp"]) == 3  # MortgageCo has 3 opportunities
    for row in payload["per_opp"]:
        assert "opp_id" in row
        assert "figures_total" in row
        assert "figures_cited" in row
        assert "addressed_in_memo" in row
