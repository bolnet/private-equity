"""
audit_agents — Inventory every registered PE-MCP tool, flag zombies and
runaway-cost agents, and render a board-defendable pruning recommendation.

Vista, Thoma Bravo, and other mega-funds are deploying agents at portco
scale. Gartner forecasts ~40% of agentic projects will be cancelled by 2027.
The cancellation rarely shows up as a single failure — it's the slow
accumulation of *zombie agents* (still running, no useful output) and
*runaway-cost agents* (LLM bills exceed the savings they deliver).

This tool scans this repo's own MCP tool registrations as a worked example,
attaches a modeled token budget to each (Anthropic public list prices), and
emits a one-glance HTML scorecard plus a JSON sidecar listing every agent
recommended for pruning, with annual savings if the recommendation lands.

Telemetry caveat
----------------
Per-tool cost and last-call timestamps are *modeled, not measured*. A real
deployment replaces the synthetic last-call clock with a measurement piped
from a logging hook. Until then, the math is reproducible-but-modeled and
labelled as such everywhere it is rendered.

Architecture
------------
Pure pandas/Python on the inputs. Deterministic — same inputs → same
report. No LLM call inside the tool. Mirrors ``explainer.explain`` and
``eval.eval`` for visual + structural consistency.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastmcp.exceptions import ToolError

from finance_mcp.agent_sprawl.inventory import (
    RegisteredTool,
    enumerate_registered_tools,
)
from finance_mcp.agent_sprawl.pricing import ToolBudget, budget_for_tool


# --- Constants -------------------------------------------------------------

# Eval-rubric thresholds for the "misaligned agent" signal. An agent is
# misaligned if its modeled hallucination rate exceeds this OR its modeled
# coverage falls below this. Numbers chosen to match the eval module's
# good/warn/bad bands.
_MISALIGNED_HALLUCINATION_RATE: float = 0.20
_MISALIGNED_COVERAGE_FLOOR: float = 0.80

# Months/year for run-rate math.
_MONTHS_PER_YEAR: int = 12

# Synthetic last-call clock — deterministic per tool name. We hash the tool
# name to a stable [0, 90)-day offset so the same audit run twice produces
# the same timestamps without any real telemetry.
_MAX_SYNTHETIC_LAST_CALL_DAYS: int = 90


# --- Frozen result rows ----------------------------------------------------


@dataclass(frozen=True)
class AgentRow:
    """One agent in the inventory, fully scored."""

    name: str
    family: str
    model_id: str
    llm_coupled: bool
    monthly_cost_usd: float
    last_call_iso: str
    days_since_last_call: int
    modeled_hallucination_rate: float
    modeled_coverage: float
    is_zombie: bool
    is_runaway_cost: bool
    is_misaligned: bool
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def annual_cost_usd(self) -> float:
        return self.monthly_cost_usd * _MONTHS_PER_YEAR

    @property
    def recommend_prune(self) -> bool:
        """Prune any agent that trips at least one signal."""
        return self.is_zombie or self.is_runaway_cost or self.is_misaligned


# --- Synthetic-but-deterministic telemetry helpers -------------------------


def _stable_hash_offset(name: str, modulus: int) -> int:
    """Map a tool name → [0, modulus) deterministically (BLAKE2-128)."""
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % modulus


def _modeled_last_call(name: str, today: date) -> tuple[str, int]:
    """Synthetic last-call timestamp + days-since for a tool.

    Deterministic in ``name`` and ``today``. A real deployment replaces this
    with a measurement piped from a logging hook.
    """
    days_back = _stable_hash_offset(name, _MAX_SYNTHETIC_LAST_CALL_DAYS)
    last_call = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=days_back)
    return last_call.isoformat(), days_back


def _modeled_eval_scores(name: str, llm_coupled: bool) -> tuple[float, float]:
    """Synthetic (hallucination_rate, coverage) for a tool.

    Pure-pandas tools (DX, BX, eval, normalize) deterministically score
    near-zero hallucination + perfect coverage. LLM-coupled tools draw a
    plausible spread tied to the tool name hash so a few flag as misaligned
    on every run — a stand-in for what real eval telemetry would surface.
    """
    if not llm_coupled:
        return 0.0, 1.0

    # Spread hallucination over [0, 0.32] and coverage over [0.70, 1.00].
    h_offset = _stable_hash_offset(name + ":hallucination", 33)
    c_offset = _stable_hash_offset(name + ":coverage", 31)
    hallucination = h_offset / 100.0
    coverage = 0.70 + c_offset / 100.0
    return round(hallucination, 4), round(min(coverage, 1.0), 4)


# --- Scoring core ----------------------------------------------------------


def _score_one_tool(
    tool: RegisteredTool,
    *,
    today: date,
    days_zombie_threshold: int,
    monthly_cost_threshold_usd: float,
) -> AgentRow:
    """Attach budget + telemetry + flags to one registered tool."""
    budget: ToolBudget = budget_for_tool(tool.name)
    monthly_cost = round(budget.monthly_cost_usd(), 2)
    last_call_iso, days_since = _modeled_last_call(tool.name, today)
    hallucination, coverage = _modeled_eval_scores(tool.name, budget.llm_coupled)

    is_zombie = days_since >= days_zombie_threshold
    is_runaway = monthly_cost > monthly_cost_threshold_usd
    is_misaligned = (
        hallucination > _MISALIGNED_HALLUCINATION_RATE
        or coverage < _MISALIGNED_COVERAGE_FLOOR
    )

    flags: list[str] = []
    if is_zombie:
        flags.append(f"zombie ({days_since}d since last call)")
    if is_runaway:
        flags.append(f"runaway cost (${monthly_cost:,.0f}/mo)")
    if is_misaligned:
        flags.append(
            f"misaligned (hallucination {hallucination:.0%}, coverage {coverage:.0%})"
        )

    return AgentRow(
        name=tool.name,
        family=budget.family,
        model_id=budget.model.model_id,
        llm_coupled=budget.llm_coupled,
        monthly_cost_usd=monthly_cost,
        last_call_iso=last_call_iso,
        days_since_last_call=days_since,
        modeled_hallucination_rate=hallucination,
        modeled_coverage=coverage,
        is_zombie=is_zombie,
        is_runaway_cost=is_runaway,
        is_misaligned=is_misaligned,
        flags=tuple(flags),
    )


# --- HTML report -----------------------------------------------------------


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Agent-sprawl audit — {server_label}</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Newsreader:ital,wght@0,300;1,300&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet" />
<style>
  :root {{
    --paper:    #f4ecd5;
    --page:     #fbf6e2;
    --ink:      #1a140d;
    --ink-dim:  #5a4a35;
    --ink-faint:#8b765a;
    --rule:     #c2ad84;
    --rule-soft:#dfd2af;
    --accent:   #6b1414;
    --accent-2: #93331f;
    --gold:     #8a6f1a;
    --max:      960px;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  html {{ background: #ece4cb; }}
  body {{
    background:
      radial-gradient(ellipse 1200px 800px at 50% -100px, rgba(255,248,220,0.6), transparent 70%),
      radial-gradient(ellipse 600px 400px at 80% 120%, rgba(107,20,20,0.04), transparent 70%),
      var(--paper);
    color: var(--ink);
    font-family: 'EB Garamond', 'Iowan Old Style', Georgia, serif;
    font-size: 17px;
    line-height: 1.6;
    font-feature-settings: "liga", "dlig", "onum", "kern";
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    padding: 64px 20px 96px;
  }}
  body::before {{
    content: '';
    position: fixed; inset: 0;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.42  0 0 0 0 0.34  0 0 0 0 0.18  0 0 0 0.07 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
    opacity: 0.32; pointer-events: none; z-index: 0; mix-blend-mode: multiply;
  }}
  .sheet {{
    max-width: var(--max);
    margin: 0 auto;
    background: var(--page);
    position: relative; z-index: 1;
    padding: 80px 72px 64px;
    box-shadow:
      0 1px 0 var(--rule-soft),
      0 30px 60px -30px rgba(60, 40, 15, 0.18),
      0 8px 18px -6px rgba(60, 40, 15, 0.08);
    border: 1px solid rgba(194, 173, 132, 0.45);
  }}

  .letterhead {{
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 24px; margin-bottom: 48px;
  }}
  .wordmark {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-style: italic;
    font-size: 22px; letter-spacing: 0.01em;
    color: var(--ink); display: flex; align-items: center; gap: 14px;
  }}
  .wordmark .seal {{
    width: 36px; height: 36px; border-radius: 50%;
    border: 1px solid var(--accent); color: var(--accent);
    display: inline-flex; align-items: center; justify-content: center;
    font-family: 'Cormorant Garamond', serif;
    font-style: italic; font-size: 18px; font-weight: 500;
    background: rgba(107, 20, 20, 0.04);
  }}
  .letterhead-meta {{
    text-align: right;
    font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-weight: 300;
    font-size: 13px; line-height: 1.55; color: var(--ink-faint);
    letter-spacing: 0.02em;
  }}
  .letterhead-meta strong {{
    display: block; color: var(--ink-dim); font-style: normal; font-weight: 500;
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    margin-bottom: 2px;
  }}

  .eyebrow {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px;
    color: var(--accent); font-weight: 600; margin-bottom: 14px;
    display: inline-block;
  }}
  .eyebrow::before {{ content: '— '; color: var(--rule); }}
  .eyebrow::after  {{ content: ' —'; color: var(--rule); }}
  h1 {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 400; font-size: 44px; line-height: 1.06;
    letter-spacing: -0.005em; margin: 0 0 16px; color: var(--ink);
  }}
  h1 em {{ font-style: italic; color: var(--accent); font-weight: 500; }}
  .lede {{
    font-family: 'EB Garamond', serif;
    font-style: italic; color: var(--ink-dim);
    font-size: 19px; line-height: 1.55;
    margin: 0 0 24px; max-width: 60ch;
  }}

  .stats-strip {{
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 0; margin: 32px 0 40px;
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
    padding: 18px 0;
  }}
  .stats-strip .stat {{
    text-align: center; padding: 0 14px;
    border-right: 1px solid var(--rule-soft);
  }}
  .stats-strip .stat:last-child {{ border-right: none; }}
  .stats-strip .stat-label {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    color: var(--ink-faint); font-weight: 500; margin-bottom: 6px;
  }}
  .stats-strip .stat-num {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 24px; line-height: 1; color: var(--ink);
    font-feature-settings: "lnum";
  }}
  .stats-strip .stat-num.bad {{ color: var(--accent); }}
  .stats-strip .stat-sub {{
    font-family: 'Newsreader', serif; font-style: italic; font-weight: 300;
    font-size: 11px; color: var(--ink-faint); margin-top: 4px;
  }}

  h2 {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 26px; line-height: 1.2;
    margin: 44px 0 8px; color: var(--ink);
  }}
  h2 em {{ font-style: italic; color: var(--accent); }}
  .h2-sub {{
    font-family: 'Newsreader', serif; font-style: italic; font-weight: 300;
    font-size: 14px; color: var(--ink-faint); margin: 0 0 18px;
  }}

  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 14px; }}
  th, td {{
    text-align: left; padding: 9px 10px;
    border-bottom: 1px solid var(--rule-soft);
    font-variant-numeric: tabular-nums;
  }}
  th {{
    font-variant: small-caps; letter-spacing: 0.12em; font-size: 11px;
    color: var(--ink-faint); font-weight: 600; border-bottom: 1px solid var(--rule);
  }}
  td.name {{ font-family: 'JetBrains Mono', monospace; font-size: 12.5px; color: var(--ink); }}
  td.num  {{ text-align: right; }}
  tr.flagged td {{ background: rgba(107, 20, 20, 0.05); }}
  tr.flagged td.name {{ color: var(--accent); font-weight: 600; }}

  .pill {{
    display: inline-block; padding: 1px 7px; border-radius: 10px;
    font-variant: small-caps; letter-spacing: 0.1em; font-size: 10px;
    font-weight: 600; margin-right: 4px;
  }}
  .pill.zombie  {{ background: rgba(107, 20, 20, 0.10); color: var(--accent); }}
  .pill.runaway {{ background: rgba(138, 111, 26, 0.18); color: var(--gold); }}
  .pill.misaligned {{ background: rgba(147, 51, 31, 0.15); color: var(--accent-2); }}
  .pill.healthy {{ background: rgba(76, 174, 139, 0.18); color: #2d6f53; }}

  .prune-list {{ margin: 0; padding: 0; list-style: none; }}
  .prune-list li {{
    border-top: 1px dashed var(--rule);
    padding: 12px 0;
  }}
  .prune-list li:first-child {{ border-top: none; }}
  .prune-list .name {{
    font-family: 'JetBrains Mono', monospace; font-size: 13px;
    color: var(--accent); font-weight: 600;
  }}
  .prune-list .savings {{
    font-family: 'Cormorant Garamond', serif; font-weight: 500;
    font-size: 17px; color: var(--ink); float: right;
  }}
  .prune-list .reason {{
    font-style: italic; color: var(--ink-dim); font-size: 14px;
    margin-top: 4px;
  }}

  .colophon {{
    margin-top: 56px; padding-top: 18px;
    border-top: 1px solid var(--rule);
    font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-weight: 300;
    font-size: 12px; line-height: 1.6; color: var(--ink-faint);
    text-align: center;
  }}
  .colophon code {{
    font-family: 'JetBrains Mono', monospace; font-style: normal;
    font-size: 11px; color: var(--ink-dim);
    background: rgba(194, 173, 132, 0.18);
    padding: 1px 6px; border-radius: 2px;
  }}
  .colophon .signature {{
    font-family: 'Cormorant Garamond', serif; font-style: italic;
    font-size: 16px; color: var(--ink-dim); margin-bottom: 12px;
  }}
  .colophon .signature::before {{
    content: ''; display: block; width: 120px; height: 1px;
    background: var(--ink-faint); opacity: 0.5; margin: 0 auto 10px;
  }}
  .caveat {{
    background: rgba(138, 111, 26, 0.10);
    border-left: 3px solid var(--gold);
    padding: 12px 16px; margin: 20px 0;
    font-family: 'Newsreader', serif; font-style: italic;
    font-size: 13.5px; color: var(--ink-dim); line-height: 1.55;
  }}

  @media print {{
    html, body {{ background: white; }}
    body::before {{ display: none; }}
    .sheet {{ box-shadow: none; border: none; padding: 24mm; max-width: none; }}
  }}
  @media (max-width: 720px) {{
    body {{ padding: 24px 8px 64px; font-size: 16px; }}
    .sheet {{ padding: 40px 24px 48px; }}
    h1 {{ font-size: 32px; }}
    .stats-strip {{ grid-template-columns: repeat(2, 1fr); gap: 12px; padding: 12px 0; }}
    .stats-strip .stat {{ border-right: none; border-bottom: 1px dotted var(--rule-soft); padding-bottom: 10px; }}
    .letterhead {{ flex-direction: column; gap: 8px; }}
    .letterhead-meta {{ text-align: left; }}
    table {{ font-size: 12.5px; }}
    th, td {{ padding: 6px 6px; }}
  }}
</style>
</head>
<body>
<article class="sheet">
  <header class="letterhead">
    <div class="wordmark">
      <span class="seal">&para;</span>
      <span>Private Equity &times; AI</span>
    </div>
    <div class="letterhead-meta">
      <strong>Agent-sprawl audit</strong>
      In re: <code>{server_label}</code><br/>
      {as_of} &middot; modeled telemetry
    </div>
  </header>

  <div class="eyebrow">An inventory of the running agent fleet</div>
  <h1>{n_agents} agents registered, <em>{n_flagged} flagged for pruning.</em></h1>
  <p class="lede">{lede}</p>

  <div class="stats-strip">
    <div class="stat">
      <div class="stat-label">Agents</div>
      <div class="stat-num">{n_agents}</div>
      <div class="stat-sub">registered</div>
    </div>
    <div class="stat">
      <div class="stat-label">Zombies</div>
      <div class="stat-num bad">{n_zombies}</div>
      <div class="stat-sub">≥ {days_zombie_threshold}d idle</div>
    </div>
    <div class="stat">
      <div class="stat-label">Runaway cost</div>
      <div class="stat-num bad">{n_runaway}</div>
      <div class="stat-sub">&gt; ${monthly_cost_threshold:,.0f}/mo</div>
    </div>
    <div class="stat">
      <div class="stat-label">Misaligned</div>
      <div class="stat-num bad">{n_misaligned}</div>
      <div class="stat-sub">eval rubric fail</div>
    </div>
    <div class="stat">
      <div class="stat-label">Annual savings</div>
      <div class="stat-num">{annual_savings_scaled}</div>
      <div class="stat-sub">if pruned</div>
    </div>
  </div>

  <div class="caveat">
    <strong>Telemetry caveat.</strong> Per-tool monthly cost and last-call
    timestamps are <em>modeled, not measured</em>. Costs flow from public
    Anthropic list prices ({sonnet_in_price}/{sonnet_out_price} per Mtok
    Sonnet 4.6, {haiku_in_price}/{haiku_out_price} per Mtok Haiku 4.5) times
    a per-tool token-budget envelope keyed on tool family. A real
    deployment replaces the synthetic clock with measurements piped from a
    logging hook; the schema below is the same.
  </div>

  <h2>Inventory</h2>
  <p class="h2-sub">Every agent registered against the server, with its model, modeled monthly cost, and modeled last-call.</p>
  <table>
    <thead>
      <tr>
        <th>Tool</th>
        <th>Family</th>
        <th>Model</th>
        <th class="num">$/mo</th>
        <th>Last call</th>
        <th class="num">Idle (d)</th>
        <th>Flags</th>
      </tr>
    </thead>
    <tbody>
      {inventory_rows}
    </tbody>
  </table>

  <h2>Pruning recommendations <em>— {n_flagged} agents</em></h2>
  <p class="h2-sub">Annual savings if every flagged agent is decommissioned: <strong>{annual_savings_scaled}</strong>.</p>
  {prune_section}

  <footer class="colophon">
    <div class="signature">Composed at the agent-fleet layer.</div>
    Generated by <code>audit_agents</code>. Pricing constants and per-tool
    token budgets frozen in <code>agent_sprawl/pricing.py</code>; modeled
    telemetry is reproducible from tool name and run date.<br/>
    Server inspected: <code>{server_path}</code> &middot; {as_of}.
  </footer>
</article>
</body>
</html>
"""


_INVENTORY_ROW = """\
      <tr class="{row_class}">
        <td class="name">{name}</td>
        <td>{family}</td>
        <td><code>{model_id}</code></td>
        <td class="num">${monthly_cost:,.2f}</td>
        <td>{last_call_date}</td>
        <td class="num">{days_since}</td>
        <td>{pills}</td>
      </tr>"""


_PRUNE_ITEM = """\
      <li>
        <span class="savings">{annual_scaled} / yr</span>
        <span class="name">{name}</span>
        <div class="reason">{reason}</div>
      </li>"""


# --- Formatters ------------------------------------------------------------


def _scaled_usd(usd: float) -> str:
    """Render a dollar amount at the right precision (matches explainer)."""
    if abs(usd) >= 1e9:
        return f"${usd / 1e9:,.2f}B"
    if abs(usd) >= 1e6:
        return f"${usd / 1e6:,.2f}M"
    if abs(usd) >= 1e3:
        return f"${usd / 1e3:,.1f}K"
    return f"${usd:,.0f}"


def _flag_pills(row: AgentRow) -> str:
    if not row.recommend_prune:
        return '<span class="pill healthy">healthy</span>'
    pills: list[str] = []
    if row.is_zombie:
        pills.append('<span class="pill zombie">zombie</span>')
    if row.is_runaway_cost:
        pills.append('<span class="pill runaway">runaway</span>')
    if row.is_misaligned:
        pills.append('<span class="pill misaligned">misaligned</span>')
    return "".join(pills)


def _prune_reason(row: AgentRow) -> str:
    """Plain-English reason a row is on the prune list."""
    parts: list[str] = []
    if row.is_zombie:
        parts.append(
            f"no successful invocation in {row.days_since_last_call} days "
            f"(modeled)"
        )
    if row.is_runaway_cost:
        parts.append(
            f"modeled monthly cost ${row.monthly_cost_usd:,.0f} exceeds threshold"
        )
    if row.is_misaligned:
        parts.append(
            f"eval rubric fails — hallucination "
            f"{row.modeled_hallucination_rate:.0%}, coverage "
            f"{row.modeled_coverage:.0%}"
        )
    return "; ".join(parts) if parts else "—"


# --- Public entry point ----------------------------------------------------


def audit_agents(
    server_module_path: str = "src/finance_mcp/server.py",
    days_zombie_threshold: int = 30,
    monthly_cost_threshold_usd: float = 1000.0,
    output_filename: str | None = None,
) -> dict:
    """Inventory registered MCP tools, flag sprawl, and recommend prunes.

    Treats every ``mcp.add_tool(...)`` call and ``@mcp.tool`` decorator in
    ``server.py`` as one agent in the fleet. Attaches a modeled monthly
    cost (Anthropic list prices × per-family token budget) and a
    deterministic synthetic last-call timestamp to each. Flags zombies
    (idle ≥ ``days_zombie_threshold``), runaway-cost agents (modeled cost
    above ``monthly_cost_threshold_usd``), and misaligned agents (modeled
    eval rubric fail) and writes a board-defendable HTML scorecard plus a
    JSON sidecar listing every agent recommended for pruning.

    **Telemetry is modeled, not measured.** Replace ``_modeled_last_call``
    and ``_modeled_eval_scores`` with measurements piped from a real
    logging hook to convert this from a worked example into operational
    telemetry; the rest of the pipeline stays the same.

    Args:
        server_module_path: Path to the FastMCP server module (read-only).
            Default: ``src/finance_mcp/server.py``.
        days_zombie_threshold: An agent with no successful invocation in
            this many days is flagged as a zombie. Default: 30.
        monthly_cost_threshold_usd: An agent whose modeled monthly cost
            exceeds this is flagged runaway. Default: 1000.
        output_filename: Optional HTML basename. Defaults to
            ``audit_agents_<server-stem>.html``.

    Returns:
        Dict with keys: ``report_path``, ``json_path``, ``n_agents``,
        ``n_zombies``, ``n_runaway_cost``, ``n_misaligned``,
        ``annual_savings_if_pruned_usd``.

    Raises:
        ToolError: if the server module is missing, malformed, or
            registers no tools, or if any threshold argument is invalid.
    """
    if days_zombie_threshold <= 0:
        raise ToolError(
            f"days_zombie_threshold must be positive (got {days_zombie_threshold})"
        )
    if monthly_cost_threshold_usd < 0:
        raise ToolError(
            f"monthly_cost_threshold_usd must be non-negative "
            f"(got {monthly_cost_threshold_usd})"
        )

    server_path = Path(server_module_path).resolve()
    tools = enumerate_registered_tools(server_path)
    today = date.today()

    rows = tuple(
        _score_one_tool(
            tool=t,
            today=today,
            days_zombie_threshold=days_zombie_threshold,
            monthly_cost_threshold_usd=monthly_cost_threshold_usd,
        )
        for t in tools
    )

    n_agents = len(rows)
    n_zombies = sum(1 for r in rows if r.is_zombie)
    n_runaway = sum(1 for r in rows if r.is_runaway_cost)
    n_misaligned = sum(1 for r in rows if r.is_misaligned)
    flagged_rows = tuple(r for r in rows if r.recommend_prune)
    n_flagged = len(flagged_rows)

    annual_savings = round(
        sum(r.annual_cost_usd for r in flagged_rows),
        2,
    )

    # --- Render HTML ------------------------------------------------------
    inventory_rows_html = "\n".join(
        _INVENTORY_ROW.format(
            row_class="flagged" if r.recommend_prune else "",
            name=r.name,
            family=r.family,
            model_id=r.model_id,
            monthly_cost=r.monthly_cost_usd,
            last_call_date=r.last_call_iso[:10],
            days_since=r.days_since_last_call,
            pills=_flag_pills(r),
        )
        for r in rows
    )

    if flagged_rows:
        # Order the prune list by largest annual savings first.
        ordered = sorted(flagged_rows, key=lambda r: r.annual_cost_usd, reverse=True)
        prune_section = (
            '<ul class="prune-list">'
            + "\n".join(
                _PRUNE_ITEM.format(
                    name=r.name,
                    annual_scaled=_scaled_usd(r.annual_cost_usd),
                    reason=_prune_reason(r),
                )
                for r in ordered
            )
            + "</ul>"
        )
    else:
        prune_section = (
            '<p class="lede">No agents flagged for pruning. Fleet is healthy '
            "against current thresholds.</p>"
        )

    server_label = server_path.stem
    lede = (
        f"A pass over the registered fleet against a {days_zombie_threshold}-day "
        f"idle threshold and a ${monthly_cost_threshold_usd:,.0f}/mo cost ceiling. "
        f"Pricing flows from Anthropic public list prices; telemetry is modeled."
    )

    html = _HTML_TEMPLATE.format(
        server_label=server_label,
        as_of=today.isoformat(),
        n_agents=n_agents,
        n_flagged=n_flagged,
        n_zombies=n_zombies,
        n_runaway=n_runaway,
        n_misaligned=n_misaligned,
        days_zombie_threshold=days_zombie_threshold,
        monthly_cost_threshold=monthly_cost_threshold_usd,
        annual_savings_scaled=_scaled_usd(annual_savings),
        sonnet_in_price="$3",
        sonnet_out_price="$15",
        haiku_in_price="$0.80",
        haiku_out_price="$4",
        lede=lede,
        inventory_rows=inventory_rows_html,
        prune_section=prune_section,
        server_path=str(server_path),
    )

    # --- Write artifacts --------------------------------------------------
    out_dir = Path(os.path.join("finance_output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = output_filename or f"audit_agents_{server_label}.html"
    out_path = out_dir / out_name
    out_path.write_text(html, encoding="utf-8")

    json_out_path = out_path.with_suffix(".json")
    payload = {
        "as_of": today.isoformat(),
        "server_path": str(server_path),
        "thresholds": {
            "days_zombie_threshold": days_zombie_threshold,
            "monthly_cost_threshold_usd": monthly_cost_threshold_usd,
            "misaligned_hallucination_rate": _MISALIGNED_HALLUCINATION_RATE,
            "misaligned_coverage_floor": _MISALIGNED_COVERAGE_FLOOR,
        },
        "telemetry_note": (
            "Per-tool monthly cost and last-call timestamps are modeled, "
            "not measured. Replace _modeled_last_call and "
            "_modeled_eval_scores in agent_sprawl/audit.py with logging-hook "
            "measurements to convert to operational telemetry."
        ),
        "totals": {
            "n_agents": n_agents,
            "n_zombies": n_zombies,
            "n_runaway_cost": n_runaway,
            "n_misaligned": n_misaligned,
            "n_flagged_for_prune": n_flagged,
            "annual_savings_if_pruned_usd": annual_savings,
        },
        "inventory": [
            {
                "name": r.name,
                "family": r.family,
                "model_id": r.model_id,
                "llm_coupled": r.llm_coupled,
                "monthly_cost_usd": r.monthly_cost_usd,
                "annual_cost_usd": round(r.annual_cost_usd, 2),
                "last_call": r.last_call_iso,
                "days_since_last_call": r.days_since_last_call,
                "modeled_hallucination_rate": r.modeled_hallucination_rate,
                "modeled_coverage": r.modeled_coverage,
                "is_zombie": r.is_zombie,
                "is_runaway_cost": r.is_runaway_cost,
                "is_misaligned": r.is_misaligned,
                "flags": list(r.flags),
                "recommend_prune": r.recommend_prune,
            }
            for r in rows
        ],
        "prune_recommendations": [
            {
                "name": r.name,
                "annual_savings_usd": round(r.annual_cost_usd, 2),
                "reason": _prune_reason(r),
            }
            for r in sorted(
                flagged_rows, key=lambda r: r.annual_cost_usd, reverse=True
            )
        ],
        "report_path": str(out_path),
    }
    json_out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "report_path": str(out_path),
        "json_path": str(json_out_path),
        "n_agents": n_agents,
        "n_zombies": n_zombies,
        "n_runaway_cost": n_runaway,
        "n_misaligned": n_misaligned,
        "annual_savings_if_pruned_usd": annual_savings,
    }
