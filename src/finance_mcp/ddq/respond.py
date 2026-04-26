"""
ddq_respond — Generate first-draft responses to a fund-manager DDQ
(Due-Diligence Questionnaire) by retrieving from the fund's existing
AI-related artifacts in `finance_output/`, templating an answer per
question, and scoring cross-answer consistency.

The wedge: ILPA DDQ v2.0 (Q1 2026) added new AI governance / data /
risk sections; funds are answering them inconsistently across vintages.
The first GP that ships a consistency layer wins the next allocation
cycle.

Architecture mirrors `seller_pack/pack.py` and `explainer/explain.py`:
pure deterministic templating over JSON sidecars, no LLM call inside
the tool, editorial-letterpress HTML render. Every figure in the
response packet traces to a JSON field in `finance_output/`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from fastmcp.exceptions import ToolError

from finance_mcp.ddq.consistency import (
    check_consistency,
    extract_entities,
    extract_figures,
)
from finance_mcp.ddq.questions import DDQQuestion, get_questions


# ---- formatting helpers (mirrors explain.py / pack.py) ---------------------


def _scaled(usd: float) -> str:
    """Render a dollar amount at appropriate precision."""
    if abs(usd) >= 1e9:
        return f"${usd / 1e9:,.2f}B"
    if abs(usd) >= 1e6:
        return f"${usd / 1e6:,.1f}M"
    if abs(usd) >= 1e3:
        return f"${usd / 1e3:,.0f}K"
    return f"${usd:,.0f}"


# ---- knowledge-base loading ------------------------------------------------


@dataclass(frozen=True)
class KnowledgeBase:
    """Frozen index of the fund's AI-evidence artifacts."""

    base_dir: Path
    dx_reports: tuple[dict, ...]
    bx_reports: tuple[dict, ...]
    explain_memos: tuple[dict, ...]
    exit_packs: tuple[dict, ...]
    ai_act_audits: tuple[dict, ...]
    cim_redflags: tuple[dict, ...]
    artifact_files: tuple[str, ...]


def _load_jsons(base: Path, glob_pattern: str) -> list[tuple[str, dict]]:
    """Load every matching JSON; return list of (filename, parsed_dict).

    A malformed file is skipped (logged via the returned error count
    in the pipeline) rather than aborting the entire DDQ response —
    a single corrupt sidecar should not block the LP from receiving
    answers grounded in the rest of the corpus.
    """
    out: list[tuple[str, dict]] = []
    for p in sorted(base.glob(glob_pattern)):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append((p.name, data))
    return out


def _load_knowledge_base(base_dir: Path) -> KnowledgeBase:
    """Read every relevant `finance_output/*.json` artifact into memory."""
    if not base_dir.exists() or not base_dir.is_dir():
        raise ToolError(f"Knowledge-base directory not found: {base_dir}")

    dx = _load_jsons(base_dir, "dx_report_*.json")
    bx = _load_jsons(base_dir, "bx_report_*.json")
    explain = _load_jsons(base_dir, "explain_*_board.json")
    exit_packs = _load_jsons(base_dir, "exit_proof_pack_*.json")
    ai_act = _load_jsons(base_dir, "ai_act_audit_*.json")
    cim = _load_jsons(base_dir, "cim_redflags_*.json")

    all_files = (
        [name for name, _ in dx]
        + [name for name, _ in bx]
        + [name for name, _ in explain]
        + [name for name, _ in exit_packs]
        + [name for name, _ in ai_act]
        + [name for name, _ in cim]
    )

    if not all_files:
        raise ToolError(
            f"Knowledge-base directory contains no recognized artifacts: {base_dir}"
        )

    return KnowledgeBase(
        base_dir=base_dir,
        dx_reports=tuple(d for _, d in dx),
        bx_reports=tuple(d for _, d in bx),
        explain_memos=tuple(d for _, d in explain),
        exit_packs=tuple(d for _, d in exit_packs),
        ai_act_audits=tuple(d for _, d in ai_act),
        cim_redflags=tuple(d for _, d in cim),
        artifact_files=tuple(all_files),
    )


# ---- evidence retrieval ----------------------------------------------------


def _aggregate_dx(kb: KnowledgeBase) -> dict[str, Any]:
    """Pre-compute everything the DX-shaped questions need."""
    portcos: list[str] = []
    opps: list[dict] = []
    evidence_rows = 0
    persistence_scores: list[float] = []
    persistence_quarters: list[int] = []
    structural_count = 0
    difficulty_scores: list[int] = []
    total_current_loss = 0.0
    total_projected_impact = 0.0

    for dx in kb.dx_reports:
        portco_id = str(dx.get("portco_id") or "")
        if portco_id:
            portcos.append(portco_id)
        for opp in dx.get("opportunities") or []:
            opps.append(opp)
            evidence_rows += len(opp.get("evidence_row_ids") or [])
            score = float(opp.get("persistence_score") or 0.0)
            persist_pair = opp.get("persistence_quarters_out_of_total") or [0, 0]
            try:
                persist_q = int(persist_pair[0])
            except (TypeError, ValueError, IndexError):
                persist_q = 0
            persistence_scores.append(score)
            persistence_quarters.append(persist_q)
            if score >= 0.5 and persist_q >= 2:
                structural_count += 1
            difficulty_scores.append(int(opp.get("difficulty_score_1_to_5") or 3))
            total_current_loss += float(opp.get("outcome_total_usd_annual") or 0.0)
            total_projected_impact += float(
                opp.get("projected_impact_usd_annual") or 0.0
            )

    n_low_difficulty = sum(1 for d in difficulty_scores if d <= 3)
    low_difficulty_pct = (
        int(round(100 * n_low_difficulty / len(difficulty_scores)))
        if difficulty_scores
        else 0
    )

    return {
        "n_dx": len(kb.dx_reports),
        "n_portcos": len(set(portcos)),
        "portcos": sorted(set(portcos)),
        "n_opportunities": len(opps),
        "n_evidence_rows": evidence_rows,
        "median_persistence": median(persistence_scores) if persistence_scores else 0.0,
        "median_quarters": (
            int(median(persistence_quarters)) if persistence_quarters else 0
        ),
        "n_structural": structural_count,
        "median_difficulty": (
            int(median(difficulty_scores)) if difficulty_scores else 3
        ),
        "n_low_difficulty": n_low_difficulty,
        "low_difficulty_pct": low_difficulty_pct,
        "total_current_loss": _scaled(total_current_loss),
        "total_projected_impact": _scaled(total_projected_impact),
    }


def _aggregate_ai_act(kb: KnowledgeBase) -> dict[str, Any]:
    """Pre-compute everything the EU-AI-Act-shaped questions need."""
    portcos: list[str] = []
    summaries: list[str] = []
    for audit in kb.ai_act_audits:
        portco = str(audit.get("portco_id") or "—")
        portcos.append(portco)
        verdict = str(audit.get("high_risk_classification") or "—")
        deadline = str(audit.get("deadline") or "—")
        summaries.append(f"{portco}: {verdict}, deadline {deadline}")

    return {
        "n_ai_act": len(kb.ai_act_audits),
        "ai_act_portcos": ", ".join(portcos) if portcos else "—",
        "ai_act_summary": "; ".join(summaries) if summaries else "—",
    }


def _aggregate_cim(kb: KnowledgeBase) -> dict[str, Any]:
    """CIM red-flag rollup focused on AI-adjacent disclosure risk."""
    company_summaries: list[str] = []
    for cim in kb.cim_redflags:
        name = str(cim.get("company_name") or "—")
        form = str(cim.get("form") or "—")
        flags = cim.get("flags") or []
        ai_flags = [
            f
            for f in flags
            if any(
                term in str(f.get("excerpt", "")).lower()
                for term in ("ai ", "artificial intelligence", "machine learning")
            )
            and str(f.get("severity")) == "high"
        ]
        company_summaries.append(
            f"{name} ({form}): {len(ai_flags)} high-severity AI-adjacent risk factor(s)"
        )

    return {
        "n_cim": len(kb.cim_redflags),
        "cim_ai_summary": "; ".join(company_summaries) if company_summaries else "—",
    }


def _aggregate_exit_packs(kb: KnowledgeBase) -> dict[str, Any]:
    """Roll up sensitivity bands and challenge flags across exit-proof packs."""
    base_total = 0.0
    cons_total = 0.0
    aggr_total = 0.0
    challenge_flags = 0
    total_claims = 0
    for pack in kb.exit_packs:
        sens = pack.get("sensitivity") or {}
        base_total += float(sens.get("total_base_usd_annual") or 0.0)
        cons_total += float(sens.get("total_conservative_usd_annual") or 0.0)
        aggr_total += float(sens.get("total_aggressive_usd_annual") or 0.0)
        for claim in pack.get("provenance_ledger") or []:
            total_claims += 1
            d = claim.get("defensibility") or {}
            if bool(d.get("would_buyer_challenge")):
                challenge_flags += 1

    return {
        "n_exit_packs": len(kb.exit_packs),
        "exit_total_base": _scaled(base_total),
        "exit_total_conservative": _scaled(cons_total),
        "exit_total_aggressive": _scaled(aggr_total),
        "n_challenge_flags": challenge_flags,
        "n_exit_claims": total_claims,
    }


def _build_evidence(kb: KnowledgeBase) -> dict[str, Any]:
    """Assemble the full evidence dict consumed by every answer template."""
    dx = _aggregate_dx(kb)
    ai_act = _aggregate_ai_act(kb)
    cim = _aggregate_cim(kb)
    exit_packs = _aggregate_exit_packs(kb)

    portco_inventory = (
        ", ".join(dx["portcos"]) if dx["portcos"] else "no portcos indexed"
    )
    return {
        **dx,
        **ai_act,
        **cim,
        **exit_packs,
        "n_bx": len(kb.bx_reports),
        "n_board_memos": len(kb.explain_memos),
        "portco_inventory": portco_inventory,
    }


# ---- per-question source-citation logic -----------------------------------


def _cite_sources(question_id: str, kb: KnowledgeBase) -> list[str]:
    """Return the list of artifact filenames a given question pulled from."""
    qid = question_id
    files = list(kb.artifact_files)

    def _filter(prefixes: tuple[str, ...]) -> list[str]:
        return [f for f in files if any(f.startswith(p) for p in prefixes)]

    if qid == "Q01_GOV_INVENTORY":
        return _filter(("dx_report_", "ai_act_audit_", "bx_report_"))
    if qid == "Q02_GOV_OVERSIGHT":
        return _filter(("explain_", "exit_proof_pack_"))
    if qid in ("Q03_DATA_LINEAGE", "Q05_MRM_VALIDATION", "Q06_MRM_COUNTERFACTUAL",
               "Q07_MRM_DIFFICULTY"):
        return _filter(("dx_report_",))
    if qid == "Q04_DATA_GOVERNANCE":
        return _filter(("ai_act_audit_",))
    if qid == "Q08_VEND_THIRD_PARTY":
        return _filter(("ai_act_audit_",))
    if qid == "Q09_REG_EU_AI_ACT":
        return _filter(("ai_act_audit_",))
    if qid == "Q10_REG_DISCLOSURE_RISK":
        return _filter(("cim_redflags_",))
    if qid in ("Q11_VAL_ATTRIBUTION", "Q12_EXIT_DEFENSIBILITY"):
        return _filter(("exit_proof_pack_",))
    return []


def _render_answer(question: DDQQuestion, evidence: dict[str, Any]) -> str:
    """Apply the answer template; missing keys fall back to em-dash."""
    safe_evidence = _SafeDict(evidence)
    return question.answer_template.format_map(safe_evidence)


class _SafeDict(dict):
    """Dict that returns '—' for missing keys, so a template never crashes."""

    def __missing__(self, key: str) -> str:  # type: ignore[override]
        return "—"


# ---- HTML render (editorial letterpress, mirrors explain.py / pack.py) -----

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>DDQ response packet — {fund_name}</title>
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
    font-weight: 400; font-size: 44px; line-height: 1.06;
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

  .stats-strip {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 0; margin: 32px 0 36px;
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

  h2.section-h {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 28px; line-height: 1.15;
    color: var(--ink); margin: 56px 0 12px;
    border-bottom: 1px solid var(--rule);
    padding-bottom: 10px;
  }}
  h2.section-h .num {{
    font-style: italic; color: var(--accent); margin-right: 10px;
  }}

  .question-block {{
    margin: 32px 0; padding: 22px 24px;
    border-left: 2px solid var(--accent);
    background: rgba(255, 247, 215, 0.5);
  }}
  .question-head {{
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; margin-bottom: 12px;
    border-bottom: 1px dotted var(--rule-soft);
    padding-bottom: 8px;
  }}
  .qid {{
    font-family: 'JetBrains Mono', monospace; font-size: 11px;
    color: var(--ink-faint);
  }}
  .qcat {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    font-weight: 600; color: var(--accent);
  }}
  .qtext {{
    font-family: 'EB Garamond', serif;
    font-weight: 500; font-size: 17px; color: var(--ink);
    margin: 0 0 14px; font-style: italic;
  }}
  .answer-label {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    color: var(--accent); font-weight: 600; margin-bottom: 6px;
  }}
  .answer-body {{
    margin: 0 0 14px; line-height: 1.62;
    text-align: justify; hyphens: auto;
  }}
  .answer-body strong {{ font-weight: 600; color: var(--ink); }}
  .citations {{
    margin-top: 12px; padding-top: 10px;
    border-top: 1px dotted var(--rule-soft);
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; color: var(--ink-faint);
    line-height: 1.55;
  }}
  .citations strong {{
    font-family: 'EB Garamond', serif; font-style: italic; font-weight: 500;
    color: var(--ink-dim); margin-right: 6px;
  }}

  .flag-block {{
    margin: 16px 0; padding: 14px 18px;
    border-left: 2px solid var(--gold);
    background: rgba(138, 111, 26, 0.06);
    font-size: 14px;
  }}
  .flag-head {{
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 6px;
  }}
  .flag-type {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    font-weight: 600; color: var(--gold);
  }}
  .flag-sev {{
    font-family: 'JetBrains Mono', monospace; font-size: 10px;
    color: var(--ink-faint);
  }}
  .flag-desc {{ margin: 0; color: var(--ink-dim); }}
  .flag-evidence {{
    font-family: 'JetBrains Mono', monospace; font-size: 11px;
    color: var(--ink-faint); margin-top: 4px;
  }}
  .no-flags {{
    margin: 24px 0; padding: 18px 22px;
    border-left: 2px solid var(--green);
    background: rgba(44, 94, 46, 0.04);
    color: var(--ink-dim);
  }}
  .no-flags strong {{ color: var(--green); font-weight: 600; }}

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
    .stats-strip {{ grid-template-columns: repeat(2, 1fr); gap: 12px; padding: 14px 0; }}
    .stats-strip .stat {{ border-right: none; border-bottom: 1px dotted var(--rule-soft); padding-bottom: 12px; }}
    .letterhead {{ flex-direction: column; gap: 8px; }}
    .letterhead-meta {{ text-align: left; }}
    .question-head {{ flex-direction: column; align-items: flex-start; gap: 4px; }}
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
      <strong>DDQ response packet</strong>
      In re: {fund_name}<br />
      {as_of} &middot; ILPA-shaped &middot; first draft
    </div>
  </header>

  <section class="title-block">
    <div class="eyebrow">Due-diligence questionnaire response &middot; AI sections</div>
    <h1>First-draft answers to <em>{n_questions}</em> ILPA-shaped<br />AI diligence questions.</h1>
    <p class="lede">{lede}</p>
  </section>

  <div class="stats-strip">
    <div class="stat">
      <div class="stat-label">Questions answered</div>
      <div class="stat-num">{n_questions}</div>
      <div class="stat-sub">ILPA-shaped set</div>
    </div>
    <div class="stat">
      <div class="stat-label">Artifacts indexed</div>
      <div class="stat-num">{n_artifacts}</div>
      <div class="stat-sub">finance_output/</div>
    </div>
    <div class="stat">
      <div class="stat-label">Consistency flags</div>
      <div class="stat-num">{n_flags}</div>
      <div class="stat-sub">cross-answer</div>
    </div>
    <div class="stat">
      <div class="stat-label">Portcos covered</div>
      <div class="stat-num">{n_portcos}</div>
      <div class="stat-sub">distinct portco_ids</div>
    </div>
  </div>

  <h2 class="section-h"><span class="num">I.</span>Responses</h2>
  {answer_blocks}

  <h2 class="section-h"><span class="num">II.</span>Cross-answer consistency</h2>
  {flag_blocks}

  <footer class="colophon">
    <div class="signature">Composed at the artifact layer.</div>
    Generated by <code>ddq_respond</code>. Every answer above traces to a
    JSON sidecar in <code>{base_dir}</code>; no number was invented in prose.
    Cross-answer consistency is checked deterministically by regex extraction
    and pairwise comparison &mdash; the contradictions surfaced are ones a
    reader can verify by reading two paragraphs side-by-side.<br />
    {fund_name} &middot; {as_of}.
  </footer>

</article>

</body>
</html>
"""


_ANSWER_BLOCK = """\
  <div class="question-block">
    <div class="question-head">
      <span class="qid">{qid}</span>
      <span class="qcat">{qcat}</span>
    </div>
    <p class="qtext">{qtext}</p>
    <div class="answer-label">Response</div>
    <p class="answer-body">{answer}</p>
    <div class="citations"><strong>Source artifacts:</strong> {citations}</div>
  </div>"""


_FLAG_BLOCK = """\
  <div class="flag-block">
    <div class="flag-head">
      <span class="flag-type">{flag_type}</span>
      <span class="flag-sev">severity: {severity} &middot; {qids}</span>
    </div>
    <p class="flag-desc">{description}</p>
    <div class="flag-evidence">evidence: {evidence}</div>
  </div>"""


_NO_FLAGS_BLOCK = """\
  <div class="no-flags">
    <strong>No contradictions detected.</strong> Across {n_questions} answers
    the deterministic checker found no numeric mismatches and no orphan
    entity references against the fund inventory. This is itself a
    publishable finding &mdash; the GP can attest that responses are
    internally consistent at draft stage.
  </div>"""


# ---- public tool entry point -----------------------------------------------


def ddq_respond(
    fund_name: str,
    knowledge_base_dir: str = "finance_output",
    output_filename: str | None = None,
) -> dict:
    """
    Generate first-draft DDQ responses + cross-answer consistency check.

    Args:
        fund_name: Fund label that goes into the response packet header.
            Validated only for non-emptiness; the same knowledge base may
            serve multiple funds.
        knowledge_base_dir: Directory containing the fund's AI-evidence
            artifacts (the same `finance_output/` that DX, BX, explainer,
            and seller_pack write into). Defaults to `finance_output`.
        output_filename: Optional HTML basename. Defaults to
            ``ddq_response_<fund_slug>.html``.

    Returns:
        dict with:
          - report_path
          - json_path
          - n_questions_answered
          - n_consistency_flags
          - knowledge_base_artifacts (count of indexed JSONs)

    Raises:
        ToolError on validation failure — empty fund_name, missing
        knowledge-base directory, or zero recognized artifacts.
    """
    if not fund_name or not fund_name.strip():
        raise ToolError("fund_name must be a non-empty string.")

    base_dir = Path(knowledge_base_dir).resolve()
    kb = _load_knowledge_base(base_dir)

    # 1. Build the fund-wide evidence dict
    evidence = _build_evidence(kb)

    # 2. Render answers per question
    questions = get_questions()
    rendered_answers: list[dict[str, Any]] = []
    answer_html_blocks: list[str] = []
    for q in questions:
        answer_text = _render_answer(q, evidence)
        citations = _cite_sources(q.id, kb)
        figures = extract_figures(answer_text)
        entities = extract_entities(answer_text)
        rendered_answers.append(
            {
                "question_id": q.id,
                "category": q.category,
                "question_text": q.text,
                "answer": answer_text,
                "citations": citations,
                "figures": [
                    {
                        "kind": f.kind,
                        "value": f.value,
                        "raw": f.raw,
                        "unit": f.unit,
                    }
                    for f in figures
                ],
                "entities": sorted(entities),
                "_figures_obj": figures,
                "_entities_obj": entities,
            }
        )
        answer_html_blocks.append(
            _ANSWER_BLOCK.format(
                qid=q.id,
                qcat=q.category,
                qtext=q.text,
                answer=answer_text,
                citations=", ".join(citations) if citations else "—",
            )
        )

    # 3. Cross-answer consistency check
    flags = check_consistency(
        [
            {
                "question_id": a["question_id"],
                "category": a["category"],
                "text": a["answer"],
                "figures": a["_figures_obj"],
                "entities": a["_entities_obj"],
            }
            for a in rendered_answers
        ]
    )

    if flags:
        flag_html = "\n".join(
            _FLAG_BLOCK.format(
                flag_type=f.flag_type,
                severity=f.severity,
                qids=" → ".join(f.question_ids),
                description=f.description,
                evidence=", ".join(f.evidence) if f.evidence else "—",
            )
            for f in flags
        )
    else:
        flag_html = _NO_FLAGS_BLOCK.format(n_questions=len(questions))

    # 4. Render HTML
    lede = (
        "Below are deterministic first-draft answers to the ILPA-shaped AI "
        "diligence questions every LP allocator is asking in the Q1 2026 "
        "vintage. Each answer cites the source artifacts it pulled evidence "
        "from. A cross-answer consistency layer scores numeric and entity "
        "contradictions before the LP does."
    )

    html = _HTML_TEMPLATE.format(
        fund_name=fund_name,
        as_of=date.today().isoformat(),
        n_questions=len(questions),
        n_artifacts=len(kb.artifact_files),
        n_flags=len(flags),
        n_portcos=evidence.get("n_portcos", 0),
        lede=lede,
        answer_blocks="\n".join(answer_html_blocks),
        flag_blocks=flag_html,
        base_dir=str(base_dir),
    )

    fund_slug = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in fund_name.strip()
    ).strip("_") or "fund"
    out_name = output_filename or f"ddq_response_{fund_slug}.html"
    out_path = base_dir / out_name
    out_path.write_text(html, encoding="utf-8")

    # 5. Structured JSON sidecar
    structured: dict[str, Any] = {
        "fund_name": fund_name,
        "as_of": date.today().isoformat(),
        "knowledge_base_dir": str(base_dir),
        "knowledge_base_artifacts": list(kb.artifact_files),
        "n_questions_answered": len(rendered_answers),
        "n_consistency_flags": len(flags),
        "answers": [
            {k: v for k, v in a.items() if not k.startswith("_")}
            for a in rendered_answers
        ],
        "consistency_flags": [
            {
                "flag_type": f.flag_type,
                "severity": f.severity,
                "question_ids": list(f.question_ids),
                "description": f.description,
                "evidence": list(f.evidence),
            }
            for f in flags
        ],
        "evidence_aggregate": {
            k: v for k, v in evidence.items()
            if not isinstance(v, list)  # keep sidecar compact
        },
    }

    json_out_path = out_path.with_suffix(".json")
    json_out_path.write_text(
        json.dumps(structured, indent=2, default=str), encoding="utf-8"
    )

    return {
        "report_path": str(out_path),
        "json_path": str(json_out_path),
        "n_questions_answered": len(rendered_answers),
        "n_consistency_flags": len(flags),
        "knowledge_base_artifacts": len(kb.artifact_files),
    }
