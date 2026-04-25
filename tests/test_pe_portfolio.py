"""
PE portfolio-stage skill and command validation tests.

Validates that all 5 portfolio-stage skills and 5 corresponding commands exist with correct
structure, frontmatter, length constraints, MCP tool references, and skill linkages.
"""
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PLUGIN_DIR = PROJECT_ROOT / "finance-mcp-plugin"
SKILLS_DIR = PLUGIN_DIR / "skills" / "private-equity"
COMMANDS_DIR = PLUGIN_DIR / "commands"

PORTFOLIO_SKILLS = [
    "portfolio-monitoring",
    "returns-analysis",
    "unit-economics",
    "value-creation-plan",
    "ai-readiness",
]

PORTFOLIO_COMMANDS = [
    "portfolio",
    "returns",
    "unit-economics",
    "value-creation",
    "ai-readiness",
]

# Map each command to the skill name it should reference
COMMAND_TO_SKILL = {
    "portfolio": "portfolio-monitoring",
    "returns": "returns-analysis",
    "unit-economics": "unit-economics",
    "value-creation": "value-creation-plan",
    "ai-readiness": "ai-readiness",
}


def test_portfolio_skills_exist():
    """All 5 SKILL.md files must exist under skills/private-equity/."""
    for skill in PORTFOLIO_SKILLS:
        path = SKILLS_DIR / skill / "SKILL.md"
        assert path.exists(), f"Missing skill file: {path}"


def test_portfolio_skills_have_frontmatter():
    """Each SKILL.md must start with --- YAML frontmatter containing name and description."""
    for skill in PORTFOLIO_SKILLS:
        path = SKILLS_DIR / skill / "SKILL.md"
        text = path.read_text()
        assert text.startswith("---"), f"{skill}/SKILL.md must start with YAML frontmatter (---)"
        assert "name:" in text, f"{skill}/SKILL.md frontmatter missing 'name:' field"
        assert "description:" in text, f"{skill}/SKILL.md frontmatter missing 'description:' field"


@pytest.mark.parametrize("skill", PORTFOLIO_SKILLS)
def test_portfolio_skills_minimum_length(skill):
    """Each SKILL.md must be at least 200 lines."""
    path = SKILLS_DIR / skill / "SKILL.md"
    lines = path.read_text().strip().split("\n")
    assert len(lines) >= 200, (
        f"{skill}/SKILL.md is too short: {len(lines)} lines (minimum 200 required)"
    )


@pytest.mark.parametrize("skill", PORTFOLIO_SKILLS)
def test_portfolio_skills_maximum_length(skill):
    """Each SKILL.md must be no more than 400 lines."""
    path = SKILLS_DIR / skill / "SKILL.md"
    lines = path.read_text().strip().split("\n")
    assert len(lines) <= 400, (
        f"{skill}/SKILL.md is too long: {len(lines)} lines (maximum 400 allowed)"
    )


def test_portfolio_monitoring_references_classify_investor():
    """portfolio-monitoring SKILL.md must reference the classify_investor MCP tool."""
    text = (SKILLS_DIR / "portfolio-monitoring" / "SKILL.md").read_text()
    assert "classify_investor" in text, (
        "portfolio-monitoring SKILL.md must reference MCP tool 'classify_investor'"
    )


def test_portfolio_monitoring_references_get_risk_metrics():
    """portfolio-monitoring SKILL.md must reference the get_risk_metrics MCP tool."""
    text = (SKILLS_DIR / "portfolio-monitoring" / "SKILL.md").read_text()
    assert "get_risk_metrics" in text, (
        "portfolio-monitoring SKILL.md must reference MCP tool 'get_risk_metrics'"
    )


def test_returns_analysis_references_get_returns():
    """returns-analysis SKILL.md must reference the get_returns MCP tool."""
    text = (SKILLS_DIR / "returns-analysis" / "SKILL.md").read_text()
    assert "get_returns" in text, (
        "returns-analysis SKILL.md must reference MCP tool 'get_returns'"
    )


def test_returns_analysis_references_get_risk_metrics():
    """returns-analysis SKILL.md must reference the get_risk_metrics MCP tool."""
    text = (SKILLS_DIR / "returns-analysis" / "SKILL.md").read_text()
    assert "get_risk_metrics" in text, (
        "returns-analysis SKILL.md must reference MCP tool 'get_risk_metrics'"
    )


def test_unit_economics_references_ingest_csv():
    """unit-economics SKILL.md must reference the ingest_csv MCP tool."""
    text = (SKILLS_DIR / "unit-economics" / "SKILL.md").read_text()
    assert "ingest_csv" in text, (
        "unit-economics SKILL.md must reference MCP tool 'ingest_csv'"
    )


def test_value_creation_has_ebitda_bridge():
    """value-creation-plan SKILL.md must contain EBITDA framework content."""
    text = (SKILLS_DIR / "value-creation-plan" / "SKILL.md").read_text()
    assert "EBITDA" in text, (
        "value-creation-plan SKILL.md must contain 'EBITDA' (case-sensitive) for EBITDA bridge framework"
    )


def test_ai_readiness_has_go_wait_gate():
    """ai-readiness SKILL.md must contain go/wait gate language."""
    text = (SKILLS_DIR / "ai-readiness" / "SKILL.md").read_text().lower()
    assert "go" in text, (
        "ai-readiness SKILL.md must contain 'go' (case-insensitive) for go/wait gate"
    )
    assert "wait" in text, (
        "ai-readiness SKILL.md must contain 'wait' (case-insensitive) for go/wait gate"
    )


def test_portfolio_commands_exist():
    """All 5 command .md files must exist in commands/."""
    for cmd in PORTFOLIO_COMMANDS:
        path = COMMANDS_DIR / f"{cmd}.md"
        assert path.exists(), f"Missing command file: {path}"


def test_portfolio_commands_have_frontmatter():
    """Each command must start with --- YAML frontmatter containing a description field."""
    for cmd in PORTFOLIO_COMMANDS:
        path = COMMANDS_DIR / f"{cmd}.md"
        text = path.read_text()
        assert text.startswith("---"), (
            f"commands/{cmd}.md must start with YAML frontmatter (---)"
        )
        assert "description:" in text, (
            f"commands/{cmd}.md frontmatter missing 'description:' field"
        )


@pytest.mark.parametrize("cmd", PORTFOLIO_COMMANDS)
def test_portfolio_commands_are_lightweight(cmd):
    """Each command file must be 3–10 lines (lightweight loader, not a full skill)."""
    path = COMMANDS_DIR / f"{cmd}.md"
    lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
    assert len(lines) >= 3, (
        f"commands/{cmd}.md is too short: {len(lines)} non-empty lines (minimum 3)"
    )
    assert len(lines) <= 10, (
        f"commands/{cmd}.md is too long: {len(lines)} non-empty lines (maximum 10 for lightweight commands)"
    )


@pytest.mark.parametrize("cmd,skill", COMMAND_TO_SKILL.items())
def test_portfolio_commands_reference_skills(cmd, skill):
    """Each command must reference its corresponding skill name in the body."""
    path = COMMANDS_DIR / f"{cmd}.md"
    text = path.read_text()
    assert skill in text, (
        f"commands/{cmd}.md must reference skill '{skill}' in the command body"
    )
