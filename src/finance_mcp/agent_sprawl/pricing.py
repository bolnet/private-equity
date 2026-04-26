"""
Frozen Anthropic Claude pricing constants and per-tool token-budget estimates.

The prices are public Anthropic list prices for the Claude API (input and
output tokens, per million). The per-tool token budgets are *modeled, not
measured* — each PE MCP tool gets a plausible estimate of how many tokens
its surrounding agent loop would consume on a typical invocation, derived
from the tool's purpose (pure-pandas DX vs LLM-coupled explainer/CIM).

Telemetry caveat: a real deployment replaces ``estimate_monthly_cost_usd``
with measured token counts piped from a logging hook. Until then, the math
in this module is reproducible-but-modeled and labelled as such everywhere
it is rendered.

References
----------
- https://www.anthropic.com/pricing#anthropic-api  (Sonnet 4.6, Haiku 4.5
  list prices, retrieved Q1 2026 — frozen here for reproducibility).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# --- Anthropic public list prices (USD per million tokens) -----------------
#
# Frozen at module load. Update with care — every dollar figure in the audit
# report flows from these four constants.

SONNET_INPUT_USD_PER_MTOK: Final[float] = 3.00
SONNET_OUTPUT_USD_PER_MTOK: Final[float] = 15.00
HAIKU_INPUT_USD_PER_MTOK: Final[float] = 0.80
HAIKU_OUTPUT_USD_PER_MTOK: Final[float] = 4.00


@dataclass(frozen=True)
class ModelPricing:
    """List price for a single Claude model, USD per million tokens."""

    model_id: str
    input_per_mtok: float
    output_per_mtok: float

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        """Cost of one invocation given measured/modeled token counts."""
        return (
            input_tokens * self.input_per_mtok / 1_000_000.0
            + output_tokens * self.output_per_mtok / 1_000_000.0
        )


SONNET_4_6: Final[ModelPricing] = ModelPricing(
    model_id="claude-sonnet-4-6",
    input_per_mtok=SONNET_INPUT_USD_PER_MTOK,
    output_per_mtok=SONNET_OUTPUT_USD_PER_MTOK,
)
HAIKU_4_5: Final[ModelPricing] = ModelPricing(
    model_id="claude-haiku-4-5",
    input_per_mtok=HAIKU_INPUT_USD_PER_MTOK,
    output_per_mtok=HAIKU_OUTPUT_USD_PER_MTOK,
)

PRICING_BY_MODEL: Final[dict[str, ModelPricing]] = {
    SONNET_4_6.model_id: SONNET_4_6,
    HAIKU_4_5.model_id: HAIKU_4_5,
}


# --- Per-tool agent-loop token-budget envelopes ----------------------------
#
# Each PE MCP tool, when run inside Claude Code, sits inside an agent loop
# that consumes tokens around the tool call (instructions, tool result
# observation, follow-up reasoning). The budgets below are conservative
# per-invocation estimates derived from each tool family's surface area:
#
#   - DX (pandas-only diagnostics)         small input, no LLM-shaped output
#   - BX (cross-portco benchmarking)       medium input (corpus), small output
#   - explainer / CIM / IC / seller pack   large input + large LLM-shaped narrative
#   - audit / eval / normalize             medium input + structured output
#   - utility (ping, validate_environment) tiny
#
# Family → (model, input_tokens_per_call, output_tokens_per_call,
#           calls_per_month, family_label, llm_coupled)


@dataclass(frozen=True)
class ToolBudget:
    """Modeled per-invocation token budget + monthly call volume for a tool."""

    family: str
    model: ModelPricing
    input_tokens_per_call: int
    output_tokens_per_call: int
    calls_per_month: int
    llm_coupled: bool

    def monthly_input_tokens(self) -> int:
        return self.input_tokens_per_call * self.calls_per_month

    def monthly_output_tokens(self) -> int:
        return self.output_tokens_per_call * self.calls_per_month

    def monthly_cost_usd(self) -> float:
        return self.model.cost_usd(
            self.monthly_input_tokens(),
            self.monthly_output_tokens(),
        )


# Family-level defaults. Concrete tools resolve via prefix-match (see below).
#
# Call volumes assume a portco-fleet deployment: a mid-market PE shop with
# ~30 portfolio companies running this tool family in their diligence and
# monitoring loops. A small in-house deployment would scale all calls/month
# values down by an order of magnitude. Token budgets per call are sized
# from the tool's purpose and surrounding agent loop.
_FAMILY_BUDGETS: Final[dict[str, ToolBudget]] = {
    "dx": ToolBudget(
        family="DX (decision-optimization diagnostic)",
        model=HAIKU_4_5,
        input_tokens_per_call=8_000,
        output_tokens_per_call=2_000,
        calls_per_month=1_200,
        llm_coupled=False,
    ),
    "bx": ToolBudget(
        family="BX (cross-portco benchmarking)",
        model=HAIKU_4_5,
        input_tokens_per_call=12_000,
        output_tokens_per_call=2_500,
        calls_per_month=900,
        llm_coupled=False,
    ),
    "explain": ToolBudget(
        family="Explainer (model-to-narrative)",
        model=SONNET_4_6,
        input_tokens_per_call=18_000,
        output_tokens_per_call=6_000,
        calls_per_month=2_500,
        llm_coupled=True,
    ),
    "cim": ToolBudget(
        family="CIM red-flag extractor",
        model=SONNET_4_6,
        input_tokens_per_call=40_000,
        output_tokens_per_call=4_000,
        calls_per_month=6_000,
        llm_coupled=True,
    ),
    "eval": ToolBudget(
        family="LLM eval (deterministic)",
        model=HAIKU_4_5,
        input_tokens_per_call=10_000,
        output_tokens_per_call=2_000,
        calls_per_month=500,
        llm_coupled=False,
    ),
    "exit_proof": ToolBudget(
        family="Seller-side diligence pack",
        model=SONNET_4_6,
        input_tokens_per_call=25_000,
        output_tokens_per_call=8_000,
        calls_per_month=120,
        llm_coupled=True,
    ),
    "ai_act": ToolBudget(
        family="EU AI Act compliance",
        model=SONNET_4_6,
        input_tokens_per_call=14_000,
        output_tokens_per_call=4_500,
        calls_per_month=80,
        llm_coupled=True,
    ),
    "normalize": ToolBudget(
        family="Portfolio normalization",
        model=HAIKU_4_5,
        input_tokens_per_call=15_000,
        output_tokens_per_call=2_500,
        calls_per_month=300,
        llm_coupled=False,
    ),
    "utility": ToolBudget(
        family="Utility (health/env)",
        model=HAIKU_4_5,
        input_tokens_per_call=200,
        output_tokens_per_call=80,
        calls_per_month=4_000,
        llm_coupled=False,
    ),
}


# Tool-name prefix → family key. Order matters: longest prefix first so
# ``ai_act_audit`` resolves to ``ai_act`` before any shorter match.
_PREFIX_TO_FAMILY: Final[tuple[tuple[str, str], ...]] = (
    ("exit_proof", "exit_proof"),
    ("ai_act", "ai_act"),
    ("normalize", "normalize"),
    ("explain", "explain"),
    ("cim", "cim"),
    ("eval", "eval"),
    ("bx_", "bx"),
    ("dx_", "dx"),
)


# Tools that match no prefix fall back to utility (cheap, pandas-only).
_DEFAULT_FAMILY_KEY: Final[str] = "utility"


def budget_for_tool(tool_name: str) -> ToolBudget:
    """Resolve a registered tool name to its modeled token budget.

    Returns a frozen ``ToolBudget`` — callers must not mutate the result.
    """
    for prefix, family_key in _PREFIX_TO_FAMILY:
        if tool_name.startswith(prefix):
            return _FAMILY_BUDGETS[family_key]
    return _FAMILY_BUDGETS[_DEFAULT_FAMILY_KEY]


def all_known_families() -> tuple[str, ...]:
    """Sorted, immutable view of all family keys we model."""
    return tuple(sorted(_FAMILY_BUDGETS.keys()))
