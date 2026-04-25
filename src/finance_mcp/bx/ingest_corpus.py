"""
bx_ingest_corpus — Load N OpportunityMap JSON sidecars into a benchmark session.

Each input JSON is an OpportunityMap (schema documented in
docs/opportunity_map_schema.md). We:
  1. Validate each has the required top-level fields.
  2. Derive a PortcoProfile (aggregated metrics) per JSON.
  3. Flatten all opportunities into a long-format dataframe for
     downstream archetype + peer-group analysis.
  4. Stash both dataframes in a BenchmarkSession identified by corpus_id.

Pandas-only. No network, no external deps.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from statistics import median
from typing import Iterable

import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.bx.session import BenchmarkSession, save_session


REQUIRED_TOP_LEVEL_FIELDS = (
    "portco_id",
    "vertical",
    "ebitda_baseline_usd",
    "as_of",
    "opportunities",
    "total_projected_impact_usd_annual",
)

REQUIRED_OPP_FIELDS = (
    "id",
    "archetype",
    "projected_impact_usd_annual",
    "persistence_quarters_out_of_total",
    "difficulty_score_1_to_5",
)

ARCHETYPES = ("allocation", "pricing", "routing", "timing", "selection")


def _validate_opportunity_map(data: dict, source: str) -> None:
    missing = [f for f in REQUIRED_TOP_LEVEL_FIELDS if f not in data]
    if missing:
        raise ToolError(
            f"OpportunityMap at {source!r} is missing required fields: {missing}"
        )
    if not isinstance(data["opportunities"], list):
        raise ToolError(f"'opportunities' in {source!r} must be a list.")
    for i, opp in enumerate(data["opportunities"]):
        opp_missing = [f for f in REQUIRED_OPP_FIELDS if f not in opp]
        if opp_missing:
            raise ToolError(
                f"Opportunity #{i} in {source!r} missing: {opp_missing}"
            )


def _profile_from_map(data: dict) -> dict:
    """Compute the aggregated PortcoProfile fields from one OpportunityMap."""
    opps = data.get("opportunities") or []
    ebitda = float(data.get("ebitda_baseline_usd", 0.0) or 0.0)
    total = float(data.get("total_projected_impact_usd_annual", 0.0) or 0.0)
    impacts = [float(o.get("projected_impact_usd_annual", 0.0) or 0.0) for o in opps]
    top3 = sorted(impacts, reverse=True)[:3]
    top3_sum = sum(top3)

    persistences: list[float] = []
    for o in opps:
        p = o.get("persistence_quarters_out_of_total") or [0, 0]
        persist, total_q = p[0] or 0, p[1] or 0
        if total_q > 0:
            persistences.append(persist / total_q)

    difficulties = [
        int(o.get("difficulty_score_1_to_5") or 3) for o in opps
    ]

    per_archetype_totals = {a: 0.0 for a in ARCHETYPES}
    for o, imp in zip(opps, impacts):
        arche = str(o.get("archetype") or "").lower()
        if arche in per_archetype_totals:
            per_archetype_totals[arche] += imp

    return {
        "portco_id": str(data.get("portco_id") or ""),
        "vertical": str(data.get("vertical") or "custom"),
        "ebitda_baseline_usd": ebitda,
        "as_of": str(data.get("as_of") or ""),
        "total_projected_impact_usd_annual": total,
        "pct_of_ebitda": (total / ebitda * 100.0) if ebitda else 0.0,
        "opportunity_count": len(opps),
        "median_opportunity_usd": float(median(impacts)) if impacts else 0.0,
        "top3_coverage_pct": (top3_sum / total * 100.0) if total else 0.0,
        "median_persistence_score": (
            float(median(persistences)) if persistences else 0.0
        ),
        "median_difficulty": float(median(difficulties)) if difficulties else 3.0,
        **{
            f"{a}_impact_usd": per_archetype_totals[a] for a in ARCHETYPES
        },
    }


def _opportunities_long(data: dict) -> list[dict]:
    """Flatten one OpportunityMap's opportunities into dict rows."""
    rows = []
    portco_id = str(data.get("portco_id") or "")
    vertical = str(data.get("vertical") or "custom")
    for o in data.get("opportunities") or []:
        persist = o.get("persistence_quarters_out_of_total") or [0, 0]
        persist_total = int(persist[1] or 0)
        rows.append(
            {
                "portco_id": portco_id,
                "vertical": vertical,
                "opp_id": str(o.get("id") or ""),
                "archetype": str(o.get("archetype") or "").lower(),
                "segment_json": json.dumps(o.get("segment") or {}, sort_keys=True),
                "projected_impact_usd_annual": float(
                    o.get("projected_impact_usd_annual", 0.0) or 0.0
                ),
                "persistence_quarters": int(persist[0] or 0),
                "persistence_total_quarters": persist_total,
                "persistence_score": (
                    (int(persist[0] or 0) / persist_total)
                    if persist_total
                    else 0.0
                ),
                "difficulty_score": int(o.get("difficulty_score_1_to_5") or 3),
                "time_to_implement_weeks": int(
                    o.get("time_to_implement_weeks") or 0
                ),
            }
        )
    return rows


def bx_ingest_corpus(
    json_paths: list[str],
    corpus_id: str = "",
) -> dict:
    """
    Load multiple OpportunityMap JSON sidecars into a benchmark session.

    Args:
        json_paths: Absolute or relative paths to dx_report JSON sidecars.
        corpus_id:  Optional label for this corpus. Auto-generated if empty.

    Returns:
        dict with corpus_id, portco_count, per-portco summary, warnings.
    """
    if not json_paths:
        raise ToolError("bx_ingest_corpus requires at least one JSON path.")

    # Validate paths
    for p in json_paths:
        if not os.path.exists(p):
            raise ToolError(f"OpportunityMap JSON not found: {p}")

    profiles: list[dict] = []
    opportunity_rows: list[dict] = []
    warnings: list[str] = []

    for path in json_paths:
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ToolError(f"Failed to parse {path!r}: {e}") from e
        _validate_opportunity_map(data, path)
        profiles.append(_profile_from_map(data))
        opportunity_rows.extend(_opportunities_long(data))

    # Deduplicate by portco_id — later JSONs win if same portco_id present
    seen: set[str] = set()
    deduped: list[dict] = []
    for p in reversed(profiles):
        if p["portco_id"] in seen:
            warnings.append(
                f"duplicate portco_id {p['portco_id']!r} — keeping only the "
                f"most recent JSON for that id"
            )
            continue
        seen.add(p["portco_id"])
        deduped.append(p)
    deduped.reverse()

    profiles_df = pd.DataFrame(deduped)
    opps_df = pd.DataFrame(opportunity_rows)
    # Filter opportunity rows to the deduped portco set
    opps_df = opps_df[opps_df["portco_id"].isin(seen)].reset_index(drop=True)

    cid = corpus_id or f"bx_{uuid.uuid4().hex[:8]}"
    session = BenchmarkSession(
        corpus_id=cid,
        portco_count=len(deduped),
        portco_profiles_df=profiles_df,
        opportunities_df=opps_df,
        created_at=datetime.now(timezone.utc),
        source_json_paths=tuple(json_paths),
    )
    save_session(session)

    return {
        "corpus_id": cid,
        "portco_count": session.portco_count,
        "opportunity_count": len(opps_df),
        "portcos": [
            {
                "portco_id": p["portco_id"],
                "vertical": p["vertical"],
                "total_projected_impact_usd_annual": round(
                    p["total_projected_impact_usd_annual"], 2
                ),
                "opportunity_count": p["opportunity_count"],
            }
            for p in deduped
        ],
        "warnings": warnings,
    }
