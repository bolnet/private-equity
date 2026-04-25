"""
PE deal-flow skill and command validation tests.

Validates that all 5 deal-flow skills and 5 corresponding commands exist with correct
structure, frontmatter, length constraints, MCP tool references, and skill linkages.
"""
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PLUGIN_DIR = PROJECT_ROOT / "finance-mcp-plugin"
SKILLS_DIR = PLUGIN_DIR / "skills" / "private-equity"
COMMANDS_DIR = PLUGIN_DIR / "commands"

DEAL_FLOW_SKILLS = [
    "deal-sourcing",
    "deal-screening",
    "dd-checklist",
    "dd-meeting-prep",
    "ic-memo",
]

DEAL_FLOW_COMMANDS = [
    "source",
    "screen-deal",
    "dd-checklist",
    "dd-prep",
    "ic-memo",
]

# Map each command to the skill name it should reference
COMMAND_TO_SKILL = {
    "source": "deal-sourcing",
    "screen-deal": "deal-screening",
    "dd-checklist": "dd-checklist",
    "dd-prep": "dd-meeting-prep",
    "ic-memo": "ic-memo",
}


def test_deal_flow_skills_exist():
    """All 5 SKILL.md files must exist under skills/private-equity/."""
    for skill in DEAL_FLOW_SKILLS:
        path = SKILLS_DIR / skill / "SKILL.md"
        assert path.exists(), f"Missing skill file: {path}"


def test_deal_flow_skills_have_frontmatter():
    """Each SKILL.md must start with --- YAML frontmatter containing name and description."""
    for skill in DEAL_FLOW_SKILLS:
        path = SKILLS_DIR / skill / "SKILL.md"
        text = path.read_text()
        assert text.startswith("---"), f"{skill}/SKILL.md must start with YAML frontmatter (---)"
        assert "name:" in text, f"{skill}/SKILL.md frontmatter missing 'name:' field"
        assert "description:" in text, f"{skill}/SKILL.md frontmatter missing 'description:' field"


@pytest.mark.parametrize("skill", DEAL_FLOW_SKILLS)
def test_deal_flow_skills_minimum_length(skill):
    """Each SKILL.md must be at least 200 lines."""
    path = SKILLS_DIR / skill / "SKILL.md"
    lines = path.read_text().strip().split("\n")
    assert len(lines) >= 200, (
        f"{skill}/SKILL.md is too short: {len(lines)} lines (minimum 200 required)"
    )


def test_deal_sourcing_references_ingest_csv():
    """deal-sourcing SKILL.md must reference the ingest_csv MCP tool."""
    text = (SKILLS_DIR / "deal-sourcing" / "SKILL.md").read_text()
    assert "ingest_csv" in text, (
        "deal-sourcing SKILL.md must reference MCP tool 'ingest_csv'"
    )


def test_deal_screening_has_pass_fail():
    """deal-screening SKILL.md must contain pass and fail framework language."""
    text = (SKILLS_DIR / "deal-screening" / "SKILL.md").read_text().lower()
    assert "pass" in text, "deal-screening SKILL.md must contain 'pass' (pass/fail framework)"
    assert "fail" in text, "deal-screening SKILL.md must contain 'fail' (pass/fail framework)"


def test_ic_memo_references_classify_investor():
    """ic-memo SKILL.md must reference the classify_investor MCP tool."""
    text = (SKILLS_DIR / "ic-memo" / "SKILL.md").read_text()
    assert "classify_investor" in text, (
        "ic-memo SKILL.md must reference MCP tool 'classify_investor'"
    )


def test_deal_flow_commands_exist():
    """All 5 command .md files must exist in commands/."""
    for cmd in DEAL_FLOW_COMMANDS:
        path = COMMANDS_DIR / f"{cmd}.md"
        assert path.exists(), f"Missing command file: {path}"


def test_deal_flow_commands_have_frontmatter():
    """Each command must start with --- YAML frontmatter containing a description field."""
    for cmd in DEAL_FLOW_COMMANDS:
        path = COMMANDS_DIR / f"{cmd}.md"
        text = path.read_text()
        assert text.startswith("---"), (
            f"commands/{cmd}.md must start with YAML frontmatter (---)"
        )
        assert "description:" in text, (
            f"commands/{cmd}.md frontmatter missing 'description:' field"
        )


@pytest.mark.parametrize("cmd", DEAL_FLOW_COMMANDS)
def test_deal_flow_commands_are_lightweight(cmd):
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
def test_deal_flow_commands_reference_skills(cmd, skill):
    """Each command must reference its corresponding skill name in the body."""
    path = COMMANDS_DIR / f"{cmd}.md"
    text = path.read_text()
    assert skill in text, (
        f"commands/{cmd}.md must reference skill '{skill}' in the command body"
    )
