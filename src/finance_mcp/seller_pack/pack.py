"""
exit_proof_pack — Seller-side AI EBITDA proof pack.

A portco entering exit pre-audits and generates a defensible evidence
trail for every $ of AI-attributable value the seller plans to claim in
IM/CIM language. The buyer's AI diligence team finds nothing surprising
because the seller already disclosed:

  1. Provenance ledger — every $ of claimed AI uplift traces to a
     structured DX/BX artifact with row-level citations.
  2. Methodology disclosure — explicit statement of how each $ was
     modeled (counterfactual, persistence window, rollout assumption).
  3. Sensitivity table — what each $ becomes under conservative / base /
     aggressive assumptions (50% / 100% / 130%).
  4. Defensibility checklist — for each claim, the supporting artifact
     plus the "we'd argue this in a banker meeting" rationale.

Architecture mirrors `explainer/explain.py`: pure pandas/regex-free
deterministic templating, editorial-letterpress HTML render. No LLM
call inside the tool — every $ in the pack must trace to an
OpportunityMap or BX corpus field.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from fastmcp.exceptions import ToolError

from finance_mcp.seller_pack.sensitivity import (
    AGGRESSIVE_MULTIPLIER,
    BASE_MULTIPLIER,
    CONSERVATIVE_MULTIPLIER,
    SensitivityRow,
    SensitivityTable,
    build_table,
)


# ----- formatting helpers (mirrors explainer/explain.py shape) ---------------

def _scaled(usd: float) -> str:
    """Render a dollar amount at appropriate precision."""
    if abs(usd) >= 1e9:
        return f"${usd / 1e9:,.2f}B"
    if abs(usd) >= 1e6:
        return f"${usd / 1e6:,.1f}M"
    if abs(usd) >= 1e3:
        return f"${usd / 1e3:,.0f}K"
    return f"${usd:,.0f}"


def _format_segment(seg: dict) -> str:
    """{grade=A, term=360 months} → 'A × 360 months'."""
    return " × ".join(str(v) for v in seg.values())


def _format_decision_cols(cols: list[str] | tuple[str, ...]) -> str:
    return " × ".join(cols) if cols else "—"


def _evidence_summary(evidence_row_ids: list[Any]) -> str:
    """Show first three row IDs + '+N more' to keep the ledger compact."""
    if not evidence_row_ids:
        return "—"
    head = [str(x) for x in evidence_row_ids[:3]]
    more = len(evidence_row_ids) - len(head)
    if more > 0:
        return f"{', '.join(head)} (+{more} more)"
    return ", ".join(head)


# ----- claim construction ---------------------------------------------------

def _build_claim(opp: dict, source_filename: str) -> dict[str, Any]:
    """Convert one DX Opportunity into a structured proof-pack claim."""
    claim_id = str(opp.get("id") or "opp_unknown")
    impact = float(opp.get("projected_impact_usd_annual") or 0.0)
    current_loss = float(opp.get("outcome_total_usd_annual") or 0.0)
    n_rows = int(opp.get("n") or 0)
    persist = opp.get("persistence_quarters_out_of_total") or [0, 0]
    persist_q, total_q = (int(persist[0]), int(persist[1]))
    persistence_score = float(opp.get("persistence_score") or 0.0)
    difficulty = int(opp.get("difficulty_score_1_to_5") or 3)
    archetype = str(opp.get("archetype", "decision"))
    segment = opp.get("segment") or {}
    decision_cols = list(opp.get("decision_cols") or [])
    evidence_row_ids = list(opp.get("evidence_row_ids") or [])
    action = str(opp.get("projected_action") or "").strip()

    methodology_note = (
        f"Counterfactual: {_scaled(current_loss)} of current annual loss "
        f"across {n_rows:,} loans is rerouted to break-even outcome via "
        f"action — '{action}'. Persistence window: {persist_q}/{total_q} "
        f"quarters observed (score {persistence_score:.2f}). Rollout "
        f"assumption: difficulty {difficulty}/5 — operator-controllable, "
        f"no underwriting policy change required."
    )

    # Defensibility checklist: each box is a Y/N question the buyer's
    # diligence team would actually ask. The rationale is what the seller
    # would say into a banker meeting if challenged.
    has_counterfactual = current_loss < 0  # we observed a loss to reverse
    has_persistence = total_q > 0 and persistence_score >= 0.5
    has_row_evidence = len(evidence_row_ids) > 0
    would_buyer_challenge = (
        difficulty >= 4
        or persistence_score < 0.75
        or impact > 100_000_000
    )

    defensibility = {
        "would_buyer_challenge": bool(would_buyer_challenge),
        "has_counterfactual": bool(has_counterfactual),
        "has_persistence_data": bool(has_persistence),
        "has_row_evidence": bool(has_row_evidence),
        "rationale": (
            f"{archetype.capitalize()} pattern in cohort "
            f"{_format_segment(segment)} — current run-rate "
            f"{_scaled(current_loss)}/yr across {n_rows:,} loans, persisting "
            f"{persist_q}/{total_q} quarters. Banker-meeting answer: "
            f"'{n_rows:,} named loans in the cohort, {len(evidence_row_ids)} "
            f"sample row IDs in the source artifact, persistence score "
            f"{persistence_score:.2f} — this is structural, not vintage.'"
        ),
    }

    return {
        "claim_id": claim_id,
        "archetype": archetype,
        "segment": segment,
        "segment_label": _format_segment(segment),
        "decision_cols": decision_cols,
        "decision_cols_label": _format_decision_cols(decision_cols),
        "n_rows": n_rows,
        "current_loss_usd_annual": current_loss,
        "claimed_impact_usd_annual": impact,
        "persistence_quarters": persist_q,
        "persistence_total_quarters": total_q,
        "persistence_score": persistence_score,
        "difficulty_score_1_to_5": difficulty,
        "evidence_row_ids": evidence_row_ids,
        "evidence_row_summary": _evidence_summary(evidence_row_ids),
        "source_artifact": source_filename,
        "methodology_note": methodology_note,
        "defensibility": defensibility,
    }


# ----- HTML rendering (editorial letterpress, mirrors explain.py) ------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Exit-proof pack — {portco_id}</title>
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
    --green:    #2c5e2e;
    --max:      820px;
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
    line-height: 1.62;
    font-feature-settings: "liga", "dlig", "onum", "kern";
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    padding: 72px 20px 96px;
  }}
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
  }}

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
    font-weight: 400; font-size: 46px; line-height: 1.06;
    letter-spacing: -0.005em;
    margin: 0 0 18px; color: var(--ink);
  }}
  h1 em {{ font-style: italic; color: var(--accent); font-weight: 500; }}
  .lede {{
    font-family: 'EB Garamond', serif;
    font-style: italic; color: var(--ink-dim);
    font-size: 19px; line-height: 1.55;
    margin: 0; max-width: 60ch;
  }}

  /* Headline EBITDA contribution block ------------------------- */
  .headline {{
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
    padding: 28px 0; margin: 36px 0;
    text-align: center;
  }}
  .headline-label {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px;
    font-weight: 600; color: var(--accent);
    margin-bottom: 12px;
  }}
  .headline-figure {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 56px; line-height: 1;
    color: var(--ink);
    font-feature-settings: "lnum";
  }}
  .headline-figure em {{
    font-style: italic; color: var(--accent); font-weight: 500;
  }}
  .headline-range {{
    margin-top: 16px;
    font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-weight: 300;
    font-size: 14px; color: var(--ink-faint);
  }}
  .headline-range strong {{
    font-style: normal; color: var(--ink-dim); font-weight: 600;
  }}

  /* Section headings ------------------------------------------- */
  h2.section-h {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 28px; line-height: 1.15;
    color: var(--ink); margin: 56px 0 16px;
    border-bottom: 1px solid var(--rule);
    padding-bottom: 10px;
  }}
  h2.section-h .num {{
    font-style: italic; color: var(--accent); margin-right: 10px;
  }}
  .section-lede {{
    font-family: 'EB Garamond', serif;
    font-style: italic; color: var(--ink-dim);
    font-size: 16px; margin: 0 0 22px;
  }}

  /* Tables ----------------------------------------------------- */
  table {{
    width: 100%; border-collapse: collapse;
    margin: 18px 0 8px;
    font-size: 14px;
  }}
  th, td {{
    text-align: left; padding: 10px 8px;
    border-bottom: 1px dotted var(--rule-soft);
    vertical-align: top;
  }}
  th {{
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    color: var(--ink-faint); font-weight: 600;
    border-bottom: 1px solid var(--rule);
  }}
  td.num {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 16px; color: var(--ink);
    font-feature-settings: "lnum";
    white-space: nowrap;
  }}
  td.cid {{
    font-family: 'JetBrains Mono', monospace; font-size: 12px;
    color: var(--ink-dim);
  }}
  td.evidence {{
    font-family: 'JetBrains Mono', monospace; font-size: 11px;
    color: var(--ink-faint);
  }}
  td.method {{
    font-size: 13px; color: var(--ink-dim); line-height: 1.5;
    max-width: 360px;
  }}

  /* Sensitivity 3-col ------------------------------------------ */
  .sensitivity-grid {{
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 0; margin: 18px 0 8px;
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
  }}
  .sens-col {{
    padding: 22px 18px; text-align: center;
    border-right: 1px solid var(--rule-soft);
  }}
  .sens-col:last-child {{ border-right: none; }}
  .sens-col.base {{ background: rgba(107, 20, 20, 0.03); }}
  .sens-label {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 11px;
    color: var(--ink-faint); font-weight: 600; margin-bottom: 6px;
  }}
  .sens-col.base .sens-label {{ color: var(--accent); }}
  .sens-num {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 30px; line-height: 1;
    color: var(--ink); font-feature-settings: "lnum";
  }}
  .sens-mult {{
    margin-top: 6px;
    font-family: 'Newsreader', serif; font-style: italic; font-weight: 300;
    font-size: 12px; color: var(--ink-faint);
  }}

  /* Defensibility checklist ------------------------------------ */
  .check-block {{
    margin: 22px 0;
    padding: 18px 22px;
    border-left: 2px solid var(--accent);
    background: rgba(255, 247, 215, 0.5);
  }}
  .check-block .check-head {{
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 10px;
    border-bottom: 1px dotted var(--rule-soft);
    padding-bottom: 8px;
  }}
  .check-block .check-claim {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 18px; color: var(--ink);
  }}
  .check-block .check-claim .cid {{
    font-family: 'JetBrains Mono', monospace; font-size: 11px;
    color: var(--ink-faint); margin-right: 8px;
  }}
  .check-block .check-amount {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 18px; color: var(--accent);
    font-feature-settings: "lnum";
  }}
  .check-list {{ margin: 8px 0 12px; padding: 0; list-style: none; }}
  .check-list li {{
    margin: 4px 0; font-size: 14px; color: var(--ink-dim);
    display: flex; align-items: center; gap: 8px;
  }}
  .mark {{
    display: inline-block; width: 16px; height: 16px;
    text-align: center; line-height: 16px;
    font-family: 'Cormorant Garamond', serif;
    font-size: 14px; font-weight: 600;
  }}
  .mark.yes {{ color: var(--green); }}
  .mark.no  {{ color: var(--accent); }}
  .mark.warn {{ color: var(--gold); }}
  .check-rationale {{
    margin-top: 10px; padding-top: 10px;
    border-top: 1px dotted var(--rule-soft);
    font-style: italic; font-size: 13px; color: var(--ink-dim);
    line-height: 1.55;
  }}

  /* Colophon --------------------------------------------------- */
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
    font-size: 16px; color: var(--ink-dim); margin-bottom: 12px;
  }}
  .colophon .signature::before {{
    content: ''; display: block; width: 120px; height: 1px;
    background: var(--ink-faint); opacity: 0.5;
    margin: 0 auto 10px;
  }}

  @media print {{
    html, body {{ background: white; }}
    body::before {{ display: none; }}
    .sheet {{ box-shadow: none; border: none; padding: 24mm; max-width: none; }}
  }}
  @media (max-width: 720px) {{
    body {{ padding: 32px 8px 64px; font-size: 16px; }}
    .sheet {{ padding: 48px 24px 56px; }}
    h1 {{ font-size: 32px; }}
    .headline-figure {{ font-size: 38px; }}
    .sensitivity-grid {{ grid-template-columns: 1fr; }}
    .sens-col {{ border-right: none; border-bottom: 1px solid var(--rule-soft); }}
    .sens-col:last-child {{ border-bottom: none; }}
    table {{ font-size: 12px; }}
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
      <strong>Exit-proof pack</strong>
      In re: {portco_id}<br />
      {as_of} &middot; pre-banker disclosure
    </div>
  </header>

  <section class="title-block">
    <div class="eyebrow">AI EBITDA proof pack &middot; seller-side diligence</div>
    <h1>An auditable trail for <em>{base_total_scaled}</em><br />of AI-attributable annualized value.</h1>
    <p class="lede">{lede}</p>
  </section>

  <section class="headline">
    <div class="headline-label">Headline AI EBITDA contribution</div>
    <div class="headline-figure"><em>{base_total_scaled}</em> per annum</div>
    <div class="headline-range">
      Sensitivity range: <strong>{conservative_total_scaled}</strong> &mdash;
      <strong>{aggressive_total_scaled}</strong>
      ({n_claims} documented claims, source {source_filename})
    </div>
  </section>

  <h2 class="section-h"><span class="num">I.</span>Provenance ledger</h2>
  <p class="section-lede">Every $ of claimed AI uplift below traces to a specific
  cohort row in <code>{source_filename}</code>. Evidence row IDs are spot-check pointers
  the buyer's diligence team can pull from the underlying dataset.</p>
  <table class="provenance">
    <thead>
      <tr>
        <th>Claim</th>
        <th>Cohort</th>
        <th style="text-align:right">$ claimed (annual)</th>
        <th>Source</th>
        <th>Evidence rows</th>
        <th>Methodology note</th>
      </tr>
    </thead>
    <tbody>
      {provenance_rows}
    </tbody>
  </table>

  <h2 class="section-h"><span class="num">II.</span>Sensitivity analysis</h2>
  <p class="section-lede">The seller's headline figure is the base case. Conservative
  applies a 50% haircut for execution and persistence risk; aggressive applies
  a 30% premium for adjacent-cohort bleed-over and faster rollout. A buyer who
  disagrees with the multipliers can re-derive the table.</p>
  <div class="sensitivity-grid">
    <div class="sens-col">
      <div class="sens-label">Conservative</div>
      <div class="sens-num">{conservative_total_scaled}</div>
      <div class="sens-mult">{conservative_mult_pct}% of modeled impact</div>
    </div>
    <div class="sens-col base">
      <div class="sens-label">Base (headline)</div>
      <div class="sens-num">{base_total_scaled}</div>
      <div class="sens-mult">{base_mult_pct}% of modeled impact</div>
    </div>
    <div class="sens-col">
      <div class="sens-label">Aggressive</div>
      <div class="sens-num">{aggressive_total_scaled}</div>
      <div class="sens-mult">{aggressive_mult_pct}% of modeled impact</div>
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Claim</th>
        <th style="text-align:right">Conservative</th>
        <th style="text-align:right">Base</th>
        <th style="text-align:right">Aggressive</th>
        <th style="text-align:right">Spread</th>
      </tr>
    </thead>
    <tbody>
      {sensitivity_rows}
    </tbody>
  </table>

  <h2 class="section-h"><span class="num">III.</span>Defensibility checklist</h2>
  <p class="section-lede">For each claim: would the buyer challenge it? does it
  carry a counterfactual? is the persistence signal thick enough? are
  row-level evidence pointers in place? Below is the answer the seller
  would give in a banker meeting.</p>
  {defensibility_blocks}

  {bx_corpus_section}

  <footer class="colophon">
    <div class="signature">Composed at the OpportunityMap layer.</div>
    Generated by <code>exit_proof_pack</code>. Every figure traces to an
    OpportunityMap or BX corpus field &mdash; no number was invented in prose.<br />
    Source artifact: <code>{source_filename}</code> &middot; {as_of}.
  </footer>

</article>

</body>
</html>
"""


_PROVENANCE_ROW = """\
      <tr>
        <td class="cid">{claim_id}</td>
        <td>{archetype_label} &middot; {segment_label}</td>
        <td class="num" style="text-align:right">{impact_scaled}</td>
        <td class="cid">{source_filename}</td>
        <td class="evidence">{evidence_summary}</td>
        <td class="method">{methodology_note}</td>
      </tr>"""


_SENSITIVITY_ROW = """\
      <tr>
        <td class="cid">{claim_id}</td>
        <td class="num" style="text-align:right">{conservative_scaled}</td>
        <td class="num" style="text-align:right">{base_scaled}</td>
        <td class="num" style="text-align:right">{aggressive_scaled}</td>
        <td class="num" style="text-align:right">{spread_scaled}</td>
      </tr>"""


_DEFENSIBILITY_BLOCK = """\
  <div class="check-block">
    <div class="check-head">
      <div class="check-claim"><span class="cid">{claim_id}</span>{archetype_label} &middot; {segment_label}</div>
      <div class="check-amount">{impact_scaled}</div>
    </div>
    <ul class="check-list">
      <li><span class="mark {challenge_class}">{challenge_mark}</span> Would buyer challenge this claim? <em>{challenge_text}</em></li>
      <li><span class="mark {cf_class}">{cf_mark}</span> Has counterfactual evidence? <em>{cf_text}</em></li>
      <li><span class="mark {persist_class}">{persist_mark}</span> Has persistence data? <em>{persist_text}</em></li>
      <li><span class="mark {row_class}">{row_mark}</span> Has row-level evidence? <em>{row_text}</em></li>
    </ul>
    <div class="check-rationale">{rationale}</div>
  </div>"""


_BX_CORPUS_SECTION = """\
  <h2 class="section-h"><span class="num">IV.</span>Fund-level context</h2>
  <p class="section-lede">The pack is grounded in the seller's own DX
  artifact, but a buyer will compare the claims against the broader
  fund. The corpus rollup below shows this portco's rank inside the
  fund's distribution &mdash; the seller is not hiding it.</p>
  <table>
    <thead>
      <tr>
        <th>Corpus</th>
        <th>Portco count</th>
        <th style="text-align:right">Corpus total $/yr</th>
        <th style="text-align:right">This portco rank</th>
        <th style="text-align:right">Percentile</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td class="cid">{corpus_id}</td>
        <td>{portco_count}</td>
        <td class="num" style="text-align:right">{corpus_total_scaled}</td>
        <td>{rank_label}</td>
        <td class="num" style="text-align:right">{percentile_label}</td>
      </tr>
    </tbody>
  </table>"""


# ----- defensibility rendering helpers --------------------------------------

def _checkmark(positive: bool, *, invert: bool = False) -> tuple[str, str]:
    """Return (CSS class, glyph). When invert=True, positive value still
    renders as a yes-mark (used for would_buyer_challenge where True is
    the *risk* signal — handled by caller)."""
    if positive:
        return ("yes", "&#10003;")
    return ("no", "&#10007;")


def _render_defensibility_block(claim: dict[str, Any]) -> str:
    d = claim["defensibility"]
    challenge = bool(d["would_buyer_challenge"])
    cf = bool(d["has_counterfactual"])
    persist = bool(d["has_persistence_data"])
    rows = bool(d["has_row_evidence"])

    # would_buyer_challenge: True is a *warning* (yellow), False is a yes (green).
    if challenge:
        ch_class, ch_mark = ("warn", "!")
        ch_text = "yes — likely a challenge area; carry extra evidence."
    else:
        ch_class, ch_mark = ("yes", "&#10003;")
        ch_text = "no — within the typical buyer comfort band."

    cf_class, cf_mark = _checkmark(cf)
    cf_text = (
        f"yes — current loss of "
        f"{_scaled(claim['current_loss_usd_annual'])} is the counterfactual baseline."
        if cf
        else "no — claim lacks an observed-loss anchor."
    )

    persist_class, persist_mark = _checkmark(persist)
    persist_text = (
        f"yes — score {claim['persistence_score']:.2f} across "
        f"{claim['persistence_total_quarters']} quarters."
        if persist
        else f"thin — score {claim['persistence_score']:.2f} (≥0.50 required)."
    )

    row_class, row_mark = _checkmark(rows)
    row_text = (
        f"yes — {len(claim['evidence_row_ids'])} sample row IDs cited."
        if rows
        else "no — no row-level pointers in the source artifact."
    )

    return _DEFENSIBILITY_BLOCK.format(
        claim_id=claim["claim_id"],
        archetype_label=str(claim["archetype"]).capitalize(),
        segment_label=claim["segment_label"],
        impact_scaled=_scaled(claim["claimed_impact_usd_annual"]),
        challenge_class=ch_class,
        challenge_mark=ch_mark,
        challenge_text=ch_text,
        cf_class=cf_class,
        cf_mark=cf_mark,
        cf_text=cf_text,
        persist_class=persist_class,
        persist_mark=persist_mark,
        persist_text=persist_text,
        row_class=row_class,
        row_mark=row_mark,
        row_text=row_text,
        rationale=d["rationale"],
    )


def _render_bx_section(
    bx_data: dict[str, Any] | None, portco_id: str
) -> str:
    """Render the optional fund-level context section."""
    if not bx_data:
        return ""
    corpus_id = str(bx_data.get("corpus_id") or "—")
    portco_count = int(bx_data.get("portco_count") or 0)
    corpus_total = float(bx_data.get("total_identified_usd_annual") or 0.0)

    rank_table = list(bx_data.get("rank_table") or [])
    portco_row = next(
        (r for r in rank_table if str(r.get("portco_id")) == portco_id), None
    )
    if portco_row is None:
        rank_label = "n/a (portco not in corpus)"
        percentile_label = "—"
    else:
        rank_label = (
            f"{int(portco_row.get('rank') or 0)} of "
            f"{int(portco_row.get('rank_total') or 0)}"
        )
        percentile_label = f"{float(portco_row.get('percentile') or 0.0):.0f}%"

    return _BX_CORPUS_SECTION.format(
        corpus_id=corpus_id,
        portco_count=portco_count,
        corpus_total_scaled=_scaled(corpus_total),
        rank_label=rank_label,
        percentile_label=percentile_label,
    )


# ----- public tool entry point ----------------------------------------------

def exit_proof_pack(
    portco_id: str,
    opportunity_map_path: str,
    bx_corpus_path: str | None = None,
    output_filename: str | None = None,
) -> dict:
    """
    Generate a defensible AI EBITDA proof pack for a portco entering exit.

    Args:
        portco_id: The portco identifier (used in the header and output
            filename). Must match the OpportunityMap's `portco_id` field.
        opportunity_map_path: Path to a `dx_report_<portco>.json` sidecar
            — the DX artifact to ground the pack on.
        bx_corpus_path: Optional path to a `bx_report_<corpus>.json`
            rollup that contextualizes this portco against its fund.
        output_filename: Optional HTML basename. Defaults to
            ``exit_proof_pack_<portco_id>.html``.

    Returns:
        dict with:
          - report_path
          - json_path
          - n_claims_documented
          - total_attributable_usd_annual
          - sensitivity_range_usd: (conservative, aggressive)

    Raises:
        ToolError on any validation failure — missing path, malformed
        JSON, zero opportunities, or portco_id mismatch.
    """
    src = Path(opportunity_map_path)
    if not src.exists():
        raise ToolError(f"OpportunityMap not found: {src}")

    try:
        opp_map = json.loads(src.read_text())
    except json.JSONDecodeError as exc:
        raise ToolError(f"OpportunityMap is not valid JSON: {exc}")

    map_portco = str(opp_map.get("portco_id") or "")
    if map_portco and map_portco != portco_id:
        raise ToolError(
            f"portco_id mismatch: caller passed '{portco_id}' but "
            f"OpportunityMap declares '{map_portco}'."
        )

    opps = list(opp_map.get("opportunities") or [])
    if not opps:
        raise ToolError(
            "OpportunityMap has zero opportunities — nothing to document."
        )

    # Optional BX corpus rollup
    bx_data: dict[str, Any] | None = None
    if bx_corpus_path:
        bx_path = Path(bx_corpus_path)
        if not bx_path.exists():
            raise ToolError(f"BX corpus rollup not found: {bx_path}")
        try:
            bx_data = json.loads(bx_path.read_text())
        except json.JSONDecodeError as exc:
            raise ToolError(f"BX corpus is not valid JSON: {exc}")

    # 1. Build claim records (provenance ledger entries)
    source_filename = src.name
    claims = [_build_claim(opp, source_filename) for opp in opps]

    # 2. Build sensitivity table
    sens_table: SensitivityTable = build_table(
        [(c["claim_id"], c["claimed_impact_usd_annual"]) for c in claims]
    )

    # 3. Render HTML pieces
    provenance_rows_html = "\n".join(
        _PROVENANCE_ROW.format(
            claim_id=c["claim_id"],
            archetype_label=str(c["archetype"]).capitalize(),
            segment_label=c["segment_label"],
            impact_scaled=_scaled(c["claimed_impact_usd_annual"]),
            source_filename=source_filename,
            evidence_summary=c["evidence_row_summary"],
            methodology_note=c["methodology_note"],
        )
        for c in claims
    )

    sensitivity_rows_html = "\n".join(
        _SENSITIVITY_ROW.format(
            claim_id=row.claim_id,
            conservative_scaled=_scaled(row.conservative_usd),
            base_scaled=_scaled(row.base_usd),
            aggressive_scaled=_scaled(row.aggressive_usd),
            spread_scaled=_scaled(row.spread_usd),
        )
        for row in sens_table.rows
    )

    defensibility_blocks_html = "\n".join(
        _render_defensibility_block(c) for c in claims
    )

    bx_section_html = _render_bx_section(bx_data, portco_id)

    lede = (
        "Below is the evidence trail every $ of AI-attributable EBITDA "
        "in the IM/CIM language can be traced to. Provenance, methodology, "
        "sensitivity, and a defensibility checklist — pre-disclosed before "
        "banker engagement so the buyer's diligence team finds nothing "
        "surprising."
    )

    html = _HTML_TEMPLATE.format(
        portco_id=portco_id,
        as_of=opp_map.get("as_of", date.today().isoformat()),
        source_filename=source_filename,
        n_claims=len(claims),
        lede=lede,
        base_total_scaled=_scaled(sens_table.total_base_usd),
        conservative_total_scaled=_scaled(sens_table.total_conservative_usd),
        aggressive_total_scaled=_scaled(sens_table.total_aggressive_usd),
        conservative_mult_pct=int(round(CONSERVATIVE_MULTIPLIER * 100)),
        base_mult_pct=int(round(BASE_MULTIPLIER * 100)),
        aggressive_mult_pct=int(round(AGGRESSIVE_MULTIPLIER * 100)),
        provenance_rows=provenance_rows_html,
        sensitivity_rows=sensitivity_rows_html,
        defensibility_blocks=defensibility_blocks_html,
        bx_corpus_section=bx_section_html,
    )

    out_dir = src.parent
    out_name = output_filename or f"exit_proof_pack_{portco_id}.html"
    out_path = out_dir / out_name
    out_path.write_text(html, encoding="utf-8")

    # Structured JSON sidecar — feeds downstream tools (DDQ-response, IC memo)
    structured = {
        "portco_id": portco_id,
        "as_of": opp_map.get("as_of", date.today().isoformat()),
        "source_opportunity_map": str(src),
        "source_bx_corpus": str(bx_corpus_path) if bx_corpus_path else None,
        "n_claims_documented": len(claims),
        "headline_total_usd_annual": sens_table.total_base_usd,
        "sensitivity": {
            "conservative_multiplier": CONSERVATIVE_MULTIPLIER,
            "base_multiplier": BASE_MULTIPLIER,
            "aggressive_multiplier": AGGRESSIVE_MULTIPLIER,
            "total_conservative_usd_annual": sens_table.total_conservative_usd,
            "total_base_usd_annual": sens_table.total_base_usd,
            "total_aggressive_usd_annual": sens_table.total_aggressive_usd,
            "per_claim": [
                {
                    "claim_id": r.claim_id,
                    "conservative_usd_annual": r.conservative_usd,
                    "base_usd_annual": r.base_usd,
                    "aggressive_usd_annual": r.aggressive_usd,
                }
                for r in sens_table.rows
            ],
        },
        "provenance_ledger": claims,
    }

    json_out_path = out_path.with_suffix(".json")
    json_out_path.write_text(
        json.dumps(structured, indent=2, default=str), encoding="utf-8"
    )

    return {
        "report_path": str(out_path),
        "json_path": str(json_out_path),
        "n_claims_documented": len(claims),
        "total_attributable_usd_annual": sens_table.total_base_usd,
        "sensitivity_range_usd": [
            sens_table.total_conservative_usd,
            sens_table.total_aggressive_usd,
        ],
    }
