"""
dx_counterfactual — What $ impact if we change the decision in this segment?

No ML. Pure pandas transformation: filter to the segment, apply the action
(throttle / cap / reroute / discontinue), re-aggregate, report the delta.

Claude calls this once per candidate opportunity to convert a statistical
pattern into an actionable $ number.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.dx.session import get_session


ActionType = Literal["throttle", "cap", "discontinue", "reroute", "custom"]


def _annualization_factor(df: pd.DataFrame, timestamp_col: str) -> float:
    if timestamp_col not in df.columns:
        return 1.0
    ts = pd.to_datetime(df[timestamp_col], errors="coerce", format="mixed")
    if not ts.notna().any():
        return 1.0
    months = max((ts.max() - ts.min()).days / 30.44, 1.0)
    return 12.0 / months


def _apply_throttle(
    seg_outcome: pd.Series, keep_pct: float
) -> tuple[pd.Series, int, int]:
    """Keep the first keep_pct fraction of rows; zero the rest.

    Returns: (projected_outcomes_for_segment, rows_kept, rows_removed)
    """
    n = len(seg_outcome)
    n_keep = max(0, int(round(n * keep_pct)))
    projected = seg_outcome.copy()
    if n_keep < n:
        # Sort by outcome descending so retained rows are the historically better
        # ones within the segment — this is a conservative upper bound on the
        # projected outcome (real-world throttle likely retains a random sample).
        order = seg_outcome.sort_values(ascending=False).index
        drop_idx = order[n_keep:]
        projected.loc[drop_idx] = 0.0
    return projected, n_keep, n - n_keep


def dx_counterfactual(
    session_id: str,
    segment_filter: dict,
    action: ActionType,
    action_params: dict | None = None,
) -> dict:
    """
    Project the $ impact of an alternative action for a segment.

    Supported actions:
      • throttle     — keep_pct=0.0..1.0.  Sort segment rows by outcome
                       descending, keep top keep_pct, drop the rest.
      • cap          — max_value=float. Cap per-row outcome at max_value
                       for rows in the segment (e.g., cap discount-driven
                       negative outcome).
      • discontinue  — fully drop the segment (equivalent to throttle keep_pct=0).
      • reroute      — outcome_replacement=float. Replace per-row outcome in
                       the segment with this constant (e.g., rerouting to a
                       better provider with known per-event margin).
      • custom       — outcome_replacement=float. Same as reroute; semantic
                       alias for Claude-directed custom scenarios.

    Args:
        session_id:      From dx_ingest.
        segment_filter:  {col: value, ...} equality filter.
        action:          One of the supported actions.
        action_params:   Action-specific parameters (see above).

    Returns:
        dict with current vs projected annualized outcome totals and delta.
    """
    session = get_session(session_id)
    df = session.joined

    if "_outcome_usd" not in df.columns:
        raise ToolError("Session dataframe lacks _outcome_usd. Re-run dx_ingest.")

    action_params = action_params or {}
    ann = _annualization_factor(df, session.template.timestamp_column)

    # Build segment mask
    mask = pd.Series(True, index=df.index)
    for col, value in segment_filter.items():
        if col not in df.columns:
            raise ToolError(f"Segment filter column '{col}' not in data.")
        mask &= df[col] == value
    seg_idx = df.index[mask]
    seg_outcome = df.loc[seg_idx, "_outcome_usd"].astype(float)

    if len(seg_outcome) == 0:
        return {
            "session_id": session_id,
            "segment": segment_filter,
            "action": action,
            "action_params": action_params,
            "current_outcome_usd_annual": 0.0,
            "projected_outcome_usd_annual": 0.0,
            "projected_impact_usd_annual": 0.0,
            "rows_affected": 0,
            "rows_retained": 0,
            "note": "Segment matched zero rows.",
        }

    current_total = float(seg_outcome.sum())
    rows_total = len(seg_outcome)

    if action == "throttle":
        keep_pct = float(action_params.get("keep_pct", 0.0))
        if not 0.0 <= keep_pct <= 1.0:
            raise ToolError("throttle requires keep_pct in [0.0, 1.0].")
        projected_seg, rows_kept, rows_removed = _apply_throttle(
            seg_outcome, keep_pct
        )
        projected_total = float(projected_seg.sum())
        rows_retained = rows_kept
        rows_affected = rows_removed
    elif action == "discontinue":
        projected_total = 0.0
        rows_retained = 0
        rows_affected = rows_total
    elif action == "cap":
        if "max_value" not in action_params:
            raise ToolError("cap requires action_params['max_value'].")
        max_val = float(action_params["max_value"])
        projected_seg = np.minimum(seg_outcome, max_val)
        projected_total = float(projected_seg.sum())
        rows_retained = rows_total
        rows_affected = int((seg_outcome > max_val).sum())
    elif action in ("reroute", "custom"):
        if "outcome_replacement" not in action_params:
            raise ToolError(
                f"{action} requires action_params['outcome_replacement']."
            )
        replacement = float(action_params["outcome_replacement"])
        projected_total = replacement * rows_total
        rows_retained = rows_total
        rows_affected = rows_total
    else:
        raise ToolError(f"Unsupported action: {action}")

    delta = projected_total - current_total
    return {
        "session_id": session_id,
        "segment": segment_filter,
        "action": action,
        "action_params": action_params,
        "current_outcome_usd_annual": round(current_total * ann, 2),
        "projected_outcome_usd_annual": round(projected_total * ann, 2),
        "projected_impact_usd_annual": round(delta * ann, 2),
        "rows_affected": int(rows_affected),
        "rows_retained": int(rows_retained),
    }
