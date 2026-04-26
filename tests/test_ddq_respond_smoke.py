"""Smoke test for ddq_respond against the existing finance_output/ corpus."""
import json
import os
from pathlib import Path

import pytest

from finance_mcp.ddq import ddq_respond
from finance_mcp.ddq.consistency import (
    extract_entities,
    extract_figures,
    check_consistency,
)
from finance_mcp.ddq.questions import get_questions


REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_BASE = REPO_ROOT / "finance_output"


@pytest.mark.smoke
def test_question_set_is_frozen():
    qs = get_questions()
    assert 12 <= len(qs) <= 15, "Frozen DDQ set must have 12-15 questions."
    ids = {q.id for q in qs}
    assert len(ids) == len(qs), "Question IDs must be unique."
    categories = {q.category for q in qs}
    assert {"GOV", "DATA", "MRM", "VEND", "REG", "VAL", "EXIT"} <= categories


@pytest.mark.smoke
def test_extract_figures_recognizes_dollars_counts_percents():
    text = "Across 12 portcos the fund holds $1.23B in claims; 67% are operator-controllable."
    figures = extract_figures(text)
    kinds = {f.kind for f in figures}
    assert kinds == {"dollar", "count", "percent"}
    dollars = [f for f in figures if f.kind == "dollar"]
    assert any(abs(f.value - 1.23e9) < 1.0 for f in dollars)
    counts = [f for f in figures if f.kind == "count"]
    assert any(f.value == 12 and f.unit.startswith("portco") for f in counts)


@pytest.mark.smoke
def test_extract_entities_recognizes_portco_id_shapes():
    text = "Portcos covered: MortgageCo, HMDA_GA, LendingCo-EU, DCMortgage."
    ents = extract_entities(text)
    assert {"MortgageCo", "HMDA_GA", "LendingCo-EU", "DCMortgage"} <= ents


@pytest.mark.smoke
def test_consistency_flags_count_mismatch():
    answers = [
        {
            "question_id": "Q01_GOV_INVENTORY",
            "category": "GOV",
            "text": "12 portcos under coverage. Includes MortgageCo and HMDA_GA.",
            "figures": extract_figures("12 portcos under coverage."),
            "entities": extract_entities("Includes MortgageCo and HMDA_GA."),
        },
        {
            "question_id": "Q02_GOV_OVERSIGHT",
            "category": "GOV",
            "text": "14 portcos rolled up.",
            "figures": extract_figures("14 portcos rolled up."),
            "entities": set(),
        },
    ]
    flags = check_consistency(answers)
    assert any(f.flag_type == "numeric_mismatch" for f in flags)


@pytest.mark.smoke
def test_consistency_flags_entity_orphan():
    answers = [
        {
            "question_id": "Q01_GOV_INVENTORY",
            "category": "GOV",
            "text": "Portcos: MortgageCo.",
            "figures": [],
            "entities": {"MortgageCo"},
        },
        {
            "question_id": "Q09_REG_EU_AI_ACT",
            "category": "REG",
            "text": "EU scope: LendingCo-EU.",
            "figures": [],
            "entities": {"LendingCo-EU"},
        },
    ]
    flags = check_consistency(answers)
    assert any(f.flag_type == "entity_orphan" for f in flags)


@pytest.mark.smoke
def test_ddq_respond_end_to_end(tmp_path):
    """End-to-end smoke against the real finance_output/ corpus."""
    if not KNOWLEDGE_BASE.exists():
        pytest.skip("finance_output/ corpus not available in this environment.")

    result = ddq_respond(
        fund_name="Bolnet Capital Partners I",
        knowledge_base_dir=str(KNOWLEDGE_BASE),
    )
    assert os.path.isfile(result["report_path"])
    assert os.path.isfile(result["json_path"])
    assert 12 <= result["n_questions_answered"] <= 15
    assert result["knowledge_base_artifacts"] >= 5

    sidecar = json.loads(Path(result["json_path"]).read_text())
    assert sidecar["fund_name"] == "Bolnet Capital Partners I"
    assert len(sidecar["answers"]) == result["n_questions_answered"]

    # Every answer must cite at least one source artifact OR have an
    # explicit 'no artifact' placeholder; the citations field is populated
    # for at least 9/12 questions (the ones tied to artifact families).
    cited_count = sum(1 for a in sidecar["answers"] if a["citations"])
    assert cited_count >= 9, (
        f"Expected ≥9 questions to cite source artifacts; got {cited_count}."
    )

    # HTML must contain at least one source-artifact filename to prove the
    # citation rendering path works.
    html = Path(result["report_path"]).read_text()
    assert "dx_report_" in html or "ai_act_audit_" in html

    # Print summary for human inspection.
    print("\n--- DDQ smoke summary ---")
    print(f"  report_path:  {result['report_path']}")
    print(f"  n_questions:  {result['n_questions_answered']}")
    print(f"  n_flags:      {result['n_consistency_flags']}")
    print(f"  artifacts:    {result['knowledge_base_artifacts']}")
    print("\n  Q03 sample answer:")
    for a in sidecar["answers"]:
        if a["question_id"] == "Q03_DATA_LINEAGE":
            print(f"    {a['answer'][:280]}...")
            print(f"    cites: {a['citations'][:4]} (+{max(0, len(a['citations'])-4)})")
            break
    print("\n  Flags:")
    for f in sidecar["consistency_flags"][:5]:
        print(f"    [{f['severity']}] {f['flag_type']}: {f['description']}")
