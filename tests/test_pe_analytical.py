"""
PE analytical engine skill and command validation tests.

Validates that all 5 analytical engine skills and 5 corresponding commands exist with correct
structure, frontmatter, length constraints, MCP tool references, and skill linkages.
"""
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PLUGIN_DIR = PROJECT_ROOT / "finance-mcp-plugin"
SKILLS_DIR = PLUGIN_DIR / "skills" / "private-equity"
COMMANDS_DIR = PLUGIN_DIR / "commands"

ANALYTICAL_SKILLS = [
    "prospect-scoring",
    "liquidity-risk",
    "pipeline-profiling",
    "public-comp-analysis",
    "market-risk-scan",
]

ANALYTICAL_COMMANDS = [
    "score-prospect",
    "liquidity-risk",
    "profile-pipeline",
    "public-comps",
    "market-risk",
]

# Map each command to the skill name it should reference
COMMAND_TO_SKILL = {
    "score-prospect": "prospect-scoring",
    "liquidity-risk": "liquidity-risk",
    "profile-pipeline": "pipeline-profiling",
    "public-comps": "public-comp-analysis",
    "market-risk": "market-risk-scan",
}


def test_analytical_skills_exist():
    """All 5 SKILL.md files must exist under skills/private-equity/."""
    for skill in ANALYTICAL_SKILLS:
        path = SKILLS_DIR / skill / "SKILL.md"
        assert path.exists(), f"Missing skill file: {path}"


def test_analytical_skills_have_frontmatter():
    """Each SKILL.md must start with --- YAML frontmatter containing name and description."""
    for skill in ANALYTICAL_SKILLS:
        path = SKILLS_DIR / skill / "SKILL.md"
        text = path.read_text()
        assert text.startswith("---"), f"{skill}/SKILL.md must start with YAML frontmatter (---)"
        assert "name:" in text, f"{skill}/SKILL.md frontmatter missing 'name:' field"
        assert "description:" in text, f"{skill}/SKILL.md frontmatter missing 'description:' field"


@pytest.mark.parametrize("skill", ANALYTICAL_SKILLS)
def test_analytical_skills_minimum_length(skill):
    """Each SKILL.md must be at least 200 lines."""
    path = SKILLS_DIR / skill / "SKILL.md"
    lines = path.read_text().strip().split("\n")
    assert len(lines) >= 200, (
        f"{skill}/SKILL.md is too short: {len(lines)} lines (minimum 200 required)"
    )


@pytest.mark.parametrize("skill", ANALYTICAL_SKILLS)
def test_analytical_skills_maximum_length(skill):
    """Each SKILL.md must be no more than 400 lines."""
    path = SKILLS_DIR / skill / "SKILL.md"
    lines = path.read_text().strip().split("\n")
    assert len(lines) <= 400, (
        f"{skill}/SKILL.md is too long: {len(lines)} lines (maximum 400 allowed)"
    )


def test_prospect_scoring_references_investor_classifier():
    """prospect-scoring SKILL.md must reference the investor_classifier MCP tool."""
    text = (SKILLS_DIR / "prospect-scoring" / "SKILL.md").read_text()
    assert "investor_classifier" in text, (
        "prospect-scoring SKILL.md must reference MCP tool 'investor_classifier'"
    )


def test_prospect_scoring_references_classify_investor():
    """prospect-scoring SKILL.md must reference the classify_investor MCP tool."""
    text = (SKILLS_DIR / "prospect-scoring" / "SKILL.md").read_text()
    assert "classify_investor" in text, (
        "prospect-scoring SKILL.md must reference MCP tool 'classify_investor'"
    )


def test_liquidity_risk_references_liquidity_predictor():
    """liquidity-risk SKILL.md must reference the liquidity_predictor MCP tool."""
    text = (SKILLS_DIR / "liquidity-risk" / "SKILL.md").read_text()
    assert "liquidity_predictor" in text, (
        "liquidity-risk SKILL.md must reference MCP tool 'liquidity_predictor'"
    )


def test_liquidity_risk_references_predict_liquidity():
    """liquidity-risk SKILL.md must reference the predict_liquidity MCP tool."""
    text = (SKILLS_DIR / "liquidity-risk" / "SKILL.md").read_text()
    assert "predict_liquidity" in text, (
        "liquidity-risk SKILL.md must reference MCP tool 'predict_liquidity'"
    )


def test_pipeline_profiling_references_ingest_csv():
    """pipeline-profiling SKILL.md must reference the ingest_csv MCP tool."""
    text = (SKILLS_DIR / "pipeline-profiling" / "SKILL.md").read_text()
    assert "ingest_csv" in text, (
        "pipeline-profiling SKILL.md must reference MCP tool 'ingest_csv'"
    )


def test_public_comp_references_compare_tickers():
    """public-comp-analysis SKILL.md must reference the compare_tickers MCP tool."""
    text = (SKILLS_DIR / "public-comp-analysis" / "SKILL.md").read_text()
    assert "compare_tickers" in text, (
        "public-comp-analysis SKILL.md must reference MCP tool 'compare_tickers'"
    )


def test_public_comp_references_correlation_map():
    """public-comp-analysis SKILL.md must reference the correlation_map MCP tool."""
    text = (SKILLS_DIR / "public-comp-analysis" / "SKILL.md").read_text()
    assert "correlation_map" in text, (
        "public-comp-analysis SKILL.md must reference MCP tool 'correlation_map'"
    )


def test_market_risk_references_get_volatility():
    """market-risk-scan SKILL.md must reference the get_volatility MCP tool."""
    text = (SKILLS_DIR / "market-risk-scan" / "SKILL.md").read_text()
    assert "get_volatility" in text, (
        "market-risk-scan SKILL.md must reference MCP tool 'get_volatility'"
    )


def test_market_risk_references_get_risk_metrics():
    """market-risk-scan SKILL.md must reference the get_risk_metrics MCP tool."""
    text = (SKILLS_DIR / "market-risk-scan" / "SKILL.md").read_text()
    assert "get_risk_metrics" in text, (
        "market-risk-scan SKILL.md must reference MCP tool 'get_risk_metrics'"
    )


def test_market_risk_references_analyze_stock():
    """market-risk-scan SKILL.md must reference the analyze_stock MCP tool."""
    text = (SKILLS_DIR / "market-risk-scan" / "SKILL.md").read_text()
    assert "analyze_stock" in text, (
        "market-risk-scan SKILL.md must reference MCP tool 'analyze_stock'"
    )


def test_analytical_commands_exist():
    """All 5 command .md files must exist in commands/."""
    for cmd in ANALYTICAL_COMMANDS:
        path = COMMANDS_DIR / f"{cmd}.md"
        assert path.exists(), f"Missing command file: {path}"


def test_analytical_commands_have_frontmatter():
    """Each command must start with --- YAML frontmatter containing a description field."""
    for cmd in ANALYTICAL_COMMANDS:
        path = COMMANDS_DIR / f"{cmd}.md"
        text = path.read_text()
        assert text.startswith("---"), (
            f"commands/{cmd}.md must start with YAML frontmatter (---)"
        )
        assert "description:" in text, (
            f"commands/{cmd}.md frontmatter missing 'description:' field"
        )


@pytest.mark.parametrize("cmd", ANALYTICAL_COMMANDS)
def test_analytical_commands_are_lightweight(cmd):
    """Each command file must be 3-10 lines (lightweight loader, not a full skill)."""
    path = COMMANDS_DIR / f"{cmd}.md"
    lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
    assert len(lines) >= 3, (
        f"commands/{cmd}.md is too short: {len(lines)} non-empty lines (minimum 3)"
    )
    assert len(lines) <= 10, (
        f"commands/{cmd}.md is too long: {len(lines)} non-empty lines (maximum 10 for lightweight commands)"
    )


@pytest.mark.parametrize("cmd,skill", COMMAND_TO_SKILL.items())
def test_analytical_commands_reference_skills(cmd, skill):
    """Each command must reference its corresponding skill name in the body."""
    path = COMMANDS_DIR / f"{cmd}.md"
    text = path.read_text()
    assert skill in text, (
        f"commands/{cmd}.md must reference skill '{skill}' in the command body"
    )
