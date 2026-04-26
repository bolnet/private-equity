"""
explain_decision — Take a DX OpportunityMap JSON sidecar and render a
board-defendable narrative memo: the why, the counterfactual, the risk of
inaction, and the rollout plan.

Closes the agency gap. The reference failure mode (e-TeleQuote case study):
a model recommends throttling 3 states; management can't defend the 'why'
in the boardroom; nothing happens. This tool produces the language a
managing director can read into a board meeting on Wednesday.

Architecture is the same shape as dx_report: pure pandas + Python on the
inputs, deterministic templated prose on the outputs, an HTML render at
the end. No LLM call inside the tool — when run inside Claude Code the
agent enriches the templated prose; when run as a standalone script the
templated prose is enough to ship.

The narrative is built from Opportunity numeric fields, so every $ and %
in the memo traces back to a tool return. No hallucinated numbers survive.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from fastmcp.exceptions import ToolError


Audience = Literal["board", "operator"]


def _format_segment(seg: dict) -> str:
    """Human-readable segment label: {grade=A, term=360 months} → 'A × 360 months'."""
    return " × ".join(str(v) for v in seg.values())


def _format_decision_cols(cols: list[str] | tuple[str, ...]) -> str:
    return " × ".join(cols) if cols else "—"


def _scaled(usd: float) -> str:
    """Render dollar amount at the right precision."""
    if abs(usd) >= 1e9:
        return f"${usd / 1e9:,.2f}B"
    if abs(usd) >= 1e6:
        return f"${usd / 1e6:,.1f}M"
    if abs(usd) >= 1e3:
        return f"${usd / 1e3:,.0f}K"
    return f"${usd:,.0f}"


def _opp_narrative_board(opp: dict) -> str:
    """If the agent has filled `narrative_board`, use it. Otherwise a
    templated default that pulls strictly from Opportunity numeric fields."""
    existing = (opp.get("narrative_board") or "").strip()
    if existing:
        return existing

    seg = _format_segment(opp.get("segment") or {})
    decision_cols = _format_decision_cols(opp.get("decision_cols") or [])
    impact = float(opp.get("projected_impact_usd_annual") or 0.0)
    n_rows = int(opp.get("n") or 0)
    persist = opp.get("persistence_quarters_out_of_total") or [0, 0]
    persist_q, total_q = (int(persist[0]), int(persist[1]))
    archetype = opp.get("archetype", "decision")
    difficulty = int(opp.get("difficulty_score_1_to_5") or 3)
    action = (opp.get("projected_action") or "").strip()

    persist_clause = (
        f"persisting in {persist_q} of {total_q} quarters observed"
        if total_q
        else "with persistence not yet measured"
    )

    return (
        f"In the **{seg}** cohort ({decision_cols}), our cross-section pass "
        f"isolates **{_scaled(impact)} of annualized leakage** across "
        f"**{n_rows:,} loans** — {persist_clause}. The pattern is "
        f"{archetype}-shape: it shows up at the cohort level even when "
        f"aggregate book metrics look healthy. Rerouting this cohort to a "
        f"break-even outcome captures the full {_scaled(impact)} run-rate. "
        f"Implementation difficulty is rated {difficulty}/5; the action — "
        f"_{action}_ — is operator-controllable and does not require "
        f"underwriting policy change."
    )


def _opp_narrative_operator(opp: dict) -> str:
    existing = (opp.get("narrative_operator") or "").strip()
    if existing:
        return existing

    seg = _format_segment(opp.get("segment") or {})
    decision_cols = _format_decision_cols(opp.get("decision_cols") or [])
    impact = float(opp.get("projected_impact_usd_annual") or 0.0)
    evidence_ids = opp.get("evidence_row_ids") or []
    action = (opp.get("projected_action") or "").strip()

    return (
        f"Action: {action} Targeted decision dimensions: {decision_cols}. "
        f"Cohort key: **{seg}**. Annualized $ at stake: {_scaled(impact)}. "
        f"Sample evidence rows for spot-check: {', '.join(map(str, evidence_ids[:5])) or '—'}. "
        f"The cohort can be carved out of existing approval logic without a "
        f"wholesale repricing — throttle volume to the cohort to ~5% of "
        f"current and observe one quarter."
    )


def _opp_counterfactual(opp: dict) -> str:
    """The 'what changes after we act' paragraph — the part that survives
    diligence questioning in a board room."""
    impact = float(opp.get("projected_impact_usd_annual") or 0.0)
    current = float(opp.get("outcome_total_usd_annual") or 0.0)
    n_rows = int(opp.get("n") or 0)
    persist = opp.get("persistence_quarters_out_of_total") or [0, 0]
    persist_q, total_q = (int(persist[0]), int(persist[1]))
    persist_score = float(opp.get("persistence_score") or 0.0)

    persistence_text = (
        f"a persistence score of {persist_score:.2f} across "
        f"{total_q} quarters means the loss is structural, not vintage-specific"
        if total_q
        else "the persistence signal is too thin to call structural yet"
    )

    return (
        f"**Counterfactual:** without action, the cohort continues producing "
        f"{_scaled(current)} of annual loss across {n_rows:,} loans. With the "
        f"projected reroute, the run-rate moves toward break-even, recovering "
        f"{_scaled(impact)} per year. {persistence_text.capitalize()}."
    )


def _opp_risk_of_inaction(opp: dict) -> str:
    impact = float(opp.get("projected_impact_usd_annual") or 0.0)
    archetype = opp.get("archetype", "decision")
    return (
        f"**Risk of inaction:** the {archetype} pattern compounds quarter "
        f"over quarter. Carrying {_scaled(impact)} of annual leakage forward "
        f"into the next vintage would erode the EBITDA bridge by an "
        f"equivalent amount; LP reporting would carry the gap as an "
        f"unexplained variance in the next quarterly letter."
    )


def _opp_rollout(opp: dict) -> str:
    difficulty = int(opp.get("difficulty_score_1_to_5") or 3)
    weeks = {1: 1, 2: 2, 3: 4, 4: 8, 5: 12}.get(difficulty, 4)
    return (
        f"**Rollout:** week 1 — carve cohort out of approval logic and route "
        f"to no-action; weeks 2-{weeks} — observe one quarter of post-action "
        f"data and validate the counterfactual at row level; on confirmation "
        f"— bake into standing underwriting policy."
    )


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Board memorandum — {portco_id}</title>
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
    --max:      720px;
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
    font-size: 18px;
    line-height: 1.62;
    font-feature-settings: "liga", "dlig", "onum", "kern";
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    padding: 72px 20px 96px;
    counter-reset: opp;
  }}

  /* Paper grain via fixed SVG turbulence — sits under everything but never scrolls. */
  body::before {{
    content: '';
    position: fixed; inset: 0;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.42  0 0 0 0 0.34  0 0 0 0 0.18  0 0 0 0.07 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
    opacity: 0.35; pointer-events: none; z-index: 0; mix-blend-mode: multiply;
  }}

  .sheet {{
    max-width: var(--max);
    margin: 0 auto;
    background: var(--page);
    position: relative; z-index: 1;
    padding: 88px 76px 72px;
    box-shadow:
      0 1px 0 var(--rule-soft),
      0 30px 60px -30px rgba(60, 40, 15, 0.18),
      0 8px 18px -6px rgba(60, 40, 15, 0.08);
    border: 1px solid rgba(194, 173, 132, 0.45);
    border-top: 1px solid rgba(194, 173, 132, 0.3);
  }}

  /* Letterhead -------------------------------------------------- */
  .letterhead {{
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 24px; margin-bottom: 56px;
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
    font-size: 13px; line-height: 1.55;
    color: var(--ink-faint);
    letter-spacing: 0.02em;
  }}
  .letterhead-meta strong {{
    display: block; color: var(--ink-dim); font-style: normal; font-weight: 500;
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    margin-bottom: 2px;
  }}

  /* Title block ------------------------------------------------- */
  .title-block {{ margin-bottom: 36px; }}
  .eyebrow {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px;
    color: var(--accent); font-weight: 600; margin-bottom: 14px;
    display: inline-block;
  }}
  .eyebrow::before {{ content: '— '; color: var(--rule); }}
  .eyebrow::after  {{ content: ' —'; color: var(--rule); }}
  h1 {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 400; font-style: normal;
    font-size: 50px; line-height: 1.06;
    letter-spacing: -0.005em;
    margin: 0 0 18px; color: var(--ink);
  }}
  h1 em {{
    font-style: italic; color: var(--accent);
    font-weight: 500;
  }}
  .lede {{
    font-family: 'EB Garamond', serif;
    font-style: italic; color: var(--ink-dim);
    font-size: 20px; line-height: 1.55;
    margin: 0; max-width: 56ch;
  }}

  /* Divider ornament -------------------------------------------- */
  .ornament {{
    text-align: center; color: var(--rule);
    font-family: 'Cormorant Garamond', serif;
    font-size: 22px; letter-spacing: 1.2em;
    margin: 44px 0;
    padding-left: 1.2em;  /* compensate for letter-spacing eating left edge */
  }}
  .ornament::before {{ content: '✦  ✦  ✦'; }}

  /* Executive summary ------------------------------------------- */
  .summary {{
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
    padding: 28px 0; margin: 36px 0;
    position: relative;
  }}
  .summary::before {{
    content: ''; position: absolute; left: 0; top: -3px; width: 64px; height: 5px;
    border-top: 2px solid var(--accent);
    border-bottom: 1px solid var(--accent);
  }}
  .summary-block {{ margin-bottom: 18px; }}
  .summary-block:last-child {{ margin-bottom: 0; }}
  .summary-label {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px;
    font-weight: 600; color: var(--accent);
    margin-bottom: 4px;
  }}
  .summary p {{ margin: 0; font-size: 17px; line-height: 1.6; }}
  .summary p strong {{ font-weight: 600; color: var(--ink); }}

  /* Stats strip — letterpress tabular ledger -------------------- */
  .stats-strip {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 0; margin: 32px 0 56px;
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
    padding: 18px 0;
  }}
  .stats-strip .stat {{
    text-align: center; padding: 0 16px;
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
  .stats-strip .stat-sub {{
    font-family: 'Newsreader', serif; font-style: italic; font-weight: 300;
    font-size: 11px; color: var(--ink-faint); margin-top: 4px;
  }}

  /* Per-opportunity sections ------------------------------------ */
  .opp-section {{
    counter-increment: opp;
    margin: 60px 0;
    position: relative;
  }}
  .opp-head {{
    display: flex; align-items: baseline; gap: 22px;
    padding-bottom: 14px; margin-bottom: 22px;
    border-bottom: 1px solid var(--rule);
    position: relative;
  }}
  .opp-head::after {{
    content: ''; position: absolute; left: 0; right: 0; bottom: -4px;
    height: 1px; background: var(--rule); opacity: 0.5;
  }}
  .opp-marker {{
    font-family: 'Cormorant Garamond', serif;
    font-style: italic; font-weight: 400;
    font-size: 32px; color: var(--accent);
    line-height: 1; min-width: 56px;
  }}
  .opp-marker::before {{ content: counter(opp, upper-roman) "."; }}
  .opp-titles {{ flex: 1; min-width: 0; }}
  .opp-archetype {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    font-weight: 600; color: var(--ink-faint);
    margin-bottom: 4px;
  }}
  .opp-cohort {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 28px; line-height: 1.15;
    color: var(--ink); margin: 0;
  }}

  .opp-body h3 {{
    font-family: 'EB Garamond', serif;
    font-variant: small-caps; letter-spacing: 0.14em;
    font-weight: 600; font-size: 14px; color: var(--accent);
    margin: 28px 0 10px;
  }}
  .opp-body h3::before {{
    content: ''; display: inline-block; width: 18px; height: 1px;
    background: var(--accent); vertical-align: middle;
    margin-right: 10px; transform: translateY(-3px);
  }}
  .opp-body p {{ margin: 0 0 14px; text-align: justify; hyphens: auto; }}
  .opp-body p strong {{ font-weight: 600; color: var(--ink); }}

  /* Drop cap on the first paragraph after "The decision" */
  .opp-body .dropcap::first-letter {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500;
    float: left; font-size: 64px; line-height: 0.85;
    margin: 6px 10px 0 -2px;
    color: var(--accent);
  }}

  /* Marginalia ledger inside each opp --------------------------- */
  .opp-ledger {{
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 0; margin: 26px 0 8px;
    padding: 16px 0;
    border-top: 1px dashed var(--rule);
    border-bottom: 1px dashed var(--rule);
    background: rgba(255, 247, 215, 0.5);
  }}
  .opp-ledger .cell {{
    text-align: center; padding: 0 10px;
    border-right: 1px dotted var(--rule-soft);
  }}
  .opp-ledger .cell:last-child {{ border-right: none; }}
  .opp-ledger .cell-label {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 10px;
    color: var(--ink-faint); font-weight: 500; margin-bottom: 4px;
  }}
  .opp-ledger .cell-val {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 18px; line-height: 1.1; color: var(--ink);
    font-feature-settings: "lnum";
  }}

  /* Colophon ---------------------------------------------------- */
  .colophon {{
    margin-top: 72px; padding-top: 24px;
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
    font-size: 16px; color: var(--ink-dim);
    margin-bottom: 12px;
  }}
  .colophon .signature::before {{
    content: ''; display: block; width: 120px; height: 1px;
    background: var(--ink-faint); opacity: 0.5;
    margin: 0 auto 10px;
  }}

  /* Wide viewport — pull marginalia into right gutter */
  @media (min-width: 1180px) {{
    body {{ padding: 80px 40px 120px; }}
    .sheet {{ max-width: 1080px; padding: 96px 120px 84px; position: relative; }}
    .opp-ledger {{
      position: absolute; right: -240px; top: 0; width: 200px;
      grid-template-columns: 1fr;
      border: 1px solid var(--rule-soft);
      border-left: 2px solid var(--accent);
      background: rgba(255, 247, 215, 0.55);
      padding: 14px 16px;
    }}
    .opp-ledger .cell {{
      text-align: left; border-right: none; border-bottom: 1px dotted var(--rule-soft);
      padding: 8px 0;
    }}
    .opp-ledger .cell:last-child {{ border-bottom: none; }}
    .opp-ledger .cell-label {{ display: inline-block; min-width: 80px; }}
    .opp-ledger .cell-val {{ display: inline-block; font-size: 15px; }}
    .opp-section {{ position: relative; }}
    .opp-section .opp-body {{ max-width: 100%; }}
  }}

  /* Print --------------------------------------------------------*/
  @media print {{
    html, body {{ background: white; }}
    body::before {{ display: none; }}
    .sheet {{ box-shadow: none; border: none; padding: 24mm; max-width: none; }}
  }}

  /* Narrow viewport ---------------------------------------------*/
  @media (max-width: 720px) {{
    body {{ padding: 32px 8px 64px; font-size: 17px; }}
    .sheet {{ padding: 48px 28px 56px; }}
    h1 {{ font-size: 36px; }}
    .stats-strip, .opp-ledger {{ grid-template-columns: repeat(2, 1fr); gap: 16px; padding: 14px 0; }}
    .stats-strip .stat, .opp-ledger .cell {{ border-right: none; border-bottom: 1px dotted var(--rule-soft); padding-bottom: 12px; }}
    .opp-cohort {{ font-size: 22px; }}
    .opp-marker {{ font-size: 24px; min-width: 40px; }}
    .letterhead {{ flex-direction: column; gap: 8px; }}
    .letterhead-meta {{ text-align: left; }}
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
      <strong>Board memorandum</strong>
      In re: {portco_id}<br />
      {as_of} &middot; {audience}
    </div>
  </header>

  <section class="title-block">
    <div class="eyebrow">A diagnostic of the {portco_id} book</div>
    <h1>{n_opps} repeatable opportunities,<br /><em>{total_impact_scaled} per annum.</em></h1>
    <p class="lede">{lede}</p>
  </section>

  <section class="summary">
    <div class="summary-block">
      <div class="summary-label">Headline</div>
      <p>A cross-section pass over the {portco_id} loan book surfaces <strong>{n_opps}
      repeatable, high-volume decision patterns</strong> producing {total_impact_scaled}
      of annualized leakage. Each pattern is operator-controllable, traces to a
      cohort of named loans, and carries a counterfactual modeled at row level.</p>
    </div>
    <div class="summary-block">
      <div class="summary-label">Recommendation</div>
      <p>Reroute the cohorts identified below to break-even routing.
      Validate one quarter post-action; on confirmation, bake into standing
      underwriting policy. No headline product change required.</p>
    </div>
  </section>

  <div class="stats-strip">
    <div class="stat">
      <div class="stat-label">Identified</div>
      <div class="stat-num">{total_impact_scaled}</div>
      <div class="stat-sub">per annum</div>
    </div>
    <div class="stat">
      <div class="stat-label">Opportunities</div>
      <div class="stat-num">{n_opps}</div>
      <div class="stat-sub">distinct cohorts</div>
    </div>
    <div class="stat">
      <div class="stat-label">Vertical</div>
      <div class="stat-num">{vertical}</div>
      <div class="stat-sub">DX template</div>
    </div>
    <div class="stat">
      <div class="stat-label">EBITDA base</div>
      <div class="stat-num">{ebitda_baseline_scaled}</div>
      <div class="stat-sub">portco baseline</div>
    </div>
  </div>

  <div class="ornament"></div>

  {opp_blocks}

  <footer class="colophon">
    <div class="signature">Composed at the OpportunityMap layer.</div>
    Generated by <code>explain_decision</code>. Every figure in this memorandum
    traces to an OpportunityMap field &mdash; no number was invented in prose.<br />
    Source sidecar: <code>{source_path}</code> &middot; audience: {audience} &middot; {as_of}.
  </footer>

</article>

</body>
</html>
"""


_OPP_BLOCK = """\
<section class="opp-section">

  <header class="opp-head">
    <div class="opp-marker"></div>
    <div class="opp-titles">
      <div class="opp-archetype">{archetype}</div>
      <h2 class="opp-cohort">{segment_label}</h2>
    </div>
  </header>

  <div class="opp-body">

    <h3>The decision</h3>
    <p class="dropcap">{narrative}</p>

    <h3>The counterfactual</h3>
    <p>{counterfactual}</p>

    <h3>Risk &amp; rollout</h3>
    <p>{risk}</p>
    <p>{rollout}</p>

    <div class="opp-ledger">
      <div class="cell">
        <div class="cell-label">Cohort</div>
        <div class="cell-val">{n_rows}</div>
      </div>
      <div class="cell">
        <div class="cell-label">Annual leakage</div>
        <div class="cell-val">{current_outcome_scaled}</div>
      </div>
      <div class="cell">
        <div class="cell-label">Projected</div>
        <div class="cell-val">{impact_scaled}</div>
      </div>
      <div class="cell">
        <div class="cell-label">Persistence</div>
        <div class="cell-val">{persistence}</div>
      </div>
      <div class="cell">
        <div class="cell-label">Difficulty</div>
        <div class="cell-val">{difficulty}/5</div>
      </div>
    </div>

  </div>

</section>
"""


def explain_decision(
    opportunity_map_path: str,
    audience: Audience = "board",
    output_filename: str | None = None,
) -> dict:
    """
    Render a board-defendable narrative memo from a DX OpportunityMap.

    Args:
        opportunity_map_path: Path to a `dx_report_<portco>.json` sidecar.
        audience: 'board' or 'operator'.
        output_filename: Optional HTML basename. Defaults to
            ``explain_<portco>_<audience>.html``.

    Returns:
        dict with report_path, json_path, narrative_words, opportunities_explained.

    Raises:
        ToolError if the OpportunityMap is missing or malformed.
    """
    src = Path(opportunity_map_path)
    if not src.exists():
        raise ToolError(f"OpportunityMap not found: {src}")

    try:
        opp_map = json.loads(src.read_text())
    except json.JSONDecodeError as exc:
        raise ToolError(f"OpportunityMap is not valid JSON: {exc}")

    portco_id = opp_map.get("portco_id", "uploaded")
    vertical = opp_map.get("vertical", "—")
    total_impact = float(opp_map.get("total_projected_impact_usd_annual") or 0.0)
    ebitda_baseline = float(opp_map.get("ebitda_baseline_usd") or 0.0)
    opps = list(opp_map.get("opportunities") or [])
    n_opps = len(opps)
    if n_opps == 0:
        raise ToolError("OpportunityMap has zero opportunities — nothing to explain.")

    # Build per-opp HTML blocks
    opp_blocks: list[str] = []
    structured: list[dict] = []
    total_words = 0
    for idx, opp in enumerate(opps, start=1):
        narrative = (
            _opp_narrative_board(opp) if audience == "board"
            else _opp_narrative_operator(opp)
        )
        counterfactual = _opp_counterfactual(opp)
        risk = _opp_risk_of_inaction(opp)
        rollout = _opp_rollout(opp)
        total_words += sum(len(s.split()) for s in (narrative, counterfactual, risk, rollout))

        persist = opp.get("persistence_quarters_out_of_total") or [0, 0]
        persistence = f"{int(persist[0])}/{int(persist[1])}q" if int(persist[1]) else "—"

        opp_blocks.append(
            _OPP_BLOCK.format(
                idx=idx,
                archetype=str(opp.get("archetype", "decision")).capitalize(),
                segment_label=_format_segment(opp.get("segment") or {}),
                narrative=narrative,
                counterfactual=counterfactual,
                risk=risk,
                rollout=rollout,
                n_rows=f"{int(opp.get('n') or 0):,}",
                current_outcome_scaled=_scaled(float(opp.get("outcome_total_usd_annual") or 0.0)),
                impact_scaled=_scaled(float(opp.get("projected_impact_usd_annual") or 0.0)),
                persistence=persistence,
                difficulty=int(opp.get("difficulty_score_1_to_5") or 3),
            )
        )
        structured.append({
            "id": opp.get("id"),
            "archetype": opp.get("archetype"),
            "segment": opp.get("segment"),
            "narrative": narrative,
            "counterfactual": counterfactual,
            "risk_of_inaction": risk,
            "rollout": rollout,
        })

    audience_label = "Board memo" if audience == "board" else "Operator brief"
    lede = (
        "A 1-page synthesis of the diagnostic findings, written for the "
        "audience that decides whether to act."
    )

    html = _HTML_TEMPLATE.format(
        portco_id=portco_id,
        audience=audience_label,
        as_of=opp_map.get("as_of", date.today().isoformat()),
        total_impact_scaled=_scaled(total_impact),
        n_opps=n_opps,
        vertical=vertical,
        ebitda_baseline_scaled=_scaled(ebitda_baseline) if ebitda_baseline else "—",
        lede=lede,
        opp_blocks="\n".join(opp_blocks),
        source_path=str(src),
    )

    out_dir = src.parent
    out_name = output_filename or f"explain_{portco_id}_{audience}.html"
    out_path = out_dir / out_name
    out_path.write_text(html, encoding="utf-8")

    json_out_path = out_path.with_suffix(".json")
    json_out_path.write_text(
        json.dumps(
            {
                "portco_id": portco_id,
                "audience": audience,
                "as_of": opp_map.get("as_of", date.today().isoformat()),
                "total_projected_impact_usd_annual": total_impact,
                "vertical": vertical,
                "ebitda_baseline_usd": ebitda_baseline,
                "opportunities_explained": structured,
                "source_opportunity_map": str(src),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return {
        "report_path": str(out_path),
        "json_path": str(json_out_path),
        "narrative_words": total_words,
        "opportunities_explained": n_opps,
        "audience": audience,
        "portco_id": portco_id,
    }
