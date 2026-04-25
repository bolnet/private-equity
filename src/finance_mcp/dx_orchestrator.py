"""
DX orchestrator — runs the full Decision-Optimization Diagnostic pipeline
on a list of uploaded CSV paths and returns an HTML report path.

Mirrors what Claude does step-by-step in `/diagnose-decisions`, but uses
template-supplied defaults (decision columns, archetypes) so the pipeline
can run unattended from a web UI upload.

Stages emitted on the `progress` callback:
    ingest, segment_stats, time_stability, counterfactual,
    evidence, memo, report
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from fastmcp.exceptions import ToolError

from finance_mcp.dx.counterfactual import dx_counterfactual
from finance_mcp.dx.evidence import dx_evidence_rows
from finance_mcp.dx.ingest import dx_ingest
from finance_mcp.dx.memo import dx_memo
from finance_mcp.dx.report import dx_report
from finance_mcp.dx.segment_stats import dx_segment_stats
from finance_mcp.dx.templates import get_template
from finance_mcp.dx.time_stability import dx_time_stability


ProgressCallback = Callable[[str, dict], None]


# Per-template defaults for unattended runs. The real `/diagnose-decisions`
# command lets Claude pick these based on the data; the UI uses these.
_ARCHETYPE_PRIORITY = {
    "insurance_b2c": "allocation",
    "saas_pricing": "pricing",
    "lending_b2c": "selection",
}

# EBITDA baseline defaults (USD) — demo datasets were sized around these.
_EBITDA_DEFAULTS = {
    "insurance_b2c": 1_200_000.0,
    "saas_pricing": 40_000_000.0,
    # 30k loans × ~$486 mean net / loan ≈ $14.6M realized book contribution
    # per vintage; treat that as the EBITDA baseline the opportunity map is
    # measured against.
    "lending_b2c": 14_500_000.0,
}


@dataclass(frozen=True)
class DiagnosticResult:
    report_path: str
    json_path: str
    session_id: str
    template_id: str
    opportunities_rendered: int
    total_impact_usd_annual: float


def _pick_decision_cols(template_id: str) -> list[str]:
    template = get_template(template_id)
    preferred = _ARCHETYPE_PRIORITY.get(template_id)
    for arch in template.archetypes:
        if preferred and arch.archetype == preferred:
            return list(arch.decision_columns)
    if template.archetypes:
        return list(template.archetypes[0].decision_columns)
    raise ToolError(f"Template {template_id} has no archetypes.")


def _build_opportunity(
    idx: int,
    segment_stat: dict,
    ts: dict,
    cf: dict,
    ev: dict,
    decision_cols: list[str],
    archetype: str,
) -> dict:
    segment = segment_stat["segment"]
    return {
        "id": f"opp_{idx:02d}",
        "archetype": archetype,
        "decision_cols": decision_cols,
        "segment": segment,
        "n": int(ev["total_matched"]),
        "current_outcome_usd_annual": float(cf["current_outcome_usd_annual"]),
        "projected_outcome_usd_annual": float(cf["projected_outcome_usd_annual"]),
        "projected_impact_usd_annual": float(cf["projected_impact_usd_annual"]),
        "persistence_quarters_out_of_total": [
            int(ts["persistence_quarters"]),
            int(ts["total_quarters"]),
        ],
        "difficulty_score_1_to_5": 2,
        "time_to_implement_weeks": 3,
        "recommendation": (
            f"Throttle {' × '.join(str(v) for v in segment.values())} — "
            f"replace current negative outcome with break-even routing."
        ),
        "evidence_row_ids": [str(r["row_id"]) for r in ev["evidence_rows"]],
        "narrative_board": "",
        "narrative_operator": "",
    }


def run_diagnostic(
    data_paths: list[str],
    portco_id: str = "uploaded",
    top_k_opportunities: int = 3,
    ebitda_baseline_usd: Optional[float] = None,
    output_filename: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
) -> DiagnosticResult:
    """
    Run the full DX pipeline on uploaded CSV files and render an HTML report.

    Args:
        data_paths: Absolute paths to CSV files (e.g., leads.csv + policies.csv).
        portco_id: Label for this run, used in the report title.
        top_k_opportunities: Max opportunities to surface (top-worst cells).
        ebitda_baseline_usd: Optional. Defaults per template if omitted.
        output_filename: Optional HTML filename (basename only).
        progress: Optional callback invoked with (stage_name, payload) between stages.

    Returns:
        DiagnosticResult with report_path (absolute) + summary metadata.

    Raises:
        ToolError if ingest gates fail or no viable opportunities found.
    """

    def _emit(stage: str, payload: dict) -> None:
        if progress is not None:
            progress(stage, payload)

    # Stage 1: ingest
    ingest = dx_ingest(
        data_paths=data_paths, vertical="auto", portco_id=portco_id
    )
    _emit("ingest", {
        "template_id": ingest["template_id"],
        "rows": ingest["joined_rows"],
        "months_coverage": ingest["months_coverage"],
        "gates_failed": list(ingest["gates_failed"]),
    })
    if ingest["gates_failed"]:
        raise ToolError(
            "Ingest validation gates failed: "
            + "; ".join(ingest["gates_failed"])
        )

    session_id = ingest["session_id"]
    template_id = ingest["template_id"]
    archetype = _ARCHETYPE_PRIORITY.get(template_id, "allocation")
    decision_cols = _pick_decision_cols(template_id)

    # Stage 2: segment stats (top-worst)
    stats = dx_segment_stats(
        session_id=session_id,
        decision_cols=decision_cols,
        min_segment_n=20,
        top_k=max(top_k_opportunities * 3, 10),
        rank_by="worst_total",
    )
    _emit("segment_stats", {
        "decision_cols": decision_cols,
        "segments_found": len(stats["segments"]),
    })
    if not stats["segments"]:
        raise ToolError(
            f"No segments with n>=20 found for decision cols {decision_cols}."
        )

    # Stages 3-6 per opportunity: time_stability, counterfactual, evidence, memo
    opportunities: list[dict] = []
    for idx, seg in enumerate(stats["segments"][:top_k_opportunities]):
        if seg["outcome_total_usd_annual"] >= 0:
            continue  # only surface losing segments

        segment_filter = seg["segment"]
        try:
            ts = dx_time_stability(
                session_id=session_id,
                segment_filter=segment_filter,
                direction="negative",
            )
            _emit("time_stability", {
                "opp": idx,
                "persistence_score": ts["persistence_score"],
            })

            cf = dx_counterfactual(
                session_id=session_id,
                segment_filter=segment_filter,
                action="reroute",
                action_params={"outcome_replacement": 0.0},
            )
            _emit("counterfactual", {
                "opp": idx,
                "projected_impact_usd_annual": cf["projected_impact_usd_annual"],
            })

            ev = dx_evidence_rows(
                session_id=session_id,
                segment_filter=segment_filter,
                limit=5,
            )
            _emit("evidence", {"opp": idx, "rows": ev["rows_returned"]})

            opp = _build_opportunity(
                idx + 1, seg, ts, cf, ev, decision_cols, archetype
            )
            memo = dx_memo(opp, audience="board")
            opp["narrative_board"] = memo.get("narrative", "")
            operator_memo = dx_memo(opp, audience="operator")
            opp["narrative_operator"] = operator_memo.get("narrative", "")
            _emit("memo", {"opp": idx, "validated": memo.get("validated", False)})

            opportunities.append(opp)
        except Exception as exc:  # skip broken segments, keep going
            _emit("memo", {"opp": idx, "skipped": str(exc)})
            continue

    if not opportunities:
        raise ToolError(
            "No viable loss-making segments surfaced after stability and "
            "counterfactual checks."
        )

    # Stage 7: render report
    ebitda = ebitda_baseline_usd or _EBITDA_DEFAULTS.get(
        template_id, 10_000_000.0
    )
    total_impact = sum(
        o["projected_impact_usd_annual"] for o in opportunities
    )
    opp_map = {
        "portco_id": portco_id,
        "vertical": template_id,
        "ebitda_baseline_usd": ebitda,
        "as_of": date.today().isoformat(),
        "opportunities": opportunities,
        "total_projected_impact_usd_annual": total_impact,
    }
    report = dx_report(opp_map, output_filename=output_filename or "")
    _emit("report", {
        "path": report["path"],
        "opportunities_rendered": report["opportunities_rendered"],
    })

    return DiagnosticResult(
        report_path=report["path"],
        json_path=report["json_path"],
        session_id=session_id,
        template_id=template_id,
        opportunities_rendered=report["opportunities_rendered"],
        total_impact_usd_annual=total_impact,
    )
