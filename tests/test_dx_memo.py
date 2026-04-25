"""Unit tests for dx_memo — narrative validation + formatting."""
from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from finance_mcp.dx.memo import dx_memo


def _opp(**overrides) -> dict:
    """Minimal Opportunity dict for tests."""
    base = {
        "id": "opp_test_1",
        "archetype": "allocation",
        "decision_cols": ["source", "state"],
        "segment": {"source": "Affiliate_B", "state": "TX"},
        "n": 1430,
        "current_outcome_usd_annual": -14000.0,
        "projected_outcome_usd_annual": 4829.0,
        "projected_impact_usd_annual": 18829.0,
        "persistence_quarters_out_of_total": [12, 12],
        "difficulty_score_1_to_5": 1,
        "time_to_implement_weeks": 2,
        "recommendation": "Throttle TX × Affiliate_B to 3% of current volume",
        "evidence_row_ids": [56978, 79982, 59612],
        "narrative_board": "",
        "narrative_operator": "",
    }
    base.update(overrides)
    return base


def test_default_skeleton_when_empty_narrative():
    r = dx_memo(_opp(), audience="board")
    assert r["used_default_skeleton"] is True
    assert r["validated"] is True  # skeleton must self-validate
    assert "Affiliate_B" in r["formatted"]
    assert "$-14,000" in r["formatted"] or "-$14,000" in r["formatted"] or "$14,000" in r["formatted"]
    assert "$18,829" in r["formatted"] or "$18,828" in r["formatted"]


def test_default_skeleton_has_all_sections():
    r = dx_memo(_opp(), audience="board")
    for section in (
        "What the data says",
        "Why it persists",
        "Counterfactual",
        "Recommendation",
        "Implementation",
    ):
        assert section in r["formatted"], f"Missing section: {section}"


def test_operator_skeleton_extends_board():
    board = dx_memo(_opp(), audience="board")
    operator = dx_memo(_opp(), audience="operator")
    assert "Operator note" in operator["formatted"]
    assert "Operator note" not in board["formatted"]


def test_valid_narrative_passes_validation():
    good = (
        "### What the data says\n"
        "Over 1,430 rows in TX × Affiliate_B (sample rows 56978, 79982), segment returned "
        "$-14,000 annually.\n\n"
        "### Why it persists\n"
        "Bad in all 12 of 12 quarters; cross-section is hidden.\n\n"
        "### Counterfactual\n"
        "Throttling projects $4,829 annual outcome, a lift of $18,829.\n\n"
        "### Recommendation\n"
        "Throttle 97% of volume in this segment.\n\n"
        "### Implementation\n"
        "Vendor supports state-level caps. 2-week rollout."
    )
    r = dx_memo(_opp(narrative_board=good), audience="board")
    assert r["used_default_skeleton"] is False
    assert r["validated"] is True, f"violations: {r['violations']}"


def test_hallucinated_dollar_fails_validation():
    bad = (
        "### What the data says\n"
        "Over 1,430 rows.\n\n"
        "### Why it persists\n"
        "Bad in 12 of 12 quarters.\n\n"
        "### Counterfactual\n"
        "Throttling projects $75,000,000 annual lift.\n\n"  # hallucinated
        "### Recommendation\n"
        "Throttle.\n\n"
        "### Implementation\n"
        "Two weeks."
    )
    r = dx_memo(_opp(narrative_board=bad), audience="board")
    assert r["validated"] is False
    assert any("unsupported $ value" in v for v in r["violations"])


def test_missing_section_fails_validation():
    incomplete = (
        "### What the data says\n"
        "$-14,000 on 1,430 rows.\n\n"
        "### Recommendation\n"
        "Throttle."
    )
    r = dx_memo(_opp(narrative_board=incomplete), audience="board")
    assert r["validated"] is False
    assert any("missing sections" in v for v in r["violations"])


def test_word_count_enforcement():
    # 400-word narrative should exceed max_words=350
    long_text = (
        "### What the data says\n"
        + "word " * 400
        + "\n\n### Why it persists\n12 of 12 quarters.\n\n"
        + "### Counterfactual\n$4,829 projected.\n\n"
        + "### Recommendation\nThrottle.\n\n"
        + "### Implementation\nTwo weeks."
    )
    r = dx_memo(_opp(narrative_board=long_text), audience="board", max_words=350)
    assert r["validated"] is False
    assert any("exceeds max_words" in v for v in r["violations"])


def test_unit_abbreviations_accepted():
    """$18.8k should match $18,829 within ±5% tolerance."""
    text = (
        "### What the data says\n"
        "Over 1,430 rows. Sample: 56978, 79982.\n\n"
        "### Why it persists\n"
        "12 of 12 quarters.\n\n"
        "### Counterfactual\n"
        "Throttle projects $18.8k/yr lift, from $-14k to $4.8k.\n\n"
        "### Recommendation\n"
        "Throttle 97%.\n\n"
        "### Implementation\n"
        "2 weeks."
    )
    r = dx_memo(_opp(narrative_board=text), audience="board")
    assert r["validated"] is True, f"violations: {r['violations']}"


def test_invalid_audience_raises():
    with pytest.raises(ToolError, match="audience must be"):
        dx_memo(_opp(), audience="investor")  # type: ignore[arg-type]


def test_numbers_cited_and_allowed_in_response():
    r = dx_memo(_opp(), audience="board")
    assert isinstance(r["numbers_cited"], list)
    assert isinstance(r["numbers_allowed"], list)
    # Skeleton must cite at least one dollar amount
    assert len(r["numbers_cited"]) >= 1


# v2 validator additions ---------------------------------------------------


def test_v2_missing_evidence_citation_fails():
    """v2 requires every memo to cite at least one evidence_row_id when ids exist."""
    no_citation = (
        "### What the data says\n"
        "Over 1,430 rows. $-14,000 annually.\n\n"
        "### Why it persists\n"
        "12 of 12 quarters.\n\n"
        "### Counterfactual\n"
        "Lift of $18,829.\n\n"
        "### Recommendation\n"
        "Throttle.\n\n"
        "### Implementation\n"
        "2 weeks."
    )
    r = dx_memo(_opp(narrative_board=no_citation), audience="board")
    assert r["validated"] is False
    assert any("evidence_row_id" in v for v in r["violations"])


def test_v2_hedge_language_fails():
    """v2 disallows hedge words in memos (might / could / perhaps / etc)."""
    hedged = (
        "### What the data says\n"
        "Over 1,430 rows; sample 56978, 79982. Segment might be losing money.\n\n"
        "### Why it persists\n"
        "Negative in 12 of 12 quarters.\n\n"
        "### Counterfactual\n"
        "Throttling could potentially yield a $18,829 lift.\n\n"
        "### Recommendation\n"
        "Perhaps throttle 97%.\n\n"
        "### Implementation\n"
        "2 weeks."
    )
    r = dx_memo(_opp(narrative_board=hedged), audience="board")
    assert r["validated"] is False
    assert any("hedging language" in v for v in r["violations"])


def test_v2_default_skeleton_self_validates_with_evidence():
    """The deterministic skeleton must auto-cite evidence and pass v2 rules."""
    r = dx_memo(_opp(), audience="board")
    assert r["used_default_skeleton"] is True
    assert r["validated"] is True, f"violations: {r['violations']}"
