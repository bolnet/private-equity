"""
Build a BX corpus for a *fund of regional mortgage origination portcos* — five
US-state mortgage books, all real CFPB HMDA 2023 data, all home-purchase
originations + denials. Apples-to-apples cross-portco comparison: same
vertical, different markets.

Source: CFPB HMDA 2023, fetched via the public data-browser API (no auth):
    https://ffiec.cfpb.gov/v2/data-browser-api/view/csv
        ?years=2023&states=<XX>&actions_taken=1,3&loan_purposes=1

Five portcos:
    DC · DE · MA · AZ · GA   (different markets, same loan product)

Usage:
    # 1. Fetch raw CSVs (one-time):
    for s in DC DE MA AZ GA; do
        curl -sSL -o /tmp/hmda_${s}_2023_purchase.csv \\
          "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv?years=2023&states=${s}&actions_taken=1,3&loan_purposes=1"
    done

    # 2. Slice each:
    for s in DC DE MA AZ GA; do python -m demo.hmda_states.slice --state $s; done

    # 3. Build the corpus (DX × 5 with multi-archetype, then BX):
    python -m scripts.build_bx_hmda_states               # full DX + BX
    python -m scripts.build_bx_hmda_states --skip-dx     # reuse JSON sidecars

Output:
    finance_output/dx_report_HMDA_<XX>.html / .json   (one per state)
    finance_output/bx_report_hmda_states.html
    finance_output/bx_report_hmda_states.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO_ROOT / "finance_output"
CORPUS_ID = "hmda_states"

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

STATES = ["DC", "DE", "MA", "AZ", "GA"]

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


def _build_opportunity(idx, archetype, decision_cols, seg_stat, ts, cf, ev):
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


def _run_dx_multi_archetype(state: str) -> Path:
    portco_id = f"HMDA_{state}"
    state_dir = REPO_ROOT / "demo" / "hmda_states" / state.lower()
    loans = state_dir / "loans.csv"
    perf = state_dir / "performance.csv"
    if not (loans.exists() and perf.exists()):
        raise SystemExit(
            f"Missing CSVs under {state_dir}. "
            f"Run `python -m demo.hmda_states.slice --state {state}` first."
        )

    print(f"[dx] {portco_id}: ingesting…")
    ingest = dx_ingest(
        data_paths=[str(loans), str(perf)],
        vertical="auto",
        portco_id=portco_id,
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
        loss_segments = [s for s in stats["segments"]
                         if s["outcome_total_usd_annual"] < 0]
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
                f"[dx] {portco_id}: archetype={arch_spec.archetype} skipped ({exc})"
            )
            continue

        idx += 1
        opp = _build_opportunity(idx, arch_spec.archetype, decision_cols,
                                  seg, ts, cf, ev)
        try:
            opp["narrative_board"] = dx_memo(opp, audience="board").get("narrative", "")
            opp["narrative_operator"] = dx_memo(opp, audience="operator").get("narrative", "")
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
    return [p for p in (
        OUT_ROOT / f"dx_report_HMDA_{s}.json" for s in STATES
    ) if p.exists()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-dx", action="store_true")
    args = parser.parse_args()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    if args.skip_dx:
        json_paths = _existing_jsons()
        if len(json_paths) != len(STATES):
            sys.exit(
                f"Expected {len(STATES)} sidecars, found {len(json_paths)}. "
                "Re-run without --skip-dx."
            )
    else:
        json_paths = [_run_dx_multi_archetype(s) for s in STATES]

    print(f"\n[bx] ingesting {len(json_paths)} OpportunityMaps into corpus…")
    ingest = bx_ingest_corpus(
        json_paths=[str(p) for p in json_paths],
        corpus_id=CORPUS_ID,
    )
    print(f"[bx] corpus_id={ingest['corpus_id']}  portcos={ingest['portco_count']}")

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
                f"       #{r['rank']}/{r['rank_total']}  {r['portco_id']:<14} "
                f"${r['value']:>14,.0f}  ({r['percentile']:>5.1f}th pct)"
            )


if __name__ == "__main__":
    main()
