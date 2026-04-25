"""
bx_delta — What changed in a portco's opportunity set between two snapshots?

Classifies every opportunity across the two snapshots as:
  • closed      — present in `from` snapshot, absent in `to` snapshot
  • new         — absent in `from`, present in `to`
  • persistent  — present in both (by id)

Headline aggregates: delta in total impact, delta in pct_of_ebitda, days elapsed.

Used to frame LP narratives: "Since Q1, 3 top-3 opportunities were closed
(–$9.7M off the books), 2 new ones surfaced (+$1.1M), 4 remain open."
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastmcp.exceptions import ToolError

from finance_mcp.bx.snapshot import list_snapshots


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _opp_ids(data: dict) -> set[str]:
    return {
        str(o.get("id") or "")
        for o in (data.get("opportunities") or [])
        if o.get("id")
    }


def _find_snapshot(portco_id: str, target_date: str) -> dict | None:
    """Return snapshot metadata for an exact date, or None."""
    for entry in list_snapshots(portco_id):
        if entry["snapshot_date"] == target_date:
            return entry
    return None


def bx_delta(
    portco_id: str,
    from_date: str = "",
    to_date: str = "",
) -> dict:
    """
    Compute the delta between two snapshots of a portco.

    If either `from_date` or `to_date` is empty, default:
      • from_date = oldest snapshot available
      • to_date   = newest snapshot available

    Args:
        portco_id: Target portco.
        from_date: ISO date of the prior snapshot (default: oldest).
        to_date:   ISO date of the later snapshot (default: newest).

    Returns:
        dict with from/to metadata, counts of closed/new/persistent opps,
        delta $ impact + pct-of-EBITDA delta.
    """
    snapshots = list_snapshots(portco_id)
    if len(snapshots) < 2:
        return {
            "portco_id": portco_id,
            "from_date": "",
            "to_date": "",
            "note": f"Need at least 2 snapshots to compute delta; "
            f"found {len(snapshots)}.",
        }

    from_meta = (
        _find_snapshot(portco_id, from_date) if from_date else snapshots[0]
    )
    to_meta = (
        _find_snapshot(portco_id, to_date) if to_date else snapshots[-1]
    )
    if from_meta is None:
        raise ToolError(f"No snapshot found for {portco_id!r} on {from_date!r}")
    if to_meta is None:
        raise ToolError(f"No snapshot found for {portco_id!r} on {to_date!r}")

    from_data = _load(from_meta["path"])
    to_data = _load(to_meta["path"])

    from_ids = _opp_ids(from_data)
    to_ids = _opp_ids(to_data)

    closed = from_ids - to_ids
    new = to_ids - from_ids
    persistent = from_ids & to_ids

    from_total = float(from_data.get("total_projected_impact_usd_annual") or 0.0)
    to_total = float(to_data.get("total_projected_impact_usd_annual") or 0.0)
    from_ebitda = float(from_data.get("ebitda_baseline_usd") or 0.0)
    to_ebitda = float(to_data.get("ebitda_baseline_usd") or 0.0)

    from_pct = (from_total / from_ebitda * 100.0) if from_ebitda else 0.0
    to_pct = (to_total / to_ebitda * 100.0) if to_ebitda else 0.0

    try:
        days = (
            date.fromisoformat(to_meta["snapshot_date"])
            - date.fromisoformat(from_meta["snapshot_date"])
        ).days
    except ValueError:
        days = 0

    return {
        "portco_id": portco_id,
        "from_date": from_meta["snapshot_date"],
        "to_date": to_meta["snapshot_date"],
        "days_elapsed": days,
        "opportunities_closed": len(closed),
        "opportunities_new": len(new),
        "opportunities_persistent": len(persistent),
        "closed_opp_ids": sorted(closed),
        "new_opp_ids": sorted(new),
        "from_total_usd_annual": round(from_total, 2),
        "to_total_usd_annual": round(to_total, 2),
        "delta_total_impact_usd": round(to_total - from_total, 2),
        "from_pct_of_ebitda": round(from_pct, 2),
        "to_pct_of_ebitda": round(to_pct, 2),
        "delta_pct_of_ebitda": round(to_pct - from_pct, 2),
    }
