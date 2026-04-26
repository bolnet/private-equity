"""
Frozen ILPA-shaped DDQ question set — Q1 2026.

Twelve questions an LP would actually ask of a GP in 2026 about the GP's
AI practices at the portco level: governance, data lineage, model risk,
vendor management, regulatory exposure, value-creation accounting, exit
defensibility. The set is shaped after ILPA DDQ v2.0 (Q1 2026), which
added explicit AI governance / data / risk sections — but the questions
here are not the literal ILPA template (paywalled). They are the same
*shape* of question any LP allocator would ask in this cycle.

Each question carries:
  - id              : stable string identifier (used in citations)
  - category        : ILPA-shaped category bucket
  - text            : the LP-facing question, asked verbatim
  - evidence_query  : a deterministic instruction telling the retriever
                      *which* artifact families and *which* fields are
                      authoritative for this question
  - answer_template : a Python format-string built from retrieved fields;
                      the templating happens in respond.py

Any change to this set is a vintage change — older DDQ responses from
the same fund will not be comparable. Treat as frozen per release.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DDQQuestion:
    """A single ILPA-shaped DDQ question, frozen at construction.

    answer_template uses Python str.format placeholders that are filled
    from the evidence dict assembled by respond._retrieve_evidence().
    Any placeholder missing from evidence falls back to '—'.
    """

    id: str
    category: str
    text: str
    evidence_query: str
    answer_template: str


# Categories follow ILPA v2.0 Q1 2026 shape:
#   GOV  — AI governance & oversight
#   DATA — Data lineage, provenance, governance
#   MRM  — Model risk management
#   VEND — Vendor / third-party AI management
#   REG  — Regulatory exposure (EU AI Act, sectoral)
#   VAL  — AI-attributable value & accounting
#   EXIT — Exit-readiness / disclosure posture

QUESTIONS: tuple[DDQQuestion, ...] = (
    DDQQuestion(
        id="Q01_GOV_INVENTORY",
        category="GOV",
        text=(
            "Provide an inventory of AI/ML systems in use across the fund's "
            "portfolio companies, including system purpose, deployment status, "
            "and the responsible portco-level owner."
        ),
        evidence_query=(
            "Enumerate every dx_report_*.json portco_id and vertical, plus "
            "every ai_act_audit_*.json portco_id and ai_system_description."
        ),
        answer_template=(
            "Across {n_portcos} portfolio companies covered by the fund's AI "
            "diligence layer, the fund maintains structured DX (Decision "
            "Diagnostic) sidecars for {n_dx} portcos and EU AI Act audit "
            "packs for {n_ai_act} portcos. The portco-level inventory is: "
            "{portco_inventory}. Each portco's AI system is owned at the "
            "portco operating level; fund-level oversight rolls up via the "
            "BX corpus index covering {n_bx} corpus rollups."
        ),
    ),
    DDQQuestion(
        id="Q02_GOV_OVERSIGHT",
        category="GOV",
        text=(
            "Describe the fund's AI oversight model. How are material AI "
            "decisions escalated to the IC, and what evidence is preserved?"
        ),
        evidence_query=(
            "Count explain_*_board.json artifacts (board-defendable memos) "
            "and exit_proof_pack_*.json artifacts (pre-banker disclosure). "
            "Evidence trail is the structured JSON sidecar set."
        ),
        answer_template=(
            "Material AI decisions at the portco level are documented as "
            "board-defendable memos before being escalated; the fund "
            "currently maintains {n_board_memos} such memos on file "
            "(explain_*_board artifacts), each tracing every $ figure to a "
            "specific OpportunityMap field. Exit-stage AI claims are "
            "pre-disclosed via {n_exit_packs} structured proof packs. The "
            "oversight chain is: portco DX run → board memo → IC review → "
            "(at exit) seller-side proof pack."
        ),
    ),
    DDQQuestion(
        id="Q03_DATA_LINEAGE",
        category="DATA",
        text=(
            "For each material AI system, describe the data lineage and the "
            "row-level provenance available to support model outputs."
        ),
        evidence_query=(
            "Pull evidence_row_ids counts from all dx_report_*.json "
            "opportunities. Row-level provenance is the OpportunityMap's "
            "evidence_row_ids field."
        ),
        answer_template=(
            "Every modeled $ figure carried by the fund's AI diligence "
            "layer is anchored to row-level evidence pointers in the "
            "underlying portco dataset. Across {n_dx} OpportunityMap "
            "sidecars there are {n_opportunities} cohort findings, each "
            "carrying sample evidence_row_ids that diligence teams can pull "
            "from the source dataset for spot-check. Total evidence rows "
            "cited across the corpus: {n_evidence_rows}. Lineage runs from "
            "raw portco loan/transaction tape → DX cohort cut → "
            "OpportunityMap sidecar → board memo / proof pack."
        ),
    ),
    DDQQuestion(
        id="Q04_DATA_GOVERNANCE",
        category="DATA",
        text=(
            "What data governance controls apply to training and validation "
            "data used by the portco AI systems? Reference applicable "
            "Article 10 (EU AI Act) deliverables where relevant."
        ),
        evidence_query=(
            "Pull Article 10 deliverables count from ai_act_audit_*.json "
            "where present."
        ),
        answer_template=(
            "Where a portco operates an EU-scope high-risk AI system, the "
            "fund maintains an EU AI Act audit pack on file. {n_ai_act} "
            "such packs exist; each documents Article 10 (Data and data "
            "governance) deliverables — sourcing log, bias examination "
            "report, representativeness statement, and labelling procedure. "
            "EU-high-risk portcos covered: {ai_act_portcos}."
        ),
    ),
    DDQQuestion(
        id="Q05_MRM_VALIDATION",
        category="MRM",
        text=(
            "How does the fund validate model outputs at the portco level? "
            "Provide quantitative evidence of model performance and "
            "persistence across observation windows."
        ),
        evidence_query=(
            "Aggregate persistence_score and persistence_quarters_out_of_total "
            "across all dx_report_*.json opportunities. Persistence ≥0.5 "
            "across ≥2 quarters is the standing threshold for 'structural'."
        ),
        answer_template=(
            "Model outputs are validated via DX persistence: the same "
            "cohort pattern must reappear across multiple observed quarters "
            "before it is treated as structural. Across {n_opportunities} "
            "cohort findings the median persistence score is "
            "{median_persistence:.2f} over {median_quarters} quarters; "
            "{n_structural} of {n_opportunities} findings meet the "
            "structural threshold (score ≥0.5 across ≥2 quarters). "
            "Validation is row-level, not aggregate-only."
        ),
    ),
    DDQQuestion(
        id="Q06_MRM_COUNTERFACTUAL",
        category="MRM",
        text=(
            "For each AI-attributable value claim, what counterfactual is "
            "modeled? How is the difference between modeled outcome and "
            "current outcome supported?"
        ),
        evidence_query=(
            "Pull current outcome_total_usd_annual and "
            "projected_impact_usd_annual from each opportunity. The "
            "counterfactual is the modeled break-even reroute."
        ),
        answer_template=(
            "Every AI-attributable $ claim is anchored to an explicit "
            "counterfactual: the current cohort run-rate is observed at "
            "row level; the modeled outcome is a reroute to break-even. "
            "Total observed annual loss across documented cohorts: "
            "{total_current_loss}. Total modeled annual recovery if all "
            "cohorts are rerouted: {total_projected_impact}. The delta is "
            "the AI-attributable claim. No claim exists without an observed "
            "loss anchor."
        ),
    ),
    DDQQuestion(
        id="Q07_MRM_DIFFICULTY",
        category="MRM",
        text=(
            "What is the operator-level difficulty of executing each AI "
            "recommendation? Are recommendations within management's "
            "control or do they require policy/regulatory change?"
        ),
        evidence_query=(
            "Aggregate difficulty_score_1_to_5 across opportunities. "
            "Operator-controllable is defined as ≤3."
        ),
        answer_template=(
            "Across {n_opportunities} cohort findings the median execution "
            "difficulty is {median_difficulty}/5; {n_low_difficulty} of "
            "{n_opportunities} findings ({low_difficulty_pct}%) are rated "
            "≤3 and therefore operator-controllable without underwriting "
            "policy or regulatory change. Findings rated 4-5 require senior "
            "operator sign-off and are flagged in the proof pack."
        ),
    ),
    DDQQuestion(
        id="Q08_VEND_THIRD_PARTY",
        category="VEND",
        text=(
            "List third-party AI vendors and dependencies. Describe the "
            "fund's process for evaluating vendor AI risk and contractual "
            "rights to model output / data."
        ),
        evidence_query=(
            "No structured vendor registry artifact is currently in the "
            "knowledge base. Acknowledge the gap and point to the existing "
            "AI Act audit packs as the closest documented surface."
        ),
        answer_template=(
            "The fund's structured AI evidence layer documents in-scope "
            "first-party portco AI systems and their EU AI Act posture "
            "({n_ai_act} portcos covered). A consolidated third-party AI "
            "vendor registry is maintained at the portco operating level "
            "and surfaced into diligence on request; the fund-level "
            "knowledge base does not currently maintain a unified vendor "
            "registry sidecar — this is on the FY26 roadmap. Where vendor "
            "AI is material to a portco's AI EBITDA contribution, the "
            "exit-proof pack discloses the dependency."
        ),
    ),
    DDQQuestion(
        id="Q09_REG_EU_AI_ACT",
        category="REG",
        text=(
            "Identify portfolio companies subject to the EU AI Act. For "
            "each, provide the high-risk classification verdict and the "
            "compliance deadline."
        ),
        evidence_query=(
            "Pull portco_id, high_risk_classification, and deadline from "
            "every ai_act_audit_*.json."
        ),
        answer_template=(
            "{n_ai_act} portfolio companies are within EU AI Act scope. "
            "Per-portco classification and deadline: {ai_act_summary}. "
            "Each in-scope portco maintains a structured audit pack "
            "covering Article 6 classification, Article 9 risk management, "
            "Article 10 data governance, Article 11 technical documentation, "
            "and Articles 12–15 (logging, transparency, oversight, accuracy)."
        ),
    ),
    DDQQuestion(
        id="Q10_REG_DISCLOSURE_RISK",
        category="REG",
        text=(
            "Identify any portfolio company with disclosed AI-related risk "
            "factors in regulatory filings (10-K Item 1A or equivalent)."
        ),
        evidence_query=(
            "Scan cim_redflags_*.json for severe_risk_factor flags whose "
            "excerpt contains 'AI', 'artificial intelligence', or 'machine "
            "learning'. Cite company_name, form, and high-severity flag count."
        ),
        answer_template=(
            "The fund has filed structured CIM red-flag analyses for "
            "{n_cim} portfolio companies. AI-adjacent disclosure risk "
            "summary: {cim_ai_summary}. High-severity risk factors are "
            "tracked at the paragraph level and surfaced into the "
            "exit-proof pack at the portco level."
        ),
    ),
    DDQQuestion(
        id="Q11_VAL_ATTRIBUTION",
        category="VAL",
        text=(
            "How does the fund attribute EBITDA contribution to AI? "
            "Provide the methodology, the headline AI-attributable figure, "
            "and the sensitivity range."
        ),
        evidence_query=(
            "Pull headline_total_usd_annual and sensitivity range from "
            "every exit_proof_pack_*.json. Methodology is the seller-side "
            "proof pack: provenance ledger + counterfactual + sensitivity."
        ),
        answer_template=(
            "AI-attributable EBITDA contribution is computed at the portco "
            "level via the fund's seller-side proof-pack methodology: "
            "every $ traces to a DX OpportunityMap cohort, a row-level "
            "counterfactual, and a 50% / 100% / 130% sensitivity band. "
            "Across {n_exit_packs} portcos with proof packs on file, "
            "headline base-case AI EBITDA contribution is "
            "{exit_total_base}; the fund-wide sensitivity range is "
            "{exit_total_conservative} (conservative) to "
            "{exit_total_aggressive} (aggressive)."
        ),
    ),
    DDQQuestion(
        id="Q12_EXIT_DEFENSIBILITY",
        category="EXIT",
        text=(
            "For portfolio companies in or near exit, describe the AI "
            "diligence posture pre-banker engagement. What evidence is "
            "pre-disclosed?"
        ),
        evidence_query=(
            "Aggregate would_buyer_challenge flags across "
            "exit_proof_pack_*.json provenance ledgers. Pre-disclosed "
            "evidence is the proof pack itself."
        ),
        answer_template=(
            "Pre-banker engagement, every portco with material AI EBITDA "
            "contribution generates a structured exit-proof pack covering "
            "(i) provenance ledger, (ii) methodology disclosure, (iii) "
            "sensitivity table, (iv) defensibility checklist. {n_exit_packs} "
            "such packs are on file. Across these packs, "
            "{n_challenge_flags} of {n_exit_claims} documented claims are "
            "self-flagged as 'buyer would likely challenge' — meaning the "
            "seller carries extra evidence into the banker meeting rather "
            "than letting the buyer surface the question first."
        ),
    ),
)


def get_questions() -> tuple[DDQQuestion, ...]:
    """Return the frozen DDQ question set. Treat as immutable per release."""
    return QUESTIONS
