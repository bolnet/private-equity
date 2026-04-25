"""
bx_trend — Per-snapshot trend of identified $ for one portco.

Reads all snapshots from finance_output/snapshots/<portco_id>/ and produces
a time-indexed series of headline metrics. Useful for LP board-meeting
narratives: "We identified $9.7M in Q1; $6.2M in Q2 (after remediating 3
top-3 items); $4.8M in Q3..."
"""
from __future__ import annotations

import json
from pathlib import Path

from fastmcp.exceptions import ToolError

from finance_mcp.bx.snapshot import list_snapshots


def bx_trend(portco_id: str) -> dict:
    """
    Return a per-snapshot trend for the given portco.

    Each entry in `points` has: snapshot_date, total_projected_impact_usd_annual,
    opportunity_count, pct_of_ebitda.

    Returns:
        dict with portco_id + points (chronological) + note if no snapshots.
    """
    if not portco_id.strip():
        raise ToolError("portco_id is required")

    entries = list_snapshots(portco_id)
    if not entries:
        return {
            "portco_id": portco_id,
            "points": [],
            "note": f"No snapshots found for {portco_id!r}.",
        }

    points: list[dict] = []
    for entry in entries:
        with open(entry["path"], "r", encoding="utf-8") as f:
            data = json.load(f)
        ebitda = float(data.get("ebitda_baseline_usd") or 0.0)
        total = float(data.get("total_projected_impact_usd_annual") or 0.0)
        points.append(
            {
                "snapshot_date": entry["snapshot_date"],
                "total_projected_impact_usd_annual": round(total, 2),
                "opportunity_count": len(data.get("opportunities") or []),
                "pct_of_ebitda": round(
                    (total / ebitda * 100.0) if ebitda else 0.0, 2
                ),
            }
        )

    return {
        "portco_id": portco_id,
        "snapshot_count": len(points),
        "points": points,
    }
