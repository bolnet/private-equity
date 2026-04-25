"""
Build the BX cross-portco corpus demo end-to-end:

1. For each regional lender slice in ``demo/regional_lenders/<slug>/``,
   run the DX diagnostic to produce an OpportunityMap JSON sidecar.
2. Ingest all 5 OpportunityMaps into a BX corpus.
3. Render the LP-facing benchmark HTML + JSON sidecar.

Usage:
    python -m scripts.build_bx_corpus            # full run, defaults
    python -m scripts.build_bx_corpus --skip-dx  # only re-run BX, reuse JSONs

Idempotent: running twice will overwrite outputs in finance_output/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = REPO_ROOT / "demo" / "regional_lenders"
OUT_ROOT = REPO_ROOT / "finance_output"
CORPUS_ID = "regional_lenders_demo"

# Survives even if src/ isn't on PYTHONPATH (run via `python -m scripts....`).
sys.path.insert(0, str(REPO_ROOT / "src"))

from datetime import date  # noqa: E402

from finance_mcp.bx import (  # noqa: E402  (after sys.path manipulation)
    bx_ingest_corpus,
    bx_report,
)
from finance_mcp.dx.counterfactual import dx_counterfactual  # noqa: E402
from finance_mcp.dx.evidence import dx_evidence_rows  # noqa: E402
from finance_mcp.dx.ingest import dx_ingest  # noqa: E402
from finance_mcp.dx.memo import dx_memo  # noqa: E402
from finance_mcp.dx.report import dx_report  # noqa: E402
from finance_mcp.dx.segment_stats import dx_segment_stats  # noqa: E402
from finance_mcp.dx.templates import get_template  # noqa: E402
from finance_mcp.dx.time_stability import dx_time_stability  # noqa: E402

# Per-template EBITDA baseline (matches dx_orchestrator._EBITDA_DEFAULTS).
_EBITDA_BASELINE: dict[str, float] = {
    "lending_b2c": 14_500_000.0,
    "saas_pricing": 40_000_000.0,
    "insurance_b2c": 1_200_000.0,
}


_DIFFICULTY_BY_ARCHETYPE: dict[str, int] = {
    # Rough heuristic: pricing changes are board-level (hardest, regulator-facing),
    # selection / allocation changes are policy throttles (easier).
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
    """Construct one opportunity dict tagged with its archetype."""
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
            f"replace current negative outcome with break-even routing."
        ),
        "evidence_row_ids": [str(r["row_id"]) for r in ev["evidence_rows"]],
        "narrative_board": "",
        "narrative_operator": "",
    }


def _run_dx_multi_archetype(region_dir: Path) -> Path:
    """
    Run DX on one region, surfacing the worst loss-making segment for *each*
    archetype defined on the matched template. Returns the path to the
    OpportunityMap JSON sidecar.

    The built-in ``run_diagnostic`` only uses the priority archetype; for
    cross-portco BX we want findings spread across pricing/selection/allocation
    so the corpus-level archetype distribution is meaningful.
    """
    loans = region_dir / "loans.csv"
    perf = region_dir / "performance.csv"
    if not (loans.exists() and perf.exists()):
        raise FileNotFoundError(
            f"Missing CSVs under {region_dir}. "
            f"Run `python -m demo.regional_lenders.slice` first."
        )

    portco_id = region_dir.name
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
        # Pick the first segment that's actually loss-making for this archetype.
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
        # Memos are deterministic skeletons — fill them so the report HTML
        # renders the operator/board narratives properly.
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
            f"segment={seg['segment']} impact=${cf['projected_impact_usd_annual']:>10,.0f}"
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


def _collect_existing_jsons() -> list[Path]:
    """Find OpportunityMap JSONs already on disk (from a prior DX run)."""
    found = []
    for region_dir in sorted(DEMO_ROOT.iterdir()):
        if not region_dir.is_dir() or region_dir.name.startswith((".", "__")):
            continue
        candidate = OUT_ROOT / f"dx_report_{region_dir.name}.json"
        if candidate.exists():
            found.append(candidate)
    return found


def main(skip_dx: bool) -> None:
    if not DEMO_ROOT.exists():
        raise SystemExit(
            f"Missing {DEMO_ROOT}. Run `python -m demo.regional_lenders.slice` first."
        )

    json_paths: list[Path] = []
    region_dirs = sorted(
        p
        for p in DEMO_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith((".", "__"))
    )

    if skip_dx:
        json_paths = _collect_existing_jsons()
        if not json_paths:
            raise SystemExit(
                "No existing OpportunityMap JSONs found; rerun without --skip-dx."
            )
        print(f"[dx] Reusing {len(json_paths)} OpportunityMaps from {OUT_ROOT}")
    else:
        for region_dir in region_dirs:
            json_paths.append(_run_dx_multi_archetype(region_dir))

    print(f"\n[bx] Ingesting {len(json_paths)} OpportunityMaps into corpus…")
    ingest = bx_ingest_corpus(
        json_paths=[str(p) for p in json_paths],
        corpus_id=CORPUS_ID,
    )
    print(f"[bx] corpus_id={ingest['corpus_id']} portcos={ingest['portco_count']}")

    print("[bx] Rendering corpus report…")
    report = bx_report(corpus_id=CORPUS_ID, output_filename="bx_report_regional_lenders_demo.html")
    print(f"[bx] HTML  → {report['path']}")
    print(f"[bx] JSON  → {report['json_path']}")

    summary = json.loads(Path(report["json_path"]).read_text())
    portcos = summary.get("portcos", [])
    if portcos:
        print("\n[bx] Per-portco totals (annual impact, USD):")
        for p in portcos:
            print(
                f"       {p.get('portco_id', '?'):<22} "
                f"${p.get('total_projected_impact_usd_annual', 0):>14,.0f}"
            )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--skip-dx",
        action="store_true",
        help="Skip DX runs and reuse existing dx_report_*.json sidecars.",
    )
    return p.parse_args()


if __name__ == "__main__":
    main(skip_dx=_parse_args().skip_dx)
