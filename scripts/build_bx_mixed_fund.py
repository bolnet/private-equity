"""
Build a BX corpus for a *mixed-vertical fund* — 7 real-data portcos:

    5 consumer-lending portcos    (Lending Club, partitioned by US region)
  + 1 specialty-mortgage portco   (Yasserh / Kaggle CC0, 2019 US mortgages)
  + 1 mortgage-origination portco (CFPB HMDA, Washington DC, 2023)

All seven are mapped onto the `lending_b2c` template so archetype indices
(pricing × selection × allocation) line up across portcos. DX is run with
the multi-archetype helper so each portco's OpportunityMap covers all
three archetypes — matching the 5-portco regional_lenders demo's pattern.

Usage:
    python -m scripts.build_bx_mixed_fund               # full DX + BX run
    python -m scripts.build_bx_mixed_fund --skip-dx     # reuse JSON sidecars

Output:
    finance_output/dx_report_<portco>.html / .json   (one per portco)
    finance_output/bx_report_mixed_fund.html
    finance_output/bx_report_mixed_fund.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO_ROOT / "finance_output"
CORPUS_ID = "mixed_fund"

sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_mcp.bx import bx_ingest_corpus, bx_report  # noqa: E402
from finance_mcp.dx.counterfactual import dx_counterfactual  # noqa: E402
from finance_mcp.dx.evidence import dx_evidence_rows  # noqa: E402
from finance_mcp.dx.ingest import dx_ingest  # noqa: E402
from finance_mcp.dx.memo import dx_memo  # noqa: E402
from finance_mcp.dx.report import dx_report  # noqa: E402
from finance_mcp.dx.segment_stats import dx_segment_stats  # noqa: E402
from finance_mcp.dx.templates import get_template  # noqa: E402
from finance_mcp.dx.time_stability import dx_time_stability  # noqa: E402

# (portco_id, csv_paths_relative_to_repo_root)
PORTCOS = [
    ("midwest_lender",   ["demo/regional_lenders/midwest_lender/loans.csv",
                          "demo/regional_lenders/midwest_lender/performance.csv"]),
    ("mountain_lender",  ["demo/regional_lenders/mountain_lender/loans.csv",
                          "demo/regional_lenders/mountain_lender/performance.csv"]),
    ("northeast_lender", ["demo/regional_lenders/northeast_lender/loans.csv",
                          "demo/regional_lenders/northeast_lender/performance.csv"]),
    ("pacific_lender",   ["demo/regional_lenders/pacific_lender/loans.csv",
                          "demo/regional_lenders/pacific_lender/performance.csv"]),
    ("southeast_lender", ["demo/regional_lenders/southeast_lender/loans.csv",
                          "demo/regional_lenders/southeast_lender/performance.csv"]),
    ("MortgageCo",       ["demo/yasserh_mortgages/loans.csv",
                          "demo/yasserh_mortgages/performance.csv"]),
    ("DCMortgage",       ["demo/hmda_dc/loans.csv",
                          "demo/hmda_dc/performance.csv"]),
]

_EBITDA_BASELINE: dict[str, float] = {
    "lending_b2c": 14_500_000.0,
    "saas_pricing": 40_000_000.0,
    "insurance_b2c": 1_200_000.0,
}

_DIFFICULTY_BY_ARCHETYPE: dict[str, int] = {
    "pricing": 4,
    "selection": 2,
    "allocation": 2,
    "routing": 3,
    "timing": 3,
}


def _build_opportunity(
    idx: int,
    archetype: str,
    decision_cols: list[str],
    seg_stat: dict,
    ts: dict,
    cf: dict,
    ev: dict,
) -> dict:
    segment = seg_stat["segment"]
    persist_q = int(ts.get("persistence_quarters") or 0)
    total_q = int(ts.get("total_quarters") or 0)
    return {
        "id": f"opp_{idx:02d}",
        "archetype": archetype,
        "decision_cols": decision_cols,
        "segment": segment,
        "n": int(ev["total_matched"]),
        "outcome_total_usd_annual": float(seg_stat["outcome_total_usd_annual"]),
        "outcome_mean_usd": float(seg_stat["outcome_mean"]),
        "persistence_score": float(ts["persistence_score"]),
        "persistence_quarters_out_of_total": [persist_q, total_q],
        "difficulty_score_1_to_5": _DIFFICULTY_BY_ARCHETYPE.get(archetype, 3),
        "projected_impact_usd_annual": float(cf["projected_impact_usd_annual"]),
        "projected_action": (
            f"Throttle {' × '.join(str(v) for v in segment.values())} — "
            "replace current negative outcome with break-even routing."
        ),
        "evidence_row_ids": [str(r["row_id"]) for r in ev["evidence_rows"]],
        "narrative_board": "",
        "narrative_operator": "",
    }


def _run_dx_multi_archetype(portco_id: str, data_paths: list[str]) -> Path:
    """Run DX on one portco surfacing the worst loss-making segment for *each*
    archetype defined on the matched template. Returns the path to the
    OpportunityMap JSON sidecar."""
    abs_paths = [str(REPO_ROOT / p) for p in data_paths]
    print(f"[dx] {portco_id}: ingesting…")
    ingest = dx_ingest(
        data_paths=abs_paths, vertical="auto", portco_id=portco_id
    )
    if ingest["gates_failed"]:
        raise RuntimeError(
            f"{portco_id}: ingest gates failed: {ingest['gates_failed']}"
        )
    session_id = ingest["session_id"]
    template_id = ingest["template_id"]
    template = get_template(template_id)

    opportunities: list[dict] = []
    idx = 0
    for arch_spec in template.archetypes:
        decision_cols = list(arch_spec.decision_columns)
        stats = dx_segment_stats(
            session_id=session_id,
            decision_cols=decision_cols,
            min_segment_n=20,
            top_k=10,
            rank_by="worst_total",
        )
        loss_segments = [
            s for s in stats["segments"] if s["outcome_total_usd_annual"] < 0
        ]
        if not loss_segments:
            print(
                f"[dx] {portco_id}: archetype={arch_spec.archetype} "
                f"no loss-making segment, skipping"
            )
            continue

        seg = loss_segments[0]
        try:
            ts = dx_time_stability(
                session_id=session_id,
                segment_filter=seg["segment"],
                direction="negative",
            )
            cf = dx_counterfactual(
                session_id=session_id,
                segment_filter=seg["segment"],
                action="reroute",
                action_params={"outcome_replacement": 0.0},
            )
            ev = dx_evidence_rows(
                session_id=session_id,
                segment_filter=seg["segment"],
                limit=5,
            )
        except Exception as exc:
            print(
                f"[dx] {portco_id}: archetype={arch_spec.archetype} "
                f"skipped ({exc})"
            )
            continue

        idx += 1
        opp = _build_opportunity(
            idx, arch_spec.archetype, decision_cols, seg, ts, cf, ev
        )
        try:
            opp["narrative_board"] = dx_memo(opp, audience="board").get(
                "narrative", ""
            )
            opp["narrative_operator"] = dx_memo(opp, audience="operator").get(
                "narrative", ""
            )
        except Exception:
            pass
        opportunities.append(opp)
        print(
            f"[dx] {portco_id}: archetype={arch_spec.archetype:<10} "
            f"segment={seg['segment']} "
            f"impact=${cf['projected_impact_usd_annual']:>13,.0f}"
        )

    if not opportunities:
        raise RuntimeError(f"{portco_id}: no viable opportunities found.")

    total_impact = sum(o["projected_impact_usd_annual"] for o in opportunities)
    opp_map = {
        "portco_id": portco_id,
        "vertical": template_id,
        "ebitda_baseline_usd": _EBITDA_BASELINE.get(template_id, 10_000_000.0),
        "as_of": date.today().isoformat(),
        "opportunities": opportunities,
        "total_projected_impact_usd_annual": total_impact,
    }
    report = dx_report(opp_map, output_filename=f"dx_report_{portco_id}.html")
    print(
        f"[dx] {portco_id}: rendered {report['opportunities_rendered']} ops, "
        f"impact=${total_impact:,.0f} → {report['json_path']}"
    )
    return Path(report["json_path"])


def _existing_jsons() -> list[Path]:
    paths = []
    for portco_id, _ in PORTCOS:
        p = OUT_ROOT / f"dx_report_{portco_id}.json"
        if p.exists():
            paths.append(p)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-dx",
        action="store_true",
        help="Skip DX runs and reuse existing dx_report_*.json sidecars.",
    )
    args = parser.parse_args()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    json_paths: list[Path]
    if args.skip_dx:
        json_paths = _existing_jsons()
        if len(json_paths) != len(PORTCOS):
            sys.exit(
                f"Expected {len(PORTCOS)} sidecars, found {len(json_paths)}. "
                "Re-run without --skip-dx."
            )
        print(f"[dx] Reusing {len(json_paths)} OpportunityMaps from {OUT_ROOT}")
    else:
        json_paths = []
        for portco_id, data_paths in PORTCOS:
            json_paths.append(_run_dx_multi_archetype(portco_id, data_paths))

    print(f"\n[bx] ingesting {len(json_paths)} OpportunityMaps into corpus…")
    ingest = bx_ingest_corpus(
        json_paths=[str(p) for p in json_paths],
        corpus_id=CORPUS_ID,
    )
    print(f"[bx] corpus_id={ingest['corpus_id']}  portcos={ingest['portco_count']}")
    for w in ingest.get("warnings", []):
        print(f"[bx] warning: {w}")

    print("[bx] rendering corpus report…")
    rep = bx_report(
        corpus_id=ingest["corpus_id"],
        output_filename=f"bx_report_{CORPUS_ID}.html",
    )
    print(f"[bx] HTML  → {rep['path']}")
    print(f"[bx] JSON  → {rep['json_path']}")

    summary = json.loads(Path(rep["json_path"]).read_text())
    if "rank_table" in summary:
        ranks = sorted(summary["rank_table"], key=lambda r: r.get("rank", 99))
        print("\n[bx] Per-portco ranking:")
        for r in ranks:
            print(
                f"       #{r['rank']}/{r['rank_total']}  {r['portco_id']:<22} "
                f"${r['value']:>14,.0f}  ({r['percentile']:>5.1f}th pct)"
            )


if __name__ == "__main__":
    main()
