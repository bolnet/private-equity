"""Smoke tests for the EU AI Act audit tool.

Runs the two PE-relevant scenarios required by the build spec:
  1. Consumer-lending credit decisioning  -> high-risk (Annex III §5(b))
  2. SaaS marketing personalization        -> limited-risk (Article 50)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_mcp.eu_ai_act import ai_act_audit
from finance_mcp.eu_ai_act.articles import HIGH_RISK_DEADLINE


@pytest.mark.unit
def test_credit_decisioning_classifies_high_risk(tmp_path, monkeypatch):
    """A consumer-lending portco's underwriting model is high-risk per
    Annex III §5(b)."""
    monkeypatch.chdir(tmp_path)
    result = ai_act_audit(
        portco_id="LendingCo-EU",
        ai_system_description=(
            "A gradient-boosted underwriting model that scores consumer "
            "personal-loan applications across 12 EU markets. Inputs include "
            "bureau data, bank-transaction features, and self-reported income. "
            "Outputs a probability of default used to set approve/decline and "
            "price tier."
        ),
        use_case_category="credit_decisioning",
    )

    assert result["high_risk_classification"] == "high-risk"
    assert result["deadline"] == HIGH_RISK_DEADLINE.isoformat() == "2026-08-02"
    assert "Article 6" in result["articles_addressed"]
    assert "Article 9" in result["articles_addressed"]
    assert "Article 11" in result["articles_addressed"]
    assert "Article 13" in result["articles_addressed"]
    assert "Article 14" in result["articles_addressed"]
    assert "Article 15" in result["articles_addressed"]

    report_path = Path(result["report_path"])
    json_path = Path(result["json_path"])
    assert report_path.exists()
    assert json_path.exists()

    html = report_path.read_text(encoding="utf-8")
    assert "<title>EU AI Act compliance pack" in html
    assert "High-risk" in html
    assert "Annex III §5(b)" in html
    assert "2026-08-02" in html
    assert "Article 9" in html
    assert "Article 15" in html

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["high_risk_classification"] == "high-risk"
    assert payload["matched_annex_iii_category"]["annex_ref"] == "Annex III §5(b)"
    assert payload["regulation"]["url"].startswith("https://eur-lex.europa.eu")


@pytest.mark.unit
def test_marketing_personalization_classifies_limited_risk(tmp_path, monkeypatch):
    """SaaS marketing personalization is not enumerated in Annex III; it
    falls under Article 50 transparency obligations."""
    monkeypatch.chdir(tmp_path)
    result = ai_act_audit(
        portco_id="MarketingSaaSCo",
        ai_system_description=(
            "A recommender engine that ranks email subject lines and product "
            "tiles for B2C e-commerce customers. Trained on aggregate click "
            "data, no protected-attribute features."
        ),
        use_case_category="marketing_personalization",
    )

    assert result["high_risk_classification"] == "limited-risk"
    assert "Article 50" in result["articles_addressed"]
    # Article 9-15 should NOT be in a limited-risk pack
    assert "Article 9" not in result["articles_addressed"]
    assert "Article 15" not in result["articles_addressed"]

    report_path = Path(result["report_path"])
    html = report_path.read_text(encoding="utf-8")
    assert "Limited-risk" in html
    assert "Article 50" in html


@pytest.mark.unit
def test_unknown_use_case_raises_tool_error(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolError):
        ai_act_audit(
            portco_id="X",
            ai_system_description="something",
            use_case_category="not_a_real_key",
        )


@pytest.mark.unit
def test_empty_portco_raises_tool_error(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolError):
        ai_act_audit(
            portco_id="",
            ai_system_description="something",
            use_case_category="credit_decisioning",
        )
