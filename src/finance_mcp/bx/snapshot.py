"""
bx_snapshot — Stash a dated copy of an OpportunityMap for a portco.

Snapshots live on disk under finance_output/snapshots/<portco_id>/<iso_date>.json.
This is the only BX tool that writes to disk — everything else is in-memory.
Persistence is intentional here: you want to compare Q1 vs Q2 vs Q3 across
many sessions, so the snapshots need to survive MCP restart.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastmcp.exceptions import ToolError

from finance_mcp.output import ensure_output_dirs, SCRIPT_DIR


def _safe_id(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


def bx_snapshot(
    opportunity_map: dict,
    snapshot_date: str = "",
) -> dict:
    """
    Persist an OpportunityMap as a dated snapshot on disk.

    Args:
        opportunity_map: A valid OpportunityMap dict (as produced by dx_report
                         or the DX agent). Must contain portco_id.
        snapshot_date:   ISO date (YYYY-MM-DD). Defaults to today (UTC).

    Returns:
        dict with path, portco_id, snapshot_date, opportunity_count.
    """
    if not isinstance(opportunity_map, dict):
        raise ToolError("opportunity_map must be a dict.")
    portco_id = str(opportunity_map.get("portco_id") or "").strip()
    if not portco_id:
        raise ToolError("opportunity_map is missing portco_id.")

    date_str = (
        snapshot_date.strip()
        or datetime.now(timezone.utc).date().isoformat()
    )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise ToolError(
            f"snapshot_date must be YYYY-MM-DD, got {date_str!r}"
        )

    ensure_output_dirs()
    snapshots_dir = Path(SCRIPT_DIR) / "snapshots" / _safe_id(portco_id)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    out_path = snapshots_dir / f"{date_str}.json"

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(opportunity_map, f, indent=2, default=str)

    return {
        "path": str(out_path.resolve()),
        "portco_id": portco_id,
        "snapshot_date": date_str,
        "opportunity_count": len(opportunity_map.get("opportunities") or []),
    }


def list_snapshots(portco_id: str) -> list[dict]:
    """Return all snapshot metadata for a portco, oldest first."""
    snapshots_dir = Path(SCRIPT_DIR) / "snapshots" / _safe_id(portco_id)
    if not snapshots_dir.exists():
        return []
    out = []
    for p in sorted(snapshots_dir.glob("*.json")):
        out.append({"path": str(p.resolve()), "snapshot_date": p.stem})
    return out
