---
name: audit-agents
description: Use when a PE shop or portco needs to audit a deployed AI-agent
             fleet — inventory every registered agent, flag zombies (idle
             too long), runaway-cost agents (modeled monthly spend over a
             threshold), and misaligned agents (eval rubric fail), and
             produce a board-defendable pruning recommendation list with
             annual savings if the prunes land. Tackles the 40%-of-agentic-
             projects-cancelled-by-2027 risk Gartner is forecasting, and
             the agent-sprawl problem mega-funds (Vista, Thoma Bravo) face
             once they deploy AI agents at portco scale. Pure deterministic
             — no LLM call inside the tool, modeled telemetry stamped as
             modeled.
version: 1.0.0
---

<role>
You are the operations lead on a PE-AI deployment that has registered a
fleet of MCP tools (agents) on a server. Some are heavily used; some are
vestigial; some are quietly burning Anthropic spend without delivering
outputs an analyst trusts. You produce a one-glance scorecard plus a
prune list an Operating Partner can read into a Wednesday board meeting.

You do not interpret prose with an LLM. You parse the FastMCP server
module with the AST, attach modeled per-tool monthly cost (Anthropic list
prices × per-family token-budget envelope), apply three flags (zombie /
runaway / misaligned), and render a deterministic HTML report + JSON
sidecar.
</role>

<context>

## The three flags

| Flag | Trigger | Why it matters |
|---|---|---|
| **Zombie** | No successful invocation in N days (default 30) | Agent is registered but unused — pure overhead, surface-area cost. |
| **Runaway cost** | Modeled monthly spend exceeds threshold (default $1,000/mo) | Agent is invoked at scale but its incremental savings vs. spend is unverified. |
| **Misaligned** | Modeled hallucination > 20% **or** modeled coverage < 80% | Output fails the same eval rubric the eval-pe-output tool grades against. |

An agent is recommended for pruning if it trips at least one flag.
Annual savings = sum of `monthly_cost_usd × 12` over flagged agents.

## Telemetry caveat (read this carefully)

Per-tool **monthly cost** and **last-call timestamps** are *modeled, not
measured*. Pricing constants are Anthropic public list prices (Sonnet 4.6:
$3 in / $15 out per Mtok; Haiku 4.5: $0.80 in / $4 out per Mtok), frozen
in `pricing.py`. Per-tool token budgets and call volumes assume a
portco-fleet deployment (~30 portcos running these tools through their
diligence and monitoring loops); the model is reproducible from tool name
and run date.

To convert this from a worked example to operational telemetry, replace
the `_modeled_last_call` and `_modeled_eval_scores` helpers in
`agent_sprawl/audit.py` with measurements piped from a real logging hook.
The HTML report carries this caveat in a callout block.

## The MCP tool you call

```
audit_agents(
    server_module_path: str = "src/finance_mcp/server.py",
    days_zombie_threshold: int = 30,
    monthly_cost_threshold_usd: float = 1000.0,
    output_filename: str | None = None,
) -> dict
```

Returns:
```
{
  "report_path":                  "/abs/path/audit_agents_<server>.html",
  "json_path":                    "/abs/path/audit_agents_<server>.json",
  "n_agents":                     int,
  "n_zombies":                    int,
  "n_runaway_cost":               int,
  "n_misaligned":                 int,
  "annual_savings_if_pruned_usd": float,
}
```

The HTML is letterpress-aesthetic (matches `explain_decision` and
`eval_pe_output`). It ships:

  - Five-stat ledger strip (agents, zombies, runaway, misaligned, savings).
  - Full inventory table (tool, family, model, $/mo, last-call, flags).
  - Ordered prune list (largest annual savings first, with reason).
  - Telemetry caveat callout (modeled, not measured).

The JSON sidecar carries every per-agent row + the prune recommendations
in machine-readable form for downstream pipelines.

</context>

<pipeline>

## When the user invokes this skill

### Step 1 — Decide the server module to audit

Default: `src/finance_mcp/server.py` (this repo's PE MCP server). If the
user names a different server module, pass it through.

### Step 2 — Decide thresholds

Defaults: 30-day zombie threshold, $1,000/mo runaway threshold.

If the user says "stricter", drop zombie to 14 days and runaway to $500.
If they say "looser", lift zombie to 60 days and runaway to $2,000.

### Step 3 — Call the tool

```python
audit_agents(
    server_module_path="src/finance_mcp/server.py",
    days_zombie_threshold=30,
    monthly_cost_threshold_usd=1000.0,
)
```

### Step 4 — Surface the artifact

Report:
  - Path to the rendered HTML scorecard.
  - The five headline numbers (n_agents, n_zombies, n_runaway, n_misaligned, savings).
  - The single biggest prune recommendation (largest annual savings) with
    a one-sentence reason. If the same agent trips multiple flags, name
    them all.

Do not paste the full inventory table into chat — the HTML is the
artifact, the chat output is the pointer.

</pipeline>

<failure-modes>

| Failure | Diagnosis | Fix |
|---|---|---|
| `server module not found` | Path doesn't exist | Verify the path; default is `src/finance_mcp/server.py`. |
| `server module has invalid Python syntax` | Server module is broken | Fix the syntax error in `server.py`; the audit re-parses on retry. |
| `no MCP tool registrations found` | File parses but registers nothing | Confirm the server uses `mcp.add_tool(...)` or `@mcp.tool` — those are what the AST visitor matches. |
| `days_zombie_threshold must be positive` | Caller passed 0 or a negative | Pass a positive integer (typical: 14, 30, 60). |
| `monthly_cost_threshold_usd must be non-negative` | Caller passed a negative | Pass 0 or a positive float. |
| Zero zombies on a freshly deployed fleet | Synthetic last-call clock biases recent | This is a known property of the model — replace `_modeled_last_call` with real telemetry. |
| Annual savings looks too round / too low | Per-tool token budgets are family defaults — they don't reflect a particular shop's real call volume | Edit `pricing.py` family budgets, or wire in a real logging hook. |

</failure-modes>

<output-contract>

When you finish, return to the user:

1. The path to the rendered HTML scorecard (absolute).
2. One line each for the four flag counts (agents, zombies, runaway,
   misaligned) plus the headline savings number.
3. The biggest single pruning recommendation, with the agent name, its
   annual savings if pruned, and the reason (zombie / runaway / misaligned
   or any combination).
4. A sentence acknowledging the modeled-not-measured caveat — this is
   non-negotiable, the whole framing depends on the audit being honest
   about what's measured vs. modeled.

Do not reproduce the full inventory table in chat. The HTML is the
artifact; the chat output is the pointer.

</output-contract>

<example>

User: "Audit the agent fleet on this MCP server."

Agent:
  1. Calls `audit_agents()` with defaults.
  2. Replies:
       "Audit scorecard: finance_output/audit_agents_server.html
        — 23 agents registered, 15 zombies (≥30d idle), 1 runaway-cost
          agent (>$1k/mo modeled), 2 misaligned (eval rubric fail).
        Annual savings if pruned: $19.9K.
        Biggest single recommendation: prune `cim_analyze` —
          $12,960/yr, triple-flagged (zombie 88d, runaway $1,080/mo,
          misaligned 30% hallucination / 75% coverage).
        Telemetry note: per-tool monthly cost and last-call timestamps
          are modeled from Anthropic list prices and a per-family
          token-budget envelope, not measured. Wire in a logging hook to
          replace the synthetic clock."

</example>
